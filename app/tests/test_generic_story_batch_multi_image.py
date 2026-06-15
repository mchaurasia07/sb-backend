from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.entity.generic_story_workflow import GenericStoryWorkflowStatus, GenericStoryWorkflowStep
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.service.generic_story_batch_service import GenericStoryBatchService


def _text_part(text: str):
    return SimpleNamespace(text=text, inline_data=None)


def _image_part(data: bytes):
    return SimpleNamespace(text=None, inline_data=SimpleNamespace(data=data, mime_type="image/png"))


def _provider_job(*parts):
    return SimpleNamespace(
        dest=SimpleNamespace(
            inlined_responses=[
                SimpleNamespace(
                    metadata={"key": "pages_multi"},
                    error=None,
                    response=SimpleNamespace(parts=list(parts)),
                )
            ]
        )
    )


def _workflow():
    return SimpleNamespace(
        id=uuid4(),
        status=GenericStoryWorkflowStatus.IN_PROGRESS.value,
        current_step=GenericStoryWorkflowStep.IMAGE_GENERATION.value,
        error_message=None,
        story_json={
            "title": "Rakhi Day",
            "pages": [
                {"page_number": 1, "text": "Page one", "image_url": "old-1"},
                {"page_number": 2, "text": "Page two", "image_url": "old-2"},
            ],
        },
    )


def _job(workflow, *, continue_after_image_generation=False):
    items = [
        {
            "key": "page_1",
            "page_type": "story_page",
            "page_number": 1,
            "filename": "page_1.png",
            "aspect_ratio": "1:1",
            "source_image_prompt": "planned page 1",
        },
        {
            "key": "page_2",
            "page_type": "story_page",
            "page_number": 2,
            "filename": "page_2.png",
            "aspect_ratio": "1:1",
            "source_image_prompt": "planned page 2",
        },
    ]
    return SimpleNamespace(
        id=uuid4(),
        generic_story_id=None,
        workflow_id=workflow.id,
        job_type=StoryBatchJobType.IMAGE,
        status=StoryBatchJobStatus.SUBMITTED,
        provider_state="JOB_STATE_SUCCEEDED",
        request_keys=["page_1", "page_2"],
        request_payload={
            "mode": GenericStoryBatchService.WORKFLOW_MULTI_IMAGE_MODE,
            "items": items,
            "continue_after_image_generation": continue_after_image_generation,
        },
        expected_item_count=2,
        completed_item_count=0,
        failed_item_count=0,
        missing_keys=[],
        response_payload=None,
        error_message=None,
    )


def _service(workflow, monkeypatch):
    class FakeWorkflowRepo:
        async def get_by_id(self, workflow_id):
            assert workflow_id == workflow.id
            return workflow

        async def update(self, updated):
            return updated

    class FakeBatchRepo:
        async def update(self, updated):
            return updated

    class FakeStorage:
        async def save_story_image(self, story_id, image_bytes, filename, public_base_url):
            assert story_id == workflow.id
            return f"https://cdn.test/{story_id}/{filename}"

    class FakeSession:
        def __init__(self):
            self.commit_count = 0

        async def commit(self):
            self.commit_count += 1

    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    service.workflows = FakeWorkflowRepo()
    service.batch_jobs = FakeBatchRepo()
    service.image_storage = FakeStorage()
    service.session = FakeSession()
    monkeypatch.setattr(
        "app.service.generic_story_batch_service.StoryService._crop_image_bytes_to_aspect_ratio",
        staticmethod(lambda image_bytes, aspect_ratio: image_bytes),
    )
    return service


