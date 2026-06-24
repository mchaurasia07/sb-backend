import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.responses import Response
from pydantic import ValidationError

from app.core.exceptions import AppException
from app.entity.custom_story_workflow import (
    CustomStoryWorkflowEntity,
    CustomStoryWorkflowEventStatus,
    CustomStoryWorkflowStatus,
    CustomStoryWorkflowStep,
    CustomStoryWorkflowType,
)
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus
from app.model.request.story import ReaderCategory, StoryGenerationRequest, age_group_for_reader_category
from app.model.response.custom_story_workflow import CustomStoryWorkflowResponse
from app.repository.custom_story_workflow_repository import (
    CustomStoryBatchJobRepository,
    CustomStoryWorkflowEventRepository,
    CustomStoryWorkflowRepository,
)
from app.routes.v1 import stories as story_routes
from app.routes.v1 import workflows as workflow_routes
from app.service import custom_story_workflow_service
from app.service.custom_story_workflow_service import CustomStoryWorkflowService
from app.service.image_plan_validator import ImagePlanValidator
from app.service.story_input_safety_service import StoryInputSafetyInspection, StoryInputSafetyResult
from app.service.story_service import StoryService
from app.service.workflow_service import WorkflowService


def _workflow(**overrides):
    data = {
        "id": uuid4(),
        "user_id": uuid4(),
        "child_id": uuid4(),
        "story_id": None,
        "request_number": 1,
        "generation_mode": "INPUT_DRIVEN",
        "processing_mode": "instant",
        "age_group": SimpleNamespace(value="3-6"),
        "category": "adventure",
        "learning_goal": "listening",
        "context": "moon bell",
        "event_description": None,
        "reader_category": "Early Reader",
        "use_child_character": False,
        "execute_image": True,
        "execute_narration": True,
        "skip_validation": False,
        "execute_workflow": False,
        "status": CustomStoryWorkflowStatus.PENDING,
        "current_step": None,
        "error_message": None,
        "story_plan_json": None,
        "story_plan_validated": False,
        "story_json": None,
        "image_plan_json": None,
        "image_plan_validated": False,
        "title": None,
        "summary": None,
        "moral": None,
        "ai_provider": "google",
        "text_model": "gemini",
        "image_model": "imagen",
        "reference_image_model": "gemini",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class _StepRepo:
    def __init__(self):
        self.items = []

    async def latest_for_workflow_step(self, workflow_id, step_name):
        value = step_name.value if hasattr(step_name, "value") else str(step_name)
        for step in reversed(self.items):
            step_value = step.step_name.value if hasattr(step.step_name, "value") else str(step.step_name)
            if step.workflow_id == workflow_id and step_value == value:
                return step
        return None

    async def create(self, workflow_id, step_name):
        step = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow_id,
            step_name=step_name,
            status=StepStatus.PENDING,
            started_at=None,
            completed_at=None,
            input_json=None,
            prompt=None,
            output_json=None,
            error_message=None,
            retry_count=0,
            created_at=datetime.now(UTC),
        )
        self.items.append(step)
        return step

    async def update(self, step):
        return step


class _BatchRepo:
    def __init__(self):
        self.by_type = {}

    async def latest_for_workflow_type(self, workflow_id, job_type):
        _ = workflow_id
        return self.by_type.get(job_type)

    def add(self, workflow_id, job_type, *, status=StoryBatchJobStatus.SUBMITTED):
        job = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow_id,
            story_id=None,
            job_type=job_type,
            status=status,
            provider_job_name=f"batches/{job_type.value.lower()}",
            provider_state="JOB_STATE_PENDING",
            error_message=None,
            request_keys=["page_1"],
            response_payload=None,
        )
        self.by_type[job_type] = job
        return job


@pytest.mark.asyncio
async def test_get_events_returns_any_owned_workflow_events_newest_first():
    user_id = uuid4()
    workflow_id = uuid4()
    older_event_id = uuid4()
    newer_event_id = uuid4()
    created_at_older = datetime(2026, 6, 19, 10, 0, tzinfo=UTC)
    created_at_newer = datetime(2026, 6, 19, 10, 5, tzinfo=UTC)
    workflow = _workflow(
        id=workflow_id,
        user_id=user_id,
        story_type=CustomStoryWorkflowType.GENERIC,
    )
    events = [
        SimpleNamespace(
            id=newer_event_id,
            workflow_id=workflow_id,
            story_type=CustomStoryWorkflowType.GENERIC,
            step_name=CustomStoryWorkflowStep.STORY_GENERATION,
            status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
            retry_count=1,
            retry_flag=True,
            retry_comment="FULL_BATCH_RETRY",
            retry_source_event_id=older_event_id,
            metadata_json={"batch_job_id": "batch-2"},
            error_message=None,
            locked_at=None,
            completed_at=None,
            created_at=created_at_newer,
            updated_at=created_at_newer,
        ),
        SimpleNamespace(
            id=older_event_id,
            workflow_id=workflow_id,
            story_type=CustomStoryWorkflowType.GENERIC,
            step_name=CustomStoryWorkflowStep.STORY_GENERATION,
            status=CustomStoryWorkflowEventStatus.FAILED,
            retry_count=0,
            retry_flag=False,
            retry_comment=None,
            retry_source_event_id=None,
            metadata_json={"batch_job_id": "batch-1"},
            error_message="Invalid JSON",
            locked_at=None,
            completed_at=created_at_older,
            created_at=created_at_older,
            updated_at=created_at_older,
        ),
    ]

    async def _get_for_user(_user_id, _workflow_id):
        assert _user_id == user_id
        assert _workflow_id == workflow_id
        return workflow

    async def _list_by_workflow_desc(_workflow_id, *, story_type=None):
        assert _workflow_id == workflow_id
        assert story_type is None
        return events

    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.workflows = SimpleNamespace(get_for_user=_get_for_user)
    service.events = SimpleNamespace(list_by_workflow_desc=_list_by_workflow_desc)

    response = await service.get_events(user_id, workflow_id)

    assert [item.id for item in response] == [newer_event_id, older_event_id]
    assert response[0].story_type == "GENERIC"
    assert response[0].retry_flag is True
    assert response[0].retry_comment == "FULL_BATCH_RETRY"
    assert response[0].metadata == {"batch_job_id": "batch-2"}
    assert response[1].error_message == "Invalid JSON"


