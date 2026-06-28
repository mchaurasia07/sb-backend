from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.container import app_container
from app.core.database import AsyncSessionLocal
from app.core.logger import get_logger

logger = get_logger("SCHEDULER-SUBSCRIPTION_EXPIRY")


class SubscriptionExpiryScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None

    def start(self) -> None:
        if not settings.SUBSCRIPTION_EXPIRY_SCHEDULER_ENABLED:
            logger.info("scheduler_disabled")
            return
        if self._task and not self._task.done():
            logger.info("scheduler_already_running")
            return
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task = asyncio.create_task(self._run_loop(), name="subscription-expiry-scheduler")
        logger.info(
            "scheduler_started",
            start_minute=settings.SUBSCRIPTION_EXPIRY_START_MINUTE,
            interval_minutes=settings.SUBSCRIPTION_EXPIRY_INTERVAL_MINUTES,
            limit=settings.SUBSCRIPTION_EXPIRY_LIMIT,
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
        self._lock = None
        logger.info("scheduler_stopped")

    async def _run_loop(self) -> None:
        if self._stop_event is None:
            return
        while not self._stop_event.is_set():
            delay_seconds = self._seconds_until_next_run()
            logger.info("next_run_scheduled", delay_seconds=round(delay_seconds, 2))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
            except TimeoutError:
                await self.run_once()

    async def run_once(self) -> None:
        if self._lock is None:
            return
        if self._lock.locked():
            logger.warning("overlap_skipped previous_run_still_active")
            return
        async with self._lock:
            try:
                async with AsyncSessionLocal() as session:
                    result = await app_container.request(session).subscription.expire_due_subscriptions(
                        limit=settings.SUBSCRIPTION_EXPIRY_LIMIT
                    )
                logger.info("run_completed", **result)
            except Exception as exc:
                logger.exception("run_failed", error=str(exc))

    @staticmethod
    def _seconds_until_next_run(now: datetime | None = None) -> float:
        current = now or datetime.now().astimezone()
        interval = min(1440, max(1, settings.SUBSCRIPTION_EXPIRY_INTERVAL_MINUTES))
        start_minute = min(59, max(0, settings.SUBSCRIPTION_EXPIRY_START_MINUTE))
        candidate = current.replace(minute=start_minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(minutes=interval)
        return max(0.0, (candidate - current).total_seconds())