@pytest.mark.asyncio
async def test_list_batch_jobs_returns_paginated_jobs_with_filters():
    generic_story_id = uuid4()
    workflow_id = uuid4()
    job_id = uuid4()
    now = datetime.now(UTC)

    class FakeBatchRepo:
        def __init__(self):
            self.kwargs = None

        async def list_paginated(self, **kwargs):
            self.kwargs = kwargs
            return [
                SimpleNamespace(
                    id=job_id,
                    generic_story_id=generic_story_id,
                    workflow_id=workflow_id,
                    job_type=StoryBatchJobType.IMAGE,
                    status=StoryBatchJobStatus.RUNNING,
                    provider="openai",
                    provider_job_name="batch_123",
                    provider_model="gpt-image-1",
                    provider_state="in_progress",
                    attempt=2,
                    expected_item_count=4,
                    completed_item_count=1,
                    failed_item_count=0,
                    request_keys=["page_1", "page_2"],
                    missing_keys=["page_2"],
                    error_message=None,
                    created_at=now,
                    updated_at=now,
                )
            ], 3

    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    service.batch_jobs = FakeBatchRepo()

    response = await service.list_batch_jobs(
        page=2,
        page_size=1,
        generic_story_id=generic_story_id,
        workflow_id=workflow_id,
        status_filter=StoryBatchJobStatus.RUNNING,
        job_type=StoryBatchJobType.IMAGE,
        provider="openai",
    )

    assert service.batch_jobs.kwargs == {
        "page": 2,
        "page_size": 1,
        "generic_story_id": generic_story_id,
        "workflow_id": workflow_id,
        "status": StoryBatchJobStatus.RUNNING,
        "job_type": StoryBatchJobType.IMAGE,
        "provider": "openai",
    }
    assert response.total == 3
    assert response.page == 2
    assert response.page_size == 1
    assert response.total_pages == 3
    assert response.items[0].id == job_id
    assert response.items[0].job_type == "IMAGE"
    assert response.items[0].status == "RUNNING"
    assert response.items[0].request_keys == ["page_1", "page_2"]


@pytest.mark.asyncio
async def test_workflow_multi_image_reconcile_updates_pages_and_continues(monkeypatch):
    workflow = _workflow()
    job = _job(workflow, continue_after_image_generation=True)
    service = _service(workflow, monkeypatch)
    continued = []

    async def _continue(updated_workflow, request_payload):
        continued.append((updated_workflow.id, request_payload))

    service._continue_workflow_after_multi_image_generation = _continue

    await service._process_reconciled_workflow_multi_image_job(
        job,
        _provider_job(
            _text_part("IMAGE_ITEM: page_1"),
            _image_part(b"one"),
            _text_part("IMAGE_ITEM: page_2"),
            _image_part(b"two"),
        ),
    )

    assert job.status == StoryBatchJobStatus.SUCCEEDED
    assert job.completed_item_count == 2
    assert job.failed_item_count == 0
    assert job.missing_keys == []
    assert workflow.story_json["pages"][0]["image_url"].endswith("/page_1.png")
    assert workflow.story_json["pages"][0]["image_prompt"] == "planned page 1"
    assert workflow.story_json["pages"][1]["planned_image_prompt"] == "planned page 2"
    assert workflow.status == GenericStoryWorkflowStatus.IN_PROGRESS.value
    assert workflow.current_step == GenericStoryWorkflowStep.IMAGE_GENERATION.value
    assert continued and continued[0][0] == workflow.id


@pytest.mark.asyncio
async def test_workflow_multi_image_reconcile_fails_on_count_mismatch(monkeypatch):
    workflow = _workflow()
    job = _job(workflow)
    service = _service(workflow, monkeypatch)

    await service._process_reconciled_workflow_multi_image_job(
        job,
        _provider_job(_text_part("IMAGE_ITEM: page_1"), _image_part(b"one")),
    )

    assert job.status == StoryBatchJobStatus.FAILED
    assert job.completed_item_count == 0
    assert set(job.missing_keys) == {"page_1", "page_2"}
    assert "Gemini returned 1 images; expected 2." == job.error_message
    assert workflow.status == GenericStoryWorkflowStatus.FAILED.value
    assert workflow.current_step == GenericStoryWorkflowStep.IMAGE_GENERATION.value


@pytest.mark.asyncio
async def test_workflow_multi_image_reconcile_fails_on_marker_mismatch_before_saving(monkeypatch):
    workflow = _workflow()
    job = _job(workflow)
    service = _service(workflow, monkeypatch)
    saved = []

    async def _save(story_id, image_bytes, filename, public_base_url):
        saved.append(filename)
        return f"https://cdn.test/{filename}"

    service.image_storage.save_story_image = _save

    await service._process_reconciled_workflow_multi_image_job(
        job,
        _provider_job(
            _text_part("IMAGE_ITEM: page_2"),
            _image_part(b"two"),
            _text_part("IMAGE_ITEM: page_1"),
            _image_part(b"one"),
        ),
    )

    assert job.status == StoryBatchJobStatus.FAILED
    assert job.response_payload["status"] == "marker_mismatch"
    assert workflow.story_json["pages"][0]["image_url"] == "old-1"
    assert workflow.current_step == GenericStoryWorkflowStep.IMAGE_GENERATION.value
    assert saved == []
