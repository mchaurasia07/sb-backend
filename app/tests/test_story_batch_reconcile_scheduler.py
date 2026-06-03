from datetime import UTC, datetime

from app.core.config import settings
from app.service.story_batch_reconcile_scheduler import StoryBatchReconcileScheduler


def test_reconcile_scheduler_waits_until_minute_one(monkeypatch):
    monkeypatch.setattr(settings, "STORY_BATCH_RECONCILE_RUN_MINUTE", 1)

    delay = StoryBatchReconcileScheduler._seconds_until_next_run(
        datetime(2026, 6, 3, 10, 0, 30, tzinfo=UTC)
    )

    assert delay == 30


def test_reconcile_scheduler_rolls_to_next_hour_after_minute_one(monkeypatch):
    monkeypatch.setattr(settings, "STORY_BATCH_RECONCILE_RUN_MINUTE", 1)

    delay = StoryBatchReconcileScheduler._seconds_until_next_run(
        datetime(2026, 6, 3, 10, 1, 0, tzinfo=UTC)
    )

    assert delay == 3600
