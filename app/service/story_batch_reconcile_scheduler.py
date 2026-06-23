"""Application-local schedulers for delayed story workflow processing."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.container import app_container
from app.core.database import AsyncSessionLocal
from app.core.logger import get_logger

logger = get_logger(__name__)
reconcile_logger = get_logger("SCHEDULER-RECONCILE")
event_process_logger = get_logger("SCHEDULER-EVENT_PROCESS")


class StoryBatchReconcileScheduler:
    """Runs delayed story batch reconciliation and workflow event processing."""

    def __init__(self) -> None:
        self._reconcile_task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._reconcile_lock: asyncio.Lock | None = None
        self._event_lock: asyncio.Lock | None = None

    def start(self) -> None:
        if not (
            settings.STORY_BATCH_RECONCILE_SCHEDULER_ENABLED
            or settings.CUSTOM_WORKFLOW_EVENT_SCHEDULER_ENABLED
        ):
            logger.info("story_workflow_schedulers_disabled")
            return
        if (
            (self._reconcile_task and not self._reconcile_task.done())
            or (self._event_task and not self._event_task.done())
        ):
            logger.info("story_workflow_schedulers_already_running")
            return

        self._stop_event = asyncio.Event()
        if settings.STORY_BATCH_RECONCILE_SCHEDULER_ENABLED:
            self._reconcile_lock = asyncio.Lock()
            self._reconcile_task = asyncio.create_task(
                self._run_reconcile_loop(),
                name="story-batch-reconcile-scheduler",
            )
            reconcile_logger.info(
                "scheduler_started",
                start_minute=settings.STORY_BATCH_RECONCILE_START_MINUTE,
                interval_minutes=settings.STORY_BATCH_RECONCILE_INTERVAL_MINUTES,
                limit=settings.STORY_BATCH_RECONCILE_LIMIT,
            )
        else:
            reconcile_logger.info("scheduler_disabled")

        if settings.CUSTOM_WORKFLOW_EVENT_SCHEDULER_ENABLED:
            self._event_lock = asyncio.Lock()
            self._event_task = asyncio.create_task(
                self._run_event_loop(),
                name="custom-workflow-event-scheduler",
            )
            event_process_logger.info(
                "scheduler_started",
                start_minute=settings.CUSTOM_WORKFLOW_EVENT_START_MINUTE,
                interval_minutes=settings.CUSTOM_WORKFLOW_EVENT_INTERVAL_MINUTES,
                limit=settings.CUSTOM_WORKFLOW_EVENT_PROCESS_LIMIT,
            )
        else:
            event_process_logger.info("scheduler_disabled")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        for task in (self._reconcile_task, self._event_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._reconcile_task = None
        self._event_task = None
        self._stop_event = None
        self._reconcile_lock = None
        self._event_lock = None
        logger.info("story_workflow_schedulers_stopped")

    async def _run_reconcile_loop(self) -> None:
        if self._stop_event is None:
            return

        while not self._stop_event.is_set():
            delay_seconds = self._seconds_until_next_reconcile_run()
            reconcile_logger.info(
                "next_run_scheduled",
                delay_seconds=round(delay_seconds, 2),
                start_minute=settings.STORY_BATCH_RECONCILE_START_MINUTE,
                interval_minutes=settings.STORY_BATCH_RECONCILE_INTERVAL_MINUTES,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
            except TimeoutError:
                await self._run_reconcile_once()

    async def _run_event_loop(self) -> None:
        if self._stop_event is None:
            return

        while not self._stop_event.is_set():
            delay_seconds = self._seconds_until_next_event_run()
            event_process_logger.info(
                "next_run_scheduled",
                delay_seconds=round(delay_seconds, 2),
                start_minute=settings.CUSTOM_WORKFLOW_EVENT_START_MINUTE,
                interval_minutes=settings.CUSTOM_WORKFLOW_EVENT_INTERVAL_MINUTES,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
            except TimeoutError:
                await self._run_events_once()

    @staticmethod
    def _seconds_until_next_reconcile_run(now: datetime | None = None) -> float:
        return StoryBatchReconcileScheduler._seconds_until_next_interval_run(
            now=now,
            start_minute=settings.STORY_BATCH_RECONCILE_START_MINUTE,
            interval_minutes=settings.STORY_BATCH_RECONCILE_INTERVAL_MINUTES,
        )

    @staticmethod
    def _seconds_until_next_event_run(now: datetime | None = None) -> float:
        return StoryBatchReconcileScheduler._seconds_until_next_interval_run(
            now=now,
            start_minute=settings.CUSTOM_WORKFLOW_EVENT_START_MINUTE,
            interval_minutes=settings.CUSTOM_WORKFLOW_EVENT_INTERVAL_MINUTES,
        )

    @staticmethod
    def _seconds_until_next_interval_run(
        *,
        now: datetime | None = None,
        start_minute: int,
        interval_minutes: int,
    ) -> float:
        current = now or datetime.now().astimezone()
        interval = min(60, max(1, interval_minutes))
        first_minute = min(59, max(0, start_minute))
        candidate = current.replace(minute=first_minute, second=0, microsecond=0)
        if candidate <= current:
            minutes_since_first = int((current - candidate).total_seconds() // 60)
            intervals_elapsed = (minutes_since_first // interval) + 1
            candidate += timedelta(minutes=intervals_elapsed * interval)
        while candidate.minute >= 60:
            candidate -= timedelta(minutes=60)
            candidate += timedelta(hours=1)
        if candidate <= current:
            candidate += timedelta(minutes=interval)
        next_run = candidate
        if next_run.hour == current.hour and next_run.minute < first_minute:
            next_run += timedelta(hours=1)
        return max(0.0, (next_run - current).total_seconds())

    async def _run_reconcile_once(self) -> None:
        if self._reconcile_lock is None:
            return
        if self._reconcile_lock.locked():
            reconcile_logger.warning("overlap_skipped previous_run_still_active")
            return

        async with self._reconcile_lock:
            try:
                reconcile_logger.info(
                    "run_started",
                    limit=settings.STORY_BATCH_RECONCILE_LIMIT,
                )
                async with AsyncSessionLocal() as session:
                    container = app_container.request(session)
                    story_result = await container.story_batch.reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                    custom_story_result = await container.custom_story_workflow.reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                reconcile_logger.info(
                    "run_completed",
                    checked_count=story_result.get("checked_count"),
                    processed_count=story_result.get("processed_count"),
                    workflow_checked_count=custom_story_result.get("checked_count"),
                    workflow_processed_count=custom_story_result.get("processed_count"),
                )
            except Exception as exc:
                reconcile_logger.exception("run_failed", error=str(exc))

    async def _run_events_once(self) -> None:
        if self._event_lock is None:
            return
        if self._event_lock.locked():
            event_process_logger.warning("overlap_skipped previous_run_still_active")
            return

        async with self._event_lock:
            try:
                event_process_logger.info(
                    "run_started",
                    limit=settings.CUSTOM_WORKFLOW_EVENT_PROCESS_LIMIT,
                )
                async with AsyncSessionLocal() as session:
                    result = await app_container.request(session).custom_story_workflow.process_events(
                        limit=settings.CUSTOM_WORKFLOW_EVENT_PROCESS_LIMIT
                    )
                event_process_logger.info(
                    "run_completed",
                    checked_count=result.get("checked_count"),
                    processed_count=result.get("processed_count"),
                )
            except Exception as exc:
                event_process_logger.exception("run_failed", error=str(exc))
