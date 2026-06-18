import asyncio
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.service import story_batch_reconcile_scheduler as scheduler_module
from app.service.story_batch_reconcile_scheduler import StoryBatchReconcileScheduler


def test_reconcile_scheduler_runs_every_15_minutes_from_hour(monkeypatch):
    monkeypatch.setattr(settings, "STORY_BATCH_RECONCILE_START_MINUTE", 0)
    monkeypatch.setattr(settings, "STORY_BATCH_RECONCILE_INTERVAL_MINUTES", 15)

    assert (
        StoryBatchReconcileScheduler._seconds_until_next_reconcile_run(
            datetime(2026, 6, 3, 10, 0, 30, tzinfo=UTC)
        )
        == 870
    )
    assert (
        StoryBatchReconcileScheduler._seconds_until_next_reconcile_run(
            datetime(2026, 6, 3, 10, 15, 0, tzinfo=UTC)
        )
        == 900
    )
    assert (
        StoryBatchReconcileScheduler._seconds_until_next_reconcile_run(
            datetime(2026, 6, 3, 10, 59, 30, tzinfo=UTC)
        )
        == 30
    )


def test_event_scheduler_runs_every_5_minutes_from_minute_four(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_WORKFLOW_EVENT_START_MINUTE", 4)
    monkeypatch.setattr(settings, "CUSTOM_WORKFLOW_EVENT_INTERVAL_MINUTES", 5)

    assert (
        StoryBatchReconcileScheduler._seconds_until_next_event_run(
            datetime(2026, 6, 3, 10, 3, 30, tzinfo=UTC)
        )
        == 30
    )
    assert (
        StoryBatchReconcileScheduler._seconds_until_next_event_run(
            datetime(2026, 6, 3, 10, 4, 0, tzinfo=UTC)
        )
        == 300
    )
    assert (
        StoryBatchReconcileScheduler._seconds_until_next_event_run(
            datetime(2026, 6, 3, 10, 59, 30, tzinfo=UTC)
        )
        == 270
    )


class _FakeSessionContext:
    async def __aenter__(self):
        return "session"

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_reconcile_scheduler_calls_story_and_workflow_services(monkeypatch):
    calls = []

    class FakeStoryBatchService:
        def __init__(self, session):
            calls.append(("story_session", session))

        async def reconcile_batch_jobs(self, *, limit):
            calls.append(("story_reconcile", limit))
            return {"checked_count": 1, "processed_count": 1}

    class FakeWorkflowService:
        def __init__(self, session):
            calls.append(("workflow_session", session))

        async def reconcile_batch_jobs(self, *, limit):
            calls.append(("workflow_reconcile", limit))
            return {"checked_count": 2, "processed_count": 1}

    monkeypatch.setattr(settings, "STORY_BATCH_RECONCILE_LIMIT", 25)
    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", _FakeSessionContext)
    monkeypatch.setattr(scheduler_module, "StoryServiceBatchService", FakeStoryBatchService)
    monkeypatch.setattr(scheduler_module, "CustomStoryWorkflowService", FakeWorkflowService)

    scheduler = StoryBatchReconcileScheduler()
    scheduler._reconcile_lock = asyncio.Lock()

    await scheduler._run_reconcile_once()

    assert calls == [
        ("story_session", "session"),
        ("story_reconcile", 25),
        ("workflow_session", "session"),
        ("workflow_reconcile", 25),
    ]


@pytest.mark.asyncio
async def test_event_scheduler_calls_workflow_event_processor(monkeypatch):
    calls = []

    class FakeWorkflowService:
        def __init__(self, session):
            calls.append(("workflow_session", session))

        async def process_events(self, *, limit):
            calls.append(("process_events", limit))
            return {"checked_count": 3, "processed_count": 2}

    monkeypatch.setattr(settings, "CUSTOM_WORKFLOW_EVENT_PROCESS_LIMIT", 10)
    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", _FakeSessionContext)
    monkeypatch.setattr(scheduler_module, "CustomStoryWorkflowService", FakeWorkflowService)

    scheduler = StoryBatchReconcileScheduler()
    scheduler._event_lock = asyncio.Lock()

    await scheduler._run_events_once()

    assert calls == [
        ("workflow_session", "session"),
        ("process_events", 10),
    ]


@pytest.mark.asyncio
async def test_scheduler_locks_skip_overlapping_runs(monkeypatch):
    class FailingService:
        def __init__(self, session):
            raise AssertionError("service should not be called while lock is held")

    monkeypatch.setattr(scheduler_module, "StoryServiceBatchService", FailingService)
    monkeypatch.setattr(scheduler_module, "CustomStoryWorkflowService", FailingService)

    scheduler = StoryBatchReconcileScheduler()
    scheduler._reconcile_lock = asyncio.Lock()
    scheduler._event_lock = asyncio.Lock()

    await scheduler._reconcile_lock.acquire()
    await scheduler._event_lock.acquire()
    try:
        await scheduler._run_reconcile_once()
        await scheduler._run_events_once()
    finally:
        scheduler._reconcile_lock.release()
        scheduler._event_lock.release()