@pytest.mark.asyncio
async def test_retry_accepts_generic_workflow_and_enqueues_next_step():
    user_id = uuid4()
    workflow = _workflow(
        user_id=user_id,
        child_id=None,
        story_type=CustomStoryWorkflowType.GENERIC,
        generic_story_id=uuid4(),
        status=CustomStoryWorkflowStatus.PENDING,
        language="en",
        languages=["en"],
    )
    updated_workflows = []
    created_events = []
    commits = []

    async def _get_for_update(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow.id
        return workflow

    async def _update_workflow(workflow_arg):
        updated_workflows.append(workflow_arg)
        return workflow_arg

    async def _create_event(**kwargs):
        created_events.append(kwargs)

    async def _commit():
        commits.append(True)

    async def _first_incomplete_step(_workflow):
        return CustomStoryWorkflowStep.STORY_GENERATION

    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.workflows = SimpleNamespace(get_for_user_for_update=_get_for_update, update=_update_workflow)
    service.events = SimpleNamespace(create_if_absent=_create_event)
    service.session = SimpleNamespace(commit=_commit)
    service._first_incomplete_step = _first_incomplete_step

    response = await service.retry(user_id, workflow.id)

    assert response.story_type == "GENERIC"
    assert response.generic_story_id == workflow.generic_story_id
    assert workflow.status == CustomStoryWorkflowStatus.PENDING
    assert workflow.error_message is None
    assert workflow.current_step == CustomStoryWorkflowStep.STORY_GENERATION.value
    assert updated_workflows == [workflow]
    assert created_events == [
        {
            "workflow_id": workflow.id,
            "step_name": CustomStoryWorkflowStep.STORY_GENERATION,
            "retry_count": 1,
            "metadata_json": {"source": "manual_retry"},
        }
    ]
    assert commits == [True]


@pytest.mark.asyncio
async def test_workflows_router_lists_shared_workflows_with_filters():
    user_id = uuid4()
    workflow_id = uuid4()
    data = {"items": [], "total": 0, "page": 2, "page_size": 10, "total_pages": 0}
    calls = {}

    class _WorkflowService:
        async def list_workflows(
            self,
            requested_user_id,
            *,
            page,
            page_size,
            workflow_id=None,
            workflow_type=None,
        ):
            calls["user_id"] = requested_user_id
            calls["page"] = page
            calls["page_size"] = page_size
            calls["workflow_id"] = workflow_id
            calls["workflow_type"] = workflow_type
            return data

    route = workflow_routes.WorkflowsRouter().list_workflows
    response = await route(
        page=2,
        page_size=10,
        workflow_id=workflow_id,
        workflow_type=CustomStoryWorkflowType.GENERIC,
        current_user=SimpleNamespace(id=user_id),
        container=SimpleNamespace(workflow_service=_WorkflowService()),
    )

    assert response.data == data
    assert response.message == "Workflows retrieved successfully"
    assert calls == {
        "user_id": user_id,
        "page": 2,
        "page_size": 10,
        "workflow_id": workflow_id,
        "workflow_type": CustomStoryWorkflowType.GENERIC,
    }


@pytest.mark.asyncio
async def test_workflow_service_lists_workflows_with_repository_filters():
    user_id = uuid4()
    workflow_id = uuid4()
    workflow = _workflow(
        id=workflow_id,
        user_id=user_id,
        story_type=CustomStoryWorkflowType.GENERIC,
        generic_story_id=uuid4(),
    )
    calls = {}

    class _WorkflowRepository:
        async def list_workflows(
            self,
            requested_user_id,
            *,
            page,
            page_size,
            workflow_id=None,
            workflow_type=None,
        ):
            calls["user_id"] = requested_user_id
            calls["page"] = page
            calls["page_size"] = page_size
            calls["workflow_id"] = workflow_id
            calls["workflow_type"] = workflow_type
            return [workflow], 1

    service = WorkflowService.__new__(WorkflowService)
    service.workflow_repo = _WorkflowRepository()

    response = await service.list_workflows(
        user_id,
        page=3,
        page_size=5,
        workflow_id=workflow_id,
        workflow_type=CustomStoryWorkflowType.GENERIC,
    )

    assert calls == {
        "user_id": user_id,
        "page": 3,
        "page_size": 5,
        "workflow_id": workflow_id,
        "workflow_type": CustomStoryWorkflowType.GENERIC,
    }
    assert response.total == 1
    assert response.page == 3
    assert response.page_size == 5
    assert response.items[0].workflow_id == workflow_id
    assert response.items[0].story_type == "GENERIC"


@pytest.mark.asyncio
async def test_event_repository_stores_workflow_story_type_on_create():
    workflow_id = uuid4()
    session = SimpleNamespace(added=[], flushed=False)

    async def _scalar(statement):
        return CustomStoryWorkflowType.GENERIC

    def _add(event):
        session.added.append(event)

    async def _flush():
        session.flushed = True

    session.scalar = _scalar
    session.add = _add
    session.flush = _flush

    event = await CustomStoryWorkflowEventRepository(session).create(
        workflow_id=workflow_id,
        step_name=CustomStoryWorkflowStep.STORY_GENERATION,
    )

    assert event.workflow_id == workflow_id
    assert event.story_type == CustomStoryWorkflowType.GENERIC
    assert session.added == [event]
    assert session.flushed is True


def _step_by_name(steps: _StepRepo, step_name: CustomStoryWorkflowStep):
    value = step_name.value
    for step in reversed(steps.items):
        step_value = step.step_name.value if hasattr(step.step_name, "value") else str(step.step_name)
        if step_value == value:
            return step
    return None


def _batch_job(**overrides):
    data = {
        "id": uuid4(),
        "workflow_id": uuid4(),
        "story_id": uuid4(),
        "job_type": StoryBatchJobType.IMAGE,
        "status": StoryBatchJobStatus.SUBMITTED,
        "provider": "google",
        "provider_job_name": "batches/image",
        "provider_model": "gemini-image",
        "provider_state": "JOB_STATE_RUNNING",
        "attempt": 1,
        "expected_item_count": 10,
        "completed_item_count": 3,
        "failed_item_count": 1,
        "request_keys": ["cover", "page_1"],
        "missing_keys": ["page_1"],
        "error_message": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_copy_character_references_to_final_story_storage_updates_manifest_and_visual_bible(monkeypatch):
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    final_story_id = uuid4()
    calls = []

    class _Storage:
        async def delete_story_directory(self, story_id):
            raise AssertionError("reference copy must not delete story directories")

    async def _copy_image_url(image_storage, *, image_url, story_id, filename):
        calls.append((image_storage, image_url, story_id, filename))
        return f"https://cdn.test/stories/{story_id}/{filename.replace('.png', '.webp')}"

    monkeypatch.setattr(custom_story_workflow_service, "get_image_storage_service", lambda: _Storage())
    service._copy_story_image_url = _copy_image_url
    image_plan = {
        "visual_bible": {
            "hero": {
                "character_id": "ria_the_pattern_maker",
                "name": "Ria",
                "reference_image_url": "https://cdn.test/workflow/character_ref_ria_the_pattern_maker.webp",
            },
            "recurring_characters": [
                {
                    "character_id": "leo_the_explorer",
                    "name": "Leo",
                    "reference_image_url": "https://cdn.test/workflow/character_ref_leo_the_explorer.webp",
                }
            ],
        },
        "character_reference_manifest": [
            {
                "character_id": "ria_the_pattern_maker",
                "name": "Ria",
                "reference_image_url": "https://cdn.test/workflow/character_ref_ria_the_pattern_maker.webp",
            },
            {
                "character_id": "leo_the_explorer",
                "name": "Leo",
                "reference_image_url": "https://cdn.test/workflow/character_ref_leo_the_explorer.webp",
            },
        ],
    }

    updated = await service._copy_character_references_to_final_story_storage(image_plan, final_story_id)

    manifest = updated["character_reference_manifest"]
    assert manifest[0]["reference_image_url"].endswith("/character_ref_ria_the_pattern_maker.webp")
    assert manifest[1]["reference_image_url"].endswith("/character_ref_leo_the_explorer.webp")
    assert updated["visual_bible"]["hero"]["persistent_reference_image_url"] == manifest[0]["reference_image_url"]
    assert (
        updated["visual_bible"]["recurring_characters"][0]["persistent_reference_image_url"]
        == manifest[1]["reference_image_url"]
    )
    assert [call[3] for call in calls] == [
        "character_ref_ria_the_pattern_maker.png",
        "character_ref_leo_the_explorer.png",
    ]


@pytest.mark.asyncio
async def test_list_batch_jobs_returns_paginated_typed_response_with_filters():
    user_id = uuid4()
    workflow_id = uuid4()
    job = _batch_job(workflow_id=workflow_id, status=StoryBatchJobStatus.RUNNING)
    calls = {}

    class _BatchJobs:
        async def list_for_user(
            self,
            user_id_arg,
            *,
            page,
            page_size,
            workflow_id=None,
            status=None,
            story_type=None,
        ):
            calls["user_id"] = user_id_arg
            calls["page"] = page
            calls["page_size"] = page_size
            calls["workflow_id"] = workflow_id
            calls["status"] = status
            calls["story_type"] = story_type
            return [job], 1

    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.batch_jobs = _BatchJobs()

    response = await service.list_batch_jobs(
        user_id,
        page=1,
        page_size=20,
        workflow_id=workflow_id,
        status_filter=StoryBatchJobStatus.RUNNING,
    )

    assert calls == {
        "user_id": user_id,
        "page": 1,
        "page_size": 20,
        "workflow_id": workflow_id,
        "status": StoryBatchJobStatus.RUNNING,
        "story_type": None,
    }
    assert response.total == 1
    assert response.page == 1
    assert response.page_size == 20
    assert response.total_pages == 1
    assert response.items[0].id == job.id
    assert response.items[0].workflow_id == workflow_id
    assert response.items[0].status == "RUNNING"
    assert response.items[0].request_keys == ["cover", "page_1"]
    assert response.items[0].missing_keys == ["page_1"]


@pytest.mark.asyncio
async def test_list_batch_jobs_can_filter_by_story_type():
    user_id = uuid4()
    calls = {}

    class _BatchJobs:
        async def list_for_user(
            self,
            user_id_arg,
            *,
            page,
            page_size,
            workflow_id=None,
            status=None,
            story_type=None,
        ):
            calls["user_id"] = user_id_arg
            calls["story_type"] = story_type
            return [], 0

    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.batch_jobs = _BatchJobs()

    response = await service.list_batch_jobs(
        user_id,
        page=1,
        page_size=20,
        story_type=CustomStoryWorkflowType.GENERIC,
    )

    assert calls == {
        "user_id": user_id,
        "story_type": CustomStoryWorkflowType.GENERIC,
    }
    assert response.total == 0


@pytest.mark.asyncio
async def test_batch_job_repository_lists_custom_workflow_jobs_newest_first():
    captured = {}

    class _Scalars:
        def all(self):
            return []

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def scalar(self, statement):
            captured["count_statement"] = statement
            return 0

        async def execute(self, statement):
            captured["id_statement"] = statement
            return _Result()

    repository = CustomStoryBatchJobRepository(_Session())

    jobs, total = await repository.list_for_user(
        uuid4(),
        page=1,
        page_size=20,
    )

    statement_sql = str(captured["id_statement"].compile(compile_kwargs={"literal_binds": False}))
    assert jobs == []
    assert total == 0
    assert "custom_story_batch_jobs" in statement_sql
    assert "ORDER BY custom_story_batch_jobs.created_at DESC, custom_story_batch_jobs.id DESC" in statement_sql


def test_workflow_list_projection_excludes_heavy_json_and_model_columns():
    selected_columns = {column.key for column in CustomStoryWorkflowRepository._list_load_columns()}

    assert "id" in selected_columns
    assert "created_at" in selected_columns
    assert "updated_at" in selected_columns
    assert "title" in selected_columns
    assert "input_request" not in selected_columns
    assert "story_plan_json" not in selected_columns
    assert "story_json" not in selected_columns
    assert "image_plan_json" not in selected_columns
    assert "image_model" not in selected_columns
    assert "reference_image_model" not in selected_columns
    assert {column.key for column in CustomStoryWorkflowEntity.__table__.columns} >= selected_columns


def test_custom_story_workflow_step_order_includes_publish_last():
    assert CustomStoryWorkflowService.ORDERED_STEPS == [
        CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
        CustomStoryWorkflowStep.STORY_PLAN_VALIDATION,
        CustomStoryWorkflowStep.STORY_GENERATION,
        CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
        CustomStoryWorkflowStep.NARRATION_GENERATION,
        CustomStoryWorkflowStep.PUBLISH_STORY,
    ]
    assert "INPUT_SAFETY_VALIDATION" not in [step.value for step in CustomStoryWorkflowService.ORDERED_STEPS]


def test_story_generation_request_defaults_to_delayed_and_execute_flags():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
    )

    assert payload.reader_category == ReaderCategory.EARLY_READER
    assert payload.execute_image is True
    assert payload.execute_narration is True
    assert payload.skip_image_generation is False
    assert payload.execute_workflow is False
    assert payload.use_child_character is False


def test_story_generation_request_ignores_legacy_mode_and_processing_mode_fields():
    payload = StoryGenerationRequest.model_validate(
        {
            "child_id": str(uuid4()),
            "mode": "EVENT_DRIVEN",
            "processing_mode": "instant",
            "reader_category": "Early Reader",
            "category": "adventure",
        }
    )

    assert not hasattr(payload, "mode")
    assert not hasattr(payload, "processing_mode")
    assert payload.category == "adventure"


def test_story_generation_request_can_disable_workflow_execution_for_ui_testing():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        execute_workflow=False,
    )

    assert payload.execute_workflow is False


