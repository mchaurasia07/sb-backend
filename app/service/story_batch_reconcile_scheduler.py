"""Application-local scheduler for delayed story batch reconciliation."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logger import get_logger
from app.service.story_service_batch_service import StoryServiceBatchService

logger = get_logger(__name__)


class StoryBatchReconcileScheduler:
    """Runs story batch reconciliation on a fixed interval in this app process."""

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
            interval_seconds=settings.STORY_BATCH_RECONCILE_INTERVAL_SECONDS,
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

        interval_seconds = max(60, settings.STORY_BATCH_RECONCILE_INTERVAL_SECONDS)
        while not self._stop_event.is_set():
            await self._run_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    async def _run_once(self) -> None:
        if self._run_lock is None:
            return
        if self._run_lock.locked():
            logger.warning("story_batch_reconcile_scheduler_overlap_skipped")
            return

        async with self._run_lock:
            try:
                async with AsyncSessionLocal() as session:
                    result = await StoryServiceBatchService(session).reconcile_batch_jobs(
                        limit=settings.STORY_BATCH_RECONCILE_LIMIT
                    )
                logger.info(
                    "story_batch_reconcile_scheduler_run_completed",
                    checked_count=result.get("checked_count"),
                    processed_count=result.get("processed_count"),
                )
            except Exception as exc:
                logger.exception("story_batch_reconcile_scheduler_run_failed", error=str(exc))
