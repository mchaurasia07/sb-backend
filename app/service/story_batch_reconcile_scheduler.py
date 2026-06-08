"""Application-local scheduler for delayed story batch reconciliation."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logger import get_logger
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.generic_story_batch_service import GenericStoryBatchService
from app.service.story_service_batch_service import StoryServiceBatchService

logger = get_logger(__name__)


class StoryBatchReconcileScheduler:
    """Runs story batch reconciliation hourly at a configured minute."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._run_lock: asyncio.Lock | None = None

    def start(self) -> None:
        if not settings.STORY_BATCH_RECONCILE_SCHEDULER_ENABLED:
            logger.info("story_batch_reconcile_scheduler_disabled")
            return
        if self._task and not self._task.done():
            logger.info("story_batch_reconcile_scheduler_already_running")
            return

        self._stop_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._task = asyncio.create_task(self._run_loop(), name="story-batch-reconcile-scheduler")
        logger.info(
            "story_batch_reconcile_scheduler_started",
            run_minute=settings.STORY_BATCH_RECONCILE_RUN_MINUTE,
            limit=settings.STORY_BATCH_RECONCILE_LIMIT,
        )

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._stop_event = None
        self._run_lock = None
        logger.info("story_batch_reconcile_scheduler_stopped")

    async def _run_loop(self) -> None:
        if self._stop_event is None:
            return

        while not self._stop_event.is_set():
            delay_seconds = self._seconds_until_next_run()
            logger.info(
                "story_batch_reconcile_scheduler_next_run_scheduled",
                delay_seconds=round(delay_seconds, 2),
                run_minute=settings.STORY_BATCH_RECONCILE_RUN_MINUTE,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
            except TimeoutError:
                await self._run_once()

    @staticmethod
    def _seconds_until_next_run(now: datetime | None = None) -> float:
        current = now or datetime.now().astimezone()
        run_minute = min(59, max(0, settings.STORY_BATCH_RECONCILE_RUN_MINUTE))
        next_run = current.replace(minute=run_minute, second=0, microsecond=0)
        if next_run <= current:
            next_run += timedelta(hours=1)
        return max(0.0, (next_run - current).total_seconds())

    async def _run_once(self) -> None:
        if self._run_lock is None:
            return
        if self._run_lock.locked():
            logger.warning("story_batch_reconcile_scheduler_overlap_skipped")
            return

        async with self._run_lock:
            try:
                async with AsyncSessionLocal() as session:
                    story_result = await StoryServiceBatchService(session).reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                    custom_story_result = await CustomStoryWorkflowService(session).reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                    generic_result = await GenericStoryBatchService(session).reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                logger.info(
                    "story_batch_reconcile_scheduler_run_completed",
                    checked_count=story_result.get("checked_count"),
                    processed_count=story_result.get("processed_count"),
                    custom_story_checked_count=custom_story_result.get("checked_count"),
                    custom_story_processed_count=custom_story_result.get("processed_count"),
                    generic_checked_count=generic_result.get("checked_count"),
                    generic_processed_count=generic_result.get("processed_count"),
                )
            except Exception as exc:
                logger.exception("story_batch_reconcile_scheduler_run_failed", error=str(exc))