def test_custom_story_execute_workflow_uses_env_default_when_omitted(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
    )
    monkeypatch.setattr(custom_story_workflow_service.settings, "CUSTOM_STORY_EXECUTE_WORKFLOW_DEFAULT", True)

    assert CustomStoryWorkflowService._effective_execute_workflow(payload) is True


def test_custom_story_execute_workflow_request_value_overrides_env_default(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        execute_workflow=False,
    )
    monkeypatch.setattr(custom_story_workflow_service.settings, "CUSTOM_STORY_EXECUTE_WORKFLOW_DEFAULT", True)

    assert CustomStoryWorkflowService._effective_execute_workflow(payload) is False


def test_story_generation_request_reader_category_alias_derives_age_group():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="GROWING_READER",
        category="adventure",
    )

    assert payload.reader_category == ReaderCategory.GROWING_READER
    assert age_group_for_reader_category(payload.reader_category) == "6-9"


def test_story_generation_request_execute_image_updates_legacy_skip_flag():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        execute_image=False,
    )

    assert payload.execute_image is False
    assert payload.skip_image_generation is True


def test_story_generation_request_rejects_delayed_with_all_media_disabled():
    with pytest.raises(ValidationError):
        StoryGenerationRequest(
            child_id=uuid4(),
            reader_category="Early Reader",
            category="adventure",
            execute_image=False,
            execute_narration=False,
        )


@pytest.mark.asyncio
async def test_first_incomplete_step_uses_persisted_outputs():
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=lambda *args, **kwargs: None)
    workflow = _workflow(
        story_plan_json={"pages": [{"page": 1}]},
        story_plan_validated=True,
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
    )

    assert await service._first_incomplete_step(workflow) == CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION


@pytest.mark.asyncio
async def test_first_incomplete_step_does_not_skip_partial_audio():
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)

    async def _latest_for_workflow_type(*args, **kwargs):
        _ = args, kwargs
        return None

    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=_latest_for_workflow_type)
    workflow = _workflow(
        processing_mode="delayed",
        execute_image=False,
        execute_narration=True,
        story_plan_json={"pages": [{"page": 1}]},
        story_plan_validated=True,
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "audio_url": "https://cdn.test/page-1.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                },
                {"page_number": 2, "text": "Mira tried again."},
            ]
        },
    )

    assert await service._first_incomplete_step(workflow) == CustomStoryWorkflowStep.NARRATION_GENERATION


@pytest.mark.asyncio
async def test_first_incomplete_step_requires_audio_even_if_latest_audio_job_succeeded():
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)

    async def _latest_for_workflow_type(*args, **kwargs):
        _ = args, kwargs
        return SimpleNamespace(status=StoryBatchJobStatus.SUCCEEDED)

    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=_latest_for_workflow_type)
    workflow = _workflow(
        processing_mode="delayed",
        execute_image=False,
        execute_narration=True,
        story_plan_json={"pages": [{"page": 1}]},
        story_plan_validated=True,
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
    )

    assert await service._first_incomplete_step(workflow) == CustomStoryWorkflowStep.NARRATION_GENERATION


@pytest.mark.asyncio
async def test_publish_story_rejects_incomplete_audio():
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    workflow = _workflow(
        execute_narration=True,
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "audio_url": "https://cdn.test/page-1.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                },
                {"page_number": 2, "text": "Mira tried again."},
            ]
        },
    )

    with pytest.raises(AppException) as exc_info:
        await service._publish_story(workflow)

    assert exc_info.value.code == "CUSTOM_STORY_AUDIO_INCOMPLETE"
    assert workflow.story_id is None


def _safety_inspection(*, safe: bool, provider: str = "google", model: str = "gemini-2.5-flash"):
    result = StoryInputSafetyResult(
        safe=safe,
        risk_level="LOW" if safe else "HIGH",
        blocked_categories=[] if safe else ["ADULT_THEME"],
        reason="Valid age-appropriate children's story idea." if safe else "Contains adult themes unsuitable for children.",
        safe_rewrite=None if safe else "A child-friendly version focusing on a bedtime routine.",
    )
    response_json = {
        "safe": result.safe,
        "risk_level": result.risk_level,
        "blocked_categories": result.blocked_categories,
        "reason": result.reason,
        "safe_rewrite": result.safe_rewrite,
    }
    return StoryInputSafetyInspection(
        request_json={
            "child_id": "child-1",
            "reader_category": "Growing Reader",
            "age_group": "6-9",
            "category": "adventure",
            "learning_goal": "kindness",
            "context": "A gentle story about helping.",
            "use_child_character": False,
            "cast_mode": "IMAGINED_CAST",
            "execute_image": False,
            "skip_image_generation": True,
            "execute_narration": True,
            "skip_validation": False,
            "execute_workflow": False,
        },
        prompt="safety prompt",
        provider=provider,
        model=model,
        result=result,
        response_text=json.dumps(response_json),
        response_json=response_json,
    )


@pytest.mark.asyncio
async def test_create_runs_input_safety_before_creating_workflow(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Growing Reader",
        category="adventure",
        learning_goal="kindness",
        context="A gentle story about helping.",
        skip_image_generation=True,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    calls = []

    async def _inspect(_self, _payload):
        calls.append("inspect")
        return _safety_inspection(safe=True)

    async def _get_child(user_id, child_id):
        calls.append("get_child")
        return SimpleNamespace(id=child_id, dob=date(2018, 1, 1), character_image_url="https://cdn.test/child.png")

    async def _create_workflow(**kwargs):
        calls.append("create_workflow")
        kwargs.setdefault("request_number", 1)
        return _workflow(
            id=uuid4(),
            user_id=kwargs["user_id"],
            child_id=kwargs["child_id"],
            request_number=kwargs["request_number"],
            generation_mode=kwargs["generation_mode"],
            processing_mode=kwargs["processing_mode"],
            age_group=SimpleNamespace(value=kwargs["age_group"]),
            reader_category=kwargs["reader_category"],
            use_child_character=kwargs["use_child_character"],
            execute_image=kwargs["execute_image"],
            execute_narration=kwargs["execute_narration"],
            skip_validation=kwargs["skip_validation"],
            execute_workflow=kwargs["execute_workflow"],
            status=kwargs["status"],
            ai_provider=kwargs["ai_provider"],
            text_model=kwargs["text_model"],
            image_model=kwargs["image_model"],
            reference_image_model=kwargs["reference_image_model"],
        )

    async def _audit_create(**kwargs):
        calls.append("audit_create")
        data = dict(kwargs)
        data.setdefault("workflow_id", None)
        data.setdefault("id", uuid4())
        return SimpleNamespace(**data)

    async def _audit_update(audit):
        calls.append("audit_update")
        return audit

    async def _commit():
        calls.append("commit")

    async def _create_event_if_absent(**_kwargs):
        return None

    monkeypatch.setattr(custom_story_workflow_service.settings, "CUSTOM_STORY_EXECUTE_WORKFLOW_DEFAULT", False)
    monkeypatch.setattr(custom_story_workflow_service.StoryInputSafetyService, "inspect", _inspect)
    service.children = SimpleNamespace(get_for_user=_get_child)
    service.workflows = SimpleNamespace(create=_create_workflow)
    service.input_safety_audits = SimpleNamespace(create=_audit_create, update=_audit_update)
    service.events = SimpleNamespace(create_if_absent=_create_event_if_absent)
    service.session = SimpleNamespace(commit=_commit)

    response = await service.create(uuid4(), payload)

    assert response.workflow_id
    assert response.request_number == 1
    assert calls[:5] == ["get_child", "inspect", "audit_create", "audit_update", "commit"]
    assert "create_workflow" in calls
    assert calls.count("commit") == 2
    assert response.reader_category == "Growing Reader"
    assert response.age_group == "6-9"


@pytest.mark.asyncio
async def test_create_allows_imagined_cast_without_child_character_image(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    created = {}
    queued_events = []

    async def _get_child(user_id, child_id):
        return SimpleNamespace(id=child_id, dob=date(2019, 1, 1), character_image_url=None)

    async def _inspect(_self, _payload):
        return _safety_inspection(safe=True)

    async def _create_workflow(**kwargs):
        kwargs.setdefault("request_number", 1)
        created.update(kwargs)
        return _workflow(
            id=uuid4(),
            user_id=kwargs["user_id"],
            child_id=kwargs["child_id"],
            request_number=kwargs["request_number"],
            generation_mode=kwargs["generation_mode"],
            processing_mode=kwargs["processing_mode"],
            age_group=SimpleNamespace(value=kwargs["age_group"]),
            reader_category=kwargs["reader_category"],
            use_child_character=kwargs["use_child_character"],
            execute_image=kwargs["execute_image"],
            execute_narration=kwargs["execute_narration"],
            skip_validation=kwargs["skip_validation"],
            execute_workflow=kwargs["execute_workflow"],
            status=kwargs["status"],
            ai_provider=kwargs["ai_provider"],
            text_model=kwargs["text_model"],
            image_model=kwargs["image_model"],
            reference_image_model=kwargs["reference_image_model"],
        )

    async def _commit():
        return None

    async def _audit_create(**kwargs):
        data = dict(kwargs)
        data.setdefault("workflow_id", None)
        data.setdefault("id", uuid4())
        return SimpleNamespace(**data)

    async def _audit_update(audit):
        return audit

    async def _create_event_if_absent(**_kwargs):
        queued_events.append(_kwargs)
        return None

    service.input_safety_audits = SimpleNamespace(create=_audit_create, update=_audit_update)
    service.children = SimpleNamespace(get_for_user=_get_child)
    service.workflows = SimpleNamespace(create=_create_workflow)
    service.events = SimpleNamespace(create_if_absent=_create_event_if_absent)
    service.session = SimpleNamespace(commit=_commit)
    monkeypatch.setattr(custom_story_workflow_service.StoryInputSafetyService, "inspect", _inspect)

    response = await service.create(uuid4(), payload)

    assert response.workflow_id
    assert created["processing_mode"] == "delayed"
    assert created["use_child_character"] is False
    assert queued_events == [
        {
            "workflow_id": response.workflow_id,
            "step_name": CustomStoryWorkflowStep.STORY_PLAN_GENERATION,
        }
    ]


@pytest.mark.asyncio
async def test_create_blocks_unsafe_input_without_creating_workflow(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        learning_goal="kindness",
        context="A story about adult parties.",
        skip_image_generation=True,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    calls = []

    async def _get_child(user_id, child_id):
        calls.append("get_child")
        return SimpleNamespace(id=child_id, dob=date(2019, 1, 1), character_image_url="https://cdn.test/child.png")

    async def _inspect(_self, _payload):
        calls.append("inspect")
        return _safety_inspection(safe=False)

    async def _create_workflow(**kwargs):
        calls.append("create_workflow")
        raise AssertionError("workflow must not be created when safety fails")

    async def _audit_create(**kwargs):
        calls.append("audit_create")
        return SimpleNamespace(id=uuid4(), workflow_id=None, **kwargs)

    async def _audit_update(audit):
        calls.append("audit_update")
        return audit

    async def _commit():
        calls.append("commit")

    monkeypatch.setattr(custom_story_workflow_service.StoryInputSafetyService, "inspect", _inspect)
    service.children = SimpleNamespace(get_for_user=_get_child)
    service.workflows = SimpleNamespace(create=_create_workflow)
    service.input_safety_audits = SimpleNamespace(create=_audit_create, update=_audit_update)
    service.session = SimpleNamespace(commit=_commit)

    with pytest.raises(AppException) as exc_info:
        await service.create(uuid4(), payload)

    assert exc_info.value.code == "STORY_INPUT_UNSAFE"
    assert "workflow must not be created" not in str(exc_info.value)
    assert calls[:5] == ["get_child", "inspect", "audit_create", "audit_update", "commit"]
    assert "create_workflow" not in calls
    assert calls.count("commit") == 1


@pytest.mark.asyncio
async def test_create_requires_character_image_when_child_hero_requested():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        use_child_character=True,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)

    async def _get_child(user_id, child_id):
        return SimpleNamespace(id=child_id, dob=date(2019, 1, 1), character_image_url=None)

    service.children = SimpleNamespace(get_for_user=_get_child)

    with pytest.raises(AppException) as exc_info:
        await service.create(uuid4(), payload)

    assert exc_info.value.code == "NO_CHARACTER_IMAGE"


@pytest.mark.asyncio
async def test_list_custom_story_workflows_returns_request_number():
    workflow_1 = _workflow(request_number=7, category="adventure")
    workflow_2 = _workflow(request_number=8, category="bedtime")
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)

    async def _list_for_user(user_id, *, page, page_size, child_id=None, status_filter=None):
        assert page == 1
        assert page_size == 20
        assert child_id is None
        assert status_filter is None
        return [workflow_2, workflow_1], 2

    service.workflows = SimpleNamespace(list_for_user=_list_for_user)

    response = await service.list(uuid4(), page=1, page_size=20)

    assert response.total == 2
    assert [item.request_number for item in response.items] == [8, 7]
    assert [item.workflow_id for item in response.items] == [workflow_2.id, workflow_1.id]


def _custom_workflow_response(workflow_id):
    now = datetime.now(UTC)
    return CustomStoryWorkflowResponse(
        workflow_id=workflow_id,
        request_number=12,
        story_id=None,
        child_id=uuid4(),
        status=CustomStoryWorkflowStatus.PENDING.value,
        current_step=None,
        error_message=None,
        generation_mode="INPUT_DRIVEN",
        processing_mode="delayed",
        reader_category="Early Reader",
        age_group="3-6",
        category="adventure",
        learning_goal=None,
        context=None,
        event_description=None,
        use_child_character=False,
        execute_image=True,
        execute_narration=True,
        skip_validation=False,
        execute_workflow=False,
        title=None,
        summary=None,
        moral=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_create_custom_story_workflow_skips_background_when_execution_disabled(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        execute_workflow=False,
    )
    response_data = _custom_workflow_response(workflow_id)
    calls = {}

    class _FakeCustomStoryWorkflowService:
        async def create(self, requested_user_id, requested_payload):
            calls["create"] = (requested_user_id, requested_payload)
            return response_data

    route = story_routes.StoriesRouter().create_custom_story_workflow
    container = SimpleNamespace(custom_story_workflow=_FakeCustomStoryWorkflowService())
    route_response = Response()

    response = await route(
        payload,
        route_response,
        SimpleNamespace(id=user_id),
        container,
    )

    assert response.data == response_data
    assert response.message == "Custom story workflow saved successfully; execution skipped"
    assert route_response.status_code == 201
    assert calls["create"] == (user_id, payload)


@pytest.mark.asyncio
async def test_create_custom_story_workflow_queues_event_when_requested(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
        execute_workflow=True,
    )
    response_data = _custom_workflow_response(workflow_id)
    response_data.execute_workflow = True

    class _FakeCustomStoryWorkflowService:
        async def create(self, requested_user_id, requested_payload):
            assert requested_user_id == user_id
            assert requested_payload == payload
            return response_data

    route = story_routes.StoriesRouter().create_custom_story_workflow
    container = SimpleNamespace(custom_story_workflow=_FakeCustomStoryWorkflowService())
    route_response = Response()

    response = await route(
        payload,
        route_response,
        SimpleNamespace(id=user_id),
        container,
    )

    assert response.data == response_data
    assert response.message == "Custom story workflow queued successfully"
    assert route_response.status_code == 202


@pytest.mark.asyncio
async def test_story_generation_text_batch_failure_creates_one_full_retry_event():
    workflow = _workflow(
        processing_mode="delayed",
        status=CustomStoryWorkflowStatus.IN_PROGRESS,
        current_step=CustomStoryWorkflowStep.STORY_GENERATION.value,
        story_plan_json={"pages": [{"page_number": 1}, {"page_number": 2}]},
    )
    source_event = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        step_name=CustomStoryWorkflowStep.STORY_GENERATION,
        status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
        retry_count=0,
        retry_flag=False,
        retry_comment=None,
        retry_source_event_id=None,
        metadata_json={"batch_job_id": "old-job"},
        error_message=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        job_type=StoryBatchJobType.STORY,
        status=StoryBatchJobStatus.FAILED,
        provider_job_name="batches/story",
        provider_state="JOB_STATE_SUCCEEDED",
        error_message="Story generation returned 1 pages; expected 2",
        response_payload=None,
    )
    created_events = []
    updated_events = []
    updated_jobs = []
    updated_workflows = []
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update_event(event):
        updated_events.append(event)
        return event

    async def _create_event(**kwargs):
        event = SimpleNamespace(
            id=uuid4(),
            status=CustomStoryWorkflowEventStatus.PENDING,
            completed_at=None,
            error_message=None,
            **kwargs,
        )
        created_events.append(event)
        return event

    async def _update_job(job_arg):
        updated_jobs.append(job_arg)
        return job_arg

    async def _update_workflow(workflow_arg):
        updated_workflows.append(workflow_arg)
        return workflow_arg

    service.events = SimpleNamespace(update=_update_event, create=_create_event)
    service.batch_jobs = SimpleNamespace(update=_update_job)
    service.workflows = SimpleNamespace(update=_update_workflow)

    raw_text = '{"title":"Tiny","pages":[{"page_number":1,"emotion":"happy","text":"Only one."}],"moral":"Try."}'
    await service._handle_text_batch_failure(
        workflow,
        job,
        source_event,
        CustomStoryWorkflowStep.STORY_GENERATION,
        error_message=job.error_message,
        raw_text=raw_text,
        provider_response={"raw": "provider"},
    )

    assert job.response_payload["text"] == raw_text
    assert source_event.status == CustomStoryWorkflowEventStatus.FAILED
    assert source_event.error_message == job.error_message
    assert len(created_events) == 1
    retry_event = created_events[0]
    assert retry_event.step_name == CustomStoryWorkflowStep.STORY_GENERATION
    assert retry_event.retry_flag is True
    assert retry_event.retry_comment == "FULL_BATCH_RETRY"
    assert retry_event.retry_source_event_id == source_event.id
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
    assert workflow.error_message is None
    assert updated_jobs
    assert updated_events
    assert updated_workflows


@pytest.mark.asyncio
async def test_story_generation_text_batch_retry_failure_marks_workflow_failed():
    workflow = _workflow(status=CustomStoryWorkflowStatus.IN_PROGRESS)
    retry_event = SimpleNamespace(
        id=uuid4(),
        status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
        retry_count=1,
        retry_flag=True,
        retry_comment="FULL_BATCH_RETRY",
        metadata_json={},
        error_message=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        job_type=StoryBatchJobType.STORY,
        status=StoryBatchJobStatus.FAILED,
        provider_job_name="batches/story",
        provider_state="JOB_STATE_SUCCEEDED",
        error_message="Invalid JSON",
        response_payload=None,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update(value):
        return value

    service.events = SimpleNamespace(update=_update)
    service.batch_jobs = SimpleNamespace(update=_update)
    service.workflows = SimpleNamespace(update=_update)

    await service._handle_text_batch_failure(
        workflow,
        job,
        retry_event,
        CustomStoryWorkflowStep.STORY_GENERATION,
        error_message=job.error_message,
        raw_text="{bad json",
        provider_response=None,
    )

    assert workflow.status == CustomStoryWorkflowStatus.FAILED
    assert workflow.error_message == "Invalid JSON"
    assert retry_event.status == CustomStoryWorkflowEventStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step_name,job_type",
    [
        (CustomStoryWorkflowStep.STORY_PLAN_GENERATION, StoryBatchJobType.STORY_PLAN),
        (CustomStoryWorkflowStep.IMAGE_PLAN_GENERATION, StoryBatchJobType.IMAGE_PLAN),
    ],
)
async def test_text_batch_failure_creates_full_retry_for_plan_steps(step_name, job_type):
    workflow = _workflow(status=CustomStoryWorkflowStatus.IN_PROGRESS)
    source_event = SimpleNamespace(
        id=uuid4(),
        status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
        retry_count=0,
        retry_flag=False,
        retry_comment=None,
        metadata_json={"batch_job_id": "old-job"},
        error_message=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        job_type=job_type,
        status=StoryBatchJobStatus.FAILED,
        provider_job_name="batches/text",
        provider_state="JOB_STATE_SUCCEEDED",
        error_message="Invalid JSON",
        response_payload=None,
    )
    created_events = []
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update(value):
        return value

    async def _create_event(**kwargs):
        event = SimpleNamespace(id=uuid4(), status=CustomStoryWorkflowEventStatus.PENDING, **kwargs)
        created_events.append(event)
        return event

    service.events = SimpleNamespace(update=_update, create=_create_event)
    service.batch_jobs = SimpleNamespace(update=_update)
    service.workflows = SimpleNamespace(update=_update)

    await service._handle_text_batch_failure(
        workflow,
        job,
        source_event,
        step_name,
        error_message=job.error_message,
        raw_text="{bad json",
        provider_response=None,
    )

    assert len(created_events) == 1
    assert created_events[0].step_name == step_name
    assert created_events[0].retry_flag is True
    assert created_events[0].retry_comment == "FULL_BATCH_RETRY"
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
    assert workflow.current_step == step_name.value


@pytest.mark.asyncio
async def test_process_reconciled_image_job_partial_retry_creates_retry_event(monkeypatch):
    workflow = _workflow(
        processing_mode="delayed",
        story_json={
            "pages": [
                {"page_number": 1, "text": "Mira listened."},
                {"page_number": 2, "text": "Mira tried again."},
            ]
        },
        image_plan_json={
            "pages": [
                {"page_number": 1, "image_prompt": "page one"},
                {"page_number": 2, "image_prompt": "page two"},
            ]
        },
        status=CustomStoryWorkflowStatus.IN_PROGRESS,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        job_type=StoryBatchJobType.IMAGE,
        status=StoryBatchJobStatus.RUNNING,
        attempt=1,
        provider_job_name="batches/image",
        provider_state="JOB_STATE_SUCCEEDED",
        request_keys=["page_1", "page_2"],
        response_payload=None,
        error_message=None,
        completed_item_count=0,
        failed_item_count=0,
        missing_keys=[],
    )
    source_event = SimpleNamespace(
        id=uuid4(),
        status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
        retry_count=0,
        retry_flag=False,
        retry_comment=None,
        metadata_json={"batch_job_id": str(job.id)},
        error_message=None,
        completed_at=None,
    )
    retry_job = SimpleNamespace(
        id=uuid4(),
        job_type=StoryBatchJobType.IMAGE,
        provider_job_name="batches/image-retry",
        attempt=2,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()
    calls = {"retry_items": None, "created_events": []}

    async def _update(value):
        return value

    async def _batch_submitted_for_job(**kwargs):
        assert kwargs["batch_job_id"] == job.id
        return source_event

    async def _create_event(**kwargs):
        event = SimpleNamespace(
            id=uuid4(),
            status=CustomStoryWorkflowEventStatus.PENDING,
            completed_at=None,
            **kwargs,
        )
        calls["created_events"].append(event)
        return event

    class _BatchRunner:
        async def _build_image_items(self, story, story_json, image_plan):
            _ = story, story_json, image_plan
            return [SimpleNamespace(key="page_1"), SimpleNamespace(key="page_2")]

        async def _process_image_batch_responses(self, story, story_json, items, provider_job):
            _ = story, story_json, items, provider_job
            return {"page_1"}, {"page_2"}, {"items": [{"key": "page_2", "status": "missing_response"}]}

        async def _submit_image_batch_job_only(self, story, items, *, attempt):
            _ = story
            calls["retry_items"] = [item.key for item in items]
            calls["retry_attempt"] = attempt
            return retry_job

    monkeypatch.setattr(custom_story_workflow_service.settings, "STORY_BATCH_MAX_IMAGE_RETRIES", 3)
    service.batch_jobs = SimpleNamespace(update=_update)
    service.workflows = SimpleNamespace(update=_update)
    service.events = SimpleNamespace(
        update=_update,
        create=_create_event,
        batch_submitted_for_job=_batch_submitted_for_job,
    )
    service.session = SimpleNamespace(commit=lambda: None)

    async def _commit():
        return None

    service.session = SimpleNamespace(commit=_commit)

    await service._process_reconciled_image_job(workflow, job, SimpleNamespace(), _BatchRunner())

    assert calls["retry_items"] == ["page_2"]
    assert calls["retry_attempt"] == 2
    assert source_event.status == CustomStoryWorkflowEventStatus.FAILED
    assert len(calls["created_events"]) == 1
    retry_event = calls["created_events"][0]
    assert retry_event.retry_flag is True
    assert retry_event.retry_comment == "PARTIAL_RETRY"
    assert retry_event.status == CustomStoryWorkflowEventStatus.BATCH_SUBMITTED
    assert retry_event.metadata_json["batch_job_id"] == str(retry_job.id)
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
    assert workflow.current_step == CustomStoryWorkflowStep.IMAGE_GENERATION.value


@pytest.mark.asyncio
async def test_skipped_image_generation_creates_completed_step_record():
    workflow = _workflow(
        execute_image=False,
        skip_validation=False,
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
    )
    created_steps = []
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = SimpleNamespace()

    async def _latest(workflow_id, step_name):
        return None

    async def _create(workflow_id, step_name):
        step = SimpleNamespace(
            workflow_id=workflow_id,
            step_name=step_name,
            status=StepStatus.PENDING,
            started_at=None,
            completed_at=None,
            input_json=None,
            output_json=None,
            error_message=None,
        )
        created_steps.append(step)
        return step

    async def _update(step):
        return step

    service.steps.latest_for_workflow_step = _latest
    service.steps.create = _create
    service.steps.update = _update

    await service._record_completed_step(
        workflow,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
        {"story_json": workflow.story_json},
        {"images_skipped": True},
    )

    assert created_steps[0].status == StepStatus.COMPLETED
    assert created_steps[0].output_json == {"images_skipped": True}


@pytest.mark.asyncio
async def test_image_plan_validation_persists_normalized_outfit_motif_lock():
    image_plan = {
        "visual_bible": {
            "hero": {
                "character_id": "hero_child",
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "blue t-shirt with yellow stars and red shorts",
                "footwear": "yellow rain boots",
                "outfit_lock": "blue t-shirt with yellow stars and red shorts",
                "body_scale_lock": "Same early-reader child height, build, proportions, and age appearance.",
                "relative_size": "child-sized hero",
                "signature_item": "Moon map",
            },
            "companion": {"appearance": "A small glowing moth."},
            "recurring_characters": [],
        },
        "cover": {
            "visual_focus": "Mira holding the moon map.",
            "emotion": "wonder",
            "characters_present": ["Mira"],
            "reference_character_ids": ["hero_child"],
            "image_prompt": "Mira smiles while the glowing moon map opens in a moonlit library.",
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "visual_importance": "medium",
                "emotion": "wonder",
                "scene_action": "Mira points to a glowing path on the map.",
                "environment": "Moonlit library with warm shelves.",
                "characters_present": ["Mira"],
                "reference_character_ids": ["hero_child"],
                "image_prompt": "Mira follows a glowing map clue in the library.",
            }
        ],
        "back_cover": {
            "emotion": "warm joy",
            "characters_present": ["Mira"],
            "reference_character_ids": ["hero_child"],
            "image_prompt": "Mira closes the moon map with a peaceful smile.",
        },
    }
    workflow = _workflow(
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json=image_plan,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update_workflow(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    class _Runner:
        async def _step_validate_image_plan(self, story, image_plan_arg, story_json, flags):
            _ = story, flags
            normalized = StoryService._normalize_image_plan(image_plan_arg)
            result = ImagePlanValidator().validate(normalized, story_json=story_json, skip_footwear_validation=True)
            assert result.ok, result.errors
            return normalized

        async def _ensure_image_plan_character_references(self, story, image_plan_arg):
            _ = story
            return image_plan_arg

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)

    await service._execute_step(
        _Runner(),
        workflow,
        CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION,
    )

    assert workflow.image_plan_validated is True
    assert "Motif lock: one star centered on the front." in workflow.image_plan_json["visual_bible"]["hero"]["outfit"]
    assert "Motif lock: one star centered on the front." in workflow.image_plan_json["visual_bible"]["hero"]["outfit_lock"]
    step = _step_by_name(service.steps, CustomStoryWorkflowStep.IMAGE_PLAN_VALIDATION)
    assert step.status == StepStatus.COMPLETED
    assert step.output_json == workflow.image_plan_json


@pytest.mark.asyncio
async def test_publish_story_creates_final_story_and_sets_workflow_story_id():
    workflow = _workflow(
        title="The Moon Bell",
        summary="A child listens carefully.",
        moral="Listening helps.",
        story_json={
            "cover_image_url": "https://cdn.test/cover.png",
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "image_url": "https://cdn.test/page.png",
                    "audio_url": "https://cdn.test/page.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                }
            ],
        },
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    story_id = uuid4()
    calls = {"pages": []}

    async def _create_story(**kwargs):
        calls["create"] = kwargs
        return SimpleNamespace(id=story_id, status=None, current_step="x", title=None, summary=None, moral=None)

    async def _upsert_content(story, *, language, story_json):
        calls["content"] = (story.id, language, story_json)

    async def _update_story(story):
        calls["updated_story"] = story

    async def _upsert_page(*args, **kwargs):
        calls["pages"].append((args, kwargs))

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg

    service.stories = SimpleNamespace(create=_create_story, upsert_content=_upsert_content, update=_update_story)
    service.story_pages = SimpleNamespace(upsert_page=_upsert_page)
    service.workflows = SimpleNamespace(update=_update_workflow)
    service.batch_jobs = SimpleNamespace(list_by_workflow=lambda workflow_id: _empty_jobs())

    async def _copy_images(story_json, final_story_id):
        calls["copy"] = final_story_id
        return story_json

    async def _empty_jobs():
        return []

    service._copy_story_images_to_final_story_storage = _copy_images

    await service._publish_story(workflow)

    assert workflow.story_id == story_id
    assert calls["create"]["generation_mode"] == "INPUT_DRIVEN"
    assert calls["content"][0] == story_id
    assert len(calls["pages"]) == 2
    assert calls["copy"] == story_id


@pytest.mark.asyncio
async def test_delayed_image_generation_uses_batch_submission_hook():
    workflow = _workflow(
        processing_mode="delayed",
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json={"cover": {"image_prompt": "cover"}, "pages": [{"page_number": 1, "image_prompt": "page"}]},
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    calls = {}

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["commits"] = calls.get("commits", 0) + 1

    class _BatchRunner:
        async def _step_submit_images_batch(self, story, story_json, image_plan):
            calls["batch_submit"] = (story.id, story_json, image_plan)
            return SimpleNamespace(
                id=uuid4(),
                status=StoryBatchJobStatus.SUBMITTED,
                provider_job_name="batches/custom-image-job",
                provider_state="JOB_STATE_PENDING",
            )

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)
    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=lambda *args, **kwargs: _no_active_job())
    service.steps = _StepRepo()
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    async def _no_active_job():
        return None

    await service._execute_step(
        SimpleNamespace(),
        workflow,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
    )

    assert calls["batch_submit"] == (workflow.id, workflow.story_json, workflow.image_plan_json)
    step = _step_by_name(service.steps, CustomStoryWorkflowStep.IMAGE_GENERATION)
    assert step.status == StepStatus.SUBMITTED_BATCH_JOB
    assert step.output_json["batch_job_id"]
    assert workflow.current_step == CustomStoryWorkflowStep.IMAGE_GENERATION.value
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_delayed_image_generation_reuses_active_batch_on_retry():
    workflow = _workflow(
        processing_mode="delayed",
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json={"cover": {"image_prompt": "cover"}, "pages": [{"page_number": 1, "image_prompt": "page"}]},
    )
    active_job = SimpleNamespace(
        id=uuid4(),
        status=StoryBatchJobStatus.RUNNING,
        provider_job_name="batches/custom-image-job",
        provider_state="JOB_STATE_RUNNING",
    )
    created_steps = []
    calls = {}
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["commits"] = calls.get("commits", 0) + 1

    async def _latest_job(workflow_id, job_type):
        calls["latest_job"] = (workflow_id, job_type)
        return active_job

    async def _latest_step(workflow_id, step_name):
        return None

    async def _create_step(workflow_id, step_name):
        step = SimpleNamespace(
            workflow_id=workflow_id,
            step_name=step_name,
            status=StepStatus.PENDING,
            started_at=None,
            completed_at=None,
            input_json=None,
            output_json=None,
            error_message=None,
        )
        created_steps.append(step)
        return step

    async def _update_step(step):
        calls["step"] = step
        return step

    class _BatchRunner:
        async def _step_submit_images_batch(self, story, story_json, image_plan):
            raise AssertionError("active image batch should be reused, not duplicated")

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)
    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=_latest_job)
    service.steps = SimpleNamespace(
        latest_for_workflow_step=_latest_step,
        create=_create_step,
        update=_update_step,
    )
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    await service._execute_step(
        SimpleNamespace(),
        workflow,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
    )

    assert len(created_steps) == 1
    assert calls["step"].status == StepStatus.SUBMITTED_BATCH_JOB
    assert calls["step"].output_json["batch_job_id"] == str(active_job.id)
    assert "reused" in calls["step"].output_json["message"]
    assert workflow.current_step == CustomStoryWorkflowStep.IMAGE_GENERATION.value
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_delayed_narration_generation_records_submitted_batch_job():
    workflow = _workflow(
        processing_mode="delayed",
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "audio_url": "https://cdn.test/page.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                }
            ]
        },
    )
    audio_job = SimpleNamespace(
        id=uuid4(),
        status=StoryBatchJobStatus.SUBMITTED,
        provider_job_name="batches/custom-audio-job",
        provider_state="JOB_STATE_PENDING",
    )
    calls = {}
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["commits"] = calls.get("commits", 0) + 1

    async def _latest_job(workflow_id, job_type):
        if job_type == StoryBatchJobType.AUDIO and calls.get("audio_submit"):
            return audio_job
        return None

    class _BatchRunner:
        async def _ensure_audio_batch_submitted(self, story):
            calls["audio_submit"] = story.id
            return "Audio job submitted."

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)
    service.batch_jobs = SimpleNamespace(latest_for_workflow_type=_latest_job)
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    await service._execute_step(
        SimpleNamespace(),
        workflow,
        CustomStoryWorkflowStep.NARRATION_GENERATION,
    )

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert calls["audio_submit"] == workflow.id
    assert step.status == StepStatus.SUBMITTED_BATCH_JOB
    assert step.output_json["batch_job_id"] == str(audio_job.id)
    assert workflow.current_step == CustomStoryWorkflowStep.NARRATION_GENERATION.value
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_disabled_narration_generation_creates_skipped_completed_step():
    workflow = _workflow(
        execute_image=True,
        execute_narration=False,
        skip_validation=False,
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()

    async def _update_workflow(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)

    await service._execute_step(
        SimpleNamespace(),
        workflow,
        CustomStoryWorkflowStep.NARRATION_GENERATION,
    )

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert step.status == StepStatus.COMPLETED
    assert step.output_json["narration_skipped"] is True


@pytest.mark.asyncio
async def test_delayed_run_submits_image_and_narration_before_publish():
    workflow = _workflow(
        processing_mode="delayed",
        story_plan_json={"pages": [{"page_number": 1}]},
        story_plan_validated=True,
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json={"cover": {"image_prompt": "cover"}, "pages": [{"page_number": 1, "image_prompt": "page"}]},
        image_plan_validated=True,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    steps = _StepRepo()
    batches = _BatchRepo()
    calls = {"updates": 0, "commits": 0}

    async def _get_workflow(workflow_id):
        assert workflow_id == workflow.id
        return workflow

    async def _update_workflow(workflow_arg):
        calls["updates"] += 1
        return workflow_arg

    async def _commit():
        calls["commits"] += 1

    async def _rollback():
        calls["rollback"] = True

    class _Runner:
        async def _ensure_story_ai_config(self, story):
            calls["ensure_config"] = story.id

    class _BatchRunner:
        async def _step_submit_images_batch(self, story, story_json, image_plan):
            calls["image_submit"] = (story.id, story_json, image_plan)
            return batches.add(story.id, StoryBatchJobType.IMAGE)

        async def _ensure_audio_batch_submitted(self, story):
            calls["audio_submit"] = story.id
            batches.add(story.id, StoryBatchJobType.AUDIO)
            return "Audio job submitted."

    service.workflows = SimpleNamespace(get_by_id_for_update=_get_workflow, update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit, rollback=_rollback)
    service.steps = steps
    service.batch_jobs = batches
    service._story_runner = lambda workflow_arg: _Runner()
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    result = await service.run(workflow.id)

    image_step = _step_by_name(steps, CustomStoryWorkflowStep.IMAGE_GENERATION)
    audio_step = _step_by_name(steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert result is workflow
    assert calls["image_submit"][0] == workflow.id
    assert calls["audio_submit"] == workflow.id
    assert image_step.status == StepStatus.SUBMITTED_BATCH_JOB
    assert audio_step.status == StepStatus.SUBMITTED_BATCH_JOB
    assert workflow.story_id is None
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
    assert workflow.current_step == CustomStoryWorkflowStep.IMAGE_GENERATION.value


@pytest.mark.asyncio
async def test_process_reconciled_image_job_updates_matching_step_response():
    workflow = _workflow(
        processing_mode="delayed",
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json={"pages": [{"page_number": 1, "image_prompt": "page"}]},
    )
    job = SimpleNamespace(
        id=uuid4(),
        story_id=None,
        status=StoryBatchJobStatus.RUNNING,
        request_keys=["page_1"],
        response_payload=None,
        error_message=None,
        completed_item_count=0,
        failed_item_count=0,
        missing_keys=[],
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()
    calls = {}

    async def _update_job(job_arg):
        calls["job"] = job_arg
        return job_arg

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["committed"] = True

    class _BatchRunner:
        async def _build_image_items(self, story, story_json, image_plan):
            return [SimpleNamespace(key="page_1")]

        async def _process_image_batch_responses(self, story, story_json, items, provider_job):
            _ = story, story_json, items, provider_job
            return {"page_1"}, set(), {"items": [{"key": "page_1", "status": "completed"}]}

    service.batch_jobs = SimpleNamespace(update=_update_job)
    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)

    await service._process_reconciled_image_job(workflow, job, SimpleNamespace(), _BatchRunner())

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.IMAGE_GENERATION)
    assert job.status == StoryBatchJobStatus.SUCCEEDED
    assert step.status == StepStatus.COMPLETED
    assert step.output_json["completed_keys"] == ["page_1"]
    assert step.output_json["response_summary"]["items"][0]["status"] == "completed"
    assert calls["committed"] is True


@pytest.mark.asyncio
async def test_process_reconciled_audio_job_retries_only_missing_keys(monkeypatch):
    workflow = _workflow(
        processing_mode="delayed",
        story_json={
            "pages": [
                {"page_number": 1, "text": "Mira listened."},
                {"page_number": 2, "text": "Mira tried again."},
            ]
        },
        status=CustomStoryWorkflowStatus.IN_PROGRESS,
        current_step=CustomStoryWorkflowStep.NARRATION_GENERATION.value,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        status=StoryBatchJobStatus.RUNNING,
        attempt=1,
        request_keys=["page_1", "page_2"],
        response_payload=None,
        error_message=None,
        completed_item_count=0,
        failed_item_count=0,
        missing_keys=[],
    )
    retry_job = SimpleNamespace(
        id=uuid4(),
        job_type=StoryBatchJobType.AUDIO,
        provider_job_name="batches/audio-retry",
        attempt=2,
    )
    source_event = SimpleNamespace(
        id=uuid4(),
        status=CustomStoryWorkflowEventStatus.BATCH_SUBMITTED,
        retry_count=0,
        retry_flag=False,
        retry_comment=None,
        metadata_json={"batch_job_id": str(job.id)},
        error_message=None,
        completed_at=None,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()
    calls = {"retry_items": None, "created_events": []}

    async def _update_job(job_arg):
        calls["job"] = job_arg
        return job_arg

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["committed"] = True

    async def _update_event(event):
        return event

    async def _batch_submitted_for_job(**kwargs):
        assert kwargs["batch_job_id"] == job.id
        return source_event

    async def _create_event(**kwargs):
        event = SimpleNamespace(
            id=uuid4(),
            status=CustomStoryWorkflowEventStatus.PENDING,
            completed_at=None,
            **kwargs,
        )
        calls["created_events"].append(event)
        return event

    async def _fail_workflow(*args, **kwargs):
        raise AssertionError("workflow should not fail when missing audio retry is submitted")

    class _BatchRunner:
        def _build_audio_items(self, story_json, *, age_group):
            _ = story_json, age_group
            return [
                SimpleNamespace(key="page_1", page_number=1),
                SimpleNamespace(key="page_2", page_number=2),
            ]

        async def _process_audio_batch_responses(self, story, story_json, items, provider_job):
            _ = story, provider_job
            story_json["pages"][0]["audio_url"] = "https://cdn.test/page-1.wav"
            assert [item.key for item in items] == ["page_1", "page_2"]
            return {"page_1"}, {"page_2"}, {"items": [{"key": "page_2", "status": "missing_response"}]}

        async def _submit_audio_batch_job_only(self, story, items, *, attempt):
            _ = story
            calls["retry_items"] = [item.key for item in items]
            calls["retry_attempt"] = attempt
            return retry_job

    monkeypatch.setattr(custom_story_workflow_service.settings, "STORY_BATCH_MAX_AUDIO_RETRIES", 3)
    service.batch_jobs = SimpleNamespace(update=_update_job)
    service.workflows = SimpleNamespace(update=_update_workflow)
    service.events = SimpleNamespace(
        update=_update_event,
        create=_create_event,
        batch_submitted_for_job=_batch_submitted_for_job,
    )
    service.session = SimpleNamespace(commit=_commit)
    service._mark_workflow_failed = _fail_workflow

    retry_submitted = await service._process_reconciled_audio_job(
        workflow,
        job,
        SimpleNamespace(),
        _BatchRunner(),
    )

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert retry_submitted is True
    assert job.status == StoryBatchJobStatus.FAILED
    assert job.completed_item_count == 1
    assert job.failed_item_count == 1
    assert job.missing_keys == ["page_2"]
    assert calls["retry_items"] == ["page_2"]
    assert calls["retry_attempt"] == 2
    assert step.status == StepStatus.SUBMITTED_BATCH_JOB
    assert step.error_message is None
    assert step.completed_at is None
    assert step.output_json["retry_submitted"] is True
    assert step.output_json["retry_batch_job_id"] == str(retry_job.id)
    assert step.output_json["retry_comment"] == "PARTIAL_RETRY"
    assert source_event.status == CustomStoryWorkflowEventStatus.FAILED
    assert len(calls["created_events"]) == 1
    retry_event = calls["created_events"][0]
    assert retry_event.retry_flag is True
    assert retry_event.retry_comment == "PARTIAL_RETRY"
    assert retry_event.status == CustomStoryWorkflowEventStatus.BATCH_SUBMITTED
    assert retry_event.metadata_json["batch_job_id"] == str(retry_job.id)
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
    assert workflow.current_step == CustomStoryWorkflowStep.NARRATION_GENERATION.value
    assert workflow.error_message is None
    assert workflow.story_json["pages"][0]["audio_url"] == "https://cdn.test/page-1.wav"
    assert calls["committed"] is True


@pytest.mark.asyncio
async def test_process_reconciled_audio_job_fails_after_retry_limit(monkeypatch):
    workflow = _workflow(
        processing_mode="delayed",
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "audio_url": "https://cdn.test/page.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                }
            ]
        },
        status=CustomStoryWorkflowStatus.IN_PROGRESS,
        current_step=CustomStoryWorkflowStep.NARRATION_GENERATION.value,
    )
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        status=StoryBatchJobStatus.RUNNING,
        attempt=3,
        request_keys=["page_1"],
        response_payload=None,
        error_message=None,
        completed_item_count=0,
        failed_item_count=0,
        missing_keys=[],
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()
    calls = {"retry_called": False}

    async def _update_job(job_arg):
        return job_arg

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["committed"] = True

    class _BatchRunner:
        def _build_audio_items(self, story_json, *, age_group):
            _ = story_json, age_group
            return [SimpleNamespace(key="page_1", page_number=1)]

        async def _process_audio_batch_responses(self, story, story_json, items, provider_job):
            _ = story, story_json, items, provider_job
            return set(), {"page_1"}, {"items": [{"key": "page_1", "status": "missing_response"}]}

        async def _submit_audio_batch_job_only(self, story, items, *, attempt):
            _ = story, items, attempt
            calls["retry_called"] = True
            raise AssertionError("retry should not be submitted after max attempts")

    monkeypatch.setattr(custom_story_workflow_service.settings, "STORY_BATCH_MAX_AUDIO_RETRIES", 3)
    service.batch_jobs = SimpleNamespace(update=_update_job)
    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)

    retry_submitted = await service._process_reconciled_audio_job(
        workflow,
        job,
        SimpleNamespace(),
        _BatchRunner(),
    )

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert retry_submitted is False
    assert calls["retry_called"] is False
    assert job.status == StoryBatchJobStatus.FAILED
    assert job.missing_keys == ["page_1"]
    assert step.status == StepStatus.FAILED
    assert workflow.status == CustomStoryWorkflowStatus.FAILED
    assert workflow.error_message == "Missing audio keys: page_1"
    assert calls["committed"] is True


@pytest.mark.asyncio
async def test_reconcile_failed_batch_job_marks_matching_step_and_workflow_failed():
    workflow = _workflow(processing_mode="delayed")
    job = SimpleNamespace(
        id=uuid4(),
        workflow_id=workflow.id,
        story_id=None,
        job_type=StoryBatchJobType.AUDIO,
        status=StoryBatchJobStatus.SUBMITTED,
        provider_job_name="batches/audio",
        provider_state=None,
        error_message=None,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.steps = _StepRepo()
    calls = {}

    async def _get_workflow(workflow_id):
        assert workflow_id == workflow.id
        return workflow

    async def _update_job(job_arg):
        calls["job"] = job_arg
        return job_arg

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["committed"] = True

    class _Batches:
        async def get(self, name):
            assert name == job.provider_job_name
            return SimpleNamespace(state=SimpleNamespace(name="JOB_STATE_FAILED"), error="Google failed")

    class _BatchRunner:
        SUCCEEDED_STATES = {"JOB_STATE_SUCCEEDED"}
        CANCELLED_STATES = {"JOB_STATE_CANCELLED"}
        FAILED_STATES = {"JOB_STATE_FAILED"}
        google_client = SimpleNamespace(aio=SimpleNamespace(batches=_Batches()))

        @staticmethod
        def _job_state_name(provider_job):
            return provider_job.state.name

    service.workflows = SimpleNamespace(get_by_id=_get_workflow, update=_update_workflow)
    service.batch_jobs = SimpleNamespace(update=_update_job)
    service.session = SimpleNamespace(commit=_commit)
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    result = await service._reconcile_batch_job(job)

    step = _step_by_name(service.steps, CustomStoryWorkflowStep.NARRATION_GENERATION)
    assert result["action"] == "failed"
    assert job.status == StoryBatchJobStatus.FAILED
    assert step.status == StepStatus.FAILED
    assert workflow.status == CustomStoryWorkflowStatus.FAILED
    assert workflow.current_step == CustomStoryWorkflowStep.NARRATION_GENERATION.value


@pytest.mark.asyncio
async def test_delayed_outputs_wait_for_out_of_order_batch_completion():
    workflow = _workflow(
        processing_mode="delayed",
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "audio_url": "https://cdn.test/page.wav",
                    "duration": 2.5,
                    "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                }
            ]
        },
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    batches = _BatchRepo()
    batches.add(workflow.id, StoryBatchJobType.IMAGE, status=StoryBatchJobStatus.SUBMITTED)
    batches.add(workflow.id, StoryBatchJobType.AUDIO, status=StoryBatchJobStatus.SUCCEEDED)
    service.batch_jobs = batches

    assert await service._delayed_outputs_completed(workflow) is False
    assert await service._delayed_waiting_step(workflow) == CustomStoryWorkflowStep.IMAGE_GENERATION.value

    batches.by_type[StoryBatchJobType.IMAGE].status = StoryBatchJobStatus.SUCCEEDED

    assert await service._delayed_outputs_completed(workflow) is True


@pytest.mark.asyncio
async def test_delayed_run_publishes_after_enabled_batch_jobs_complete():
    workflow = _workflow(
        processing_mode="delayed",
        story_plan_json={"pages": [{"page_number": 1}]},
        story_plan_validated=True,
        story_json={
            "cover_image_url": "https://cdn.test/cover.png",
            "pages": [
                    {
                        "page_number": 1,
                        "text": "Mira listened.",
                        "image_url": "https://cdn.test/page.png",
                        "audio_url": "https://cdn.test/page.wav",
                        "duration": 2.5,
                        "word_timestamps": [{"word": "Mira", "start": 0, "end": 1}],
                    }
                ],
            },
        image_plan_json={"cover": {"image_prompt": "cover"}, "pages": [{"page_number": 1, "image_prompt": "page"}]},
        image_plan_validated=True,
    )
    story_id = uuid4()
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    batches = _BatchRepo()
    batches.add(workflow.id, StoryBatchJobType.IMAGE, status=StoryBatchJobStatus.SUCCEEDED)
    batches.add(workflow.id, StoryBatchJobType.AUDIO, status=StoryBatchJobStatus.SUCCEEDED)
    calls = {}

    async def _get_workflow(workflow_id):
        assert workflow_id == workflow.id
        return workflow

    async def _update_workflow(workflow_arg):
        calls["workflow"] = workflow_arg
        return workflow_arg

    async def _commit():
        calls["commits"] = calls.get("commits", 0) + 1

    async def _rollback():
        calls["rollback"] = True

    async def _publish(workflow_arg):
        calls["published"] = workflow_arg.id
        workflow_arg.story_id = story_id

    async def _notify(workflow_arg):
        calls["notified"] = workflow_arg.id

    class _Runner:
        async def _ensure_story_ai_config(self, story):
            calls["ensure_config"] = story.id

    service.workflows = SimpleNamespace(get_by_id_for_update=_get_workflow, update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit, rollback=_rollback)
    service.steps = _StepRepo()
    service.batch_jobs = batches
    service._story_runner = lambda workflow_arg: _Runner()
    service._publish_story = _publish
    service._send_completion_notifications = _notify

    result = await service.run(workflow.id)

    assert result.status == CustomStoryWorkflowStatus.COMPLETED
    assert result.current_step is None
    assert result.story_id == story_id
    assert calls["published"] == workflow.id
    assert calls["notified"] == workflow.id


@pytest.mark.asyncio
async def test_send_completion_notifications_uses_published_story(monkeypatch):
    story_id = uuid4()
    workflow = _workflow(
        story_id=story_id,
        story_json={"title": "Mira's Map", "pages": [{"page_number": 1, "text": "Mira listened."}]},
    )
    story = SimpleNamespace(id=story_id, user_id=workflow.user_id, title="Mira's Map")
    calls = {}

    class _Stories:
        async def get_by_id(self, requested_story_id):
            calls["requested_story_id"] = requested_story_id
            return story

    class _CompletionService:
        def __init__(self, session):
            calls["session"] = session

        async def send_story_completed(self, story_arg, story_json_arg):
            calls["story"] = story_arg
            calls["story_json"] = story_json_arg

    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    service.stories = _Stories()
    service.session = SimpleNamespace(name="session")

    monkeypatch.setattr(custom_story_workflow_service, "StoryCompletionEmailService", _CompletionService)

    await service._send_completion_notifications(workflow)

    assert calls["requested_story_id"] == story_id
    assert calls["session"] is service.session
    assert calls["story"] is story
    assert calls["story_json"] == workflow.story_json
