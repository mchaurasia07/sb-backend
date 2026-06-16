from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from starlette.responses import Response
from pydantic import ValidationError

from app.core.exceptions import AppException
from app.entity.custom_story_workflow import CustomStoryWorkflowStatus, CustomStoryWorkflowStep
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus
from app.model.request.story import ReaderCategory, StoryGenerationRequest, age_group_for_reader_category
from app.model.response.custom_story_workflow import CustomStoryWorkflowResponse
from app.routes.v1 import stories as story_routes
from app.service import custom_story_workflow_service
from app.service.custom_story_workflow_service import CustomStoryWorkflowService


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
async def test_list_batch_jobs_returns_paginated_typed_response_with_filters():
    user_id = uuid4()
    workflow_id = uuid4()
    job = _batch_job(workflow_id=workflow_id, status=StoryBatchJobStatus.RUNNING)
    calls = {}

    class _BatchJobs:
        async def list_for_user(self, user_id_arg, *, page, page_size, workflow_id=None, status=None):
            calls["user_id"] = user_id_arg
            calls["page"] = page
            calls["page_size"] = page_size
            calls["workflow_id"] = workflow_id
            calls["status"] = status
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
async def test_create_persists_workflow_before_safety_llm(monkeypatch):
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Growing Reader",
        category="adventure",
        learning_goal="kindness",
        context="A gentle story about helping.",
        skip_image_generation=True,
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    created = {}

    async def _validate_should_not_run(_self, _payload):
        raise AssertionError("safety LLM should run in background workflow, not create API")

    async def _get_child(user_id, child_id):
        return SimpleNamespace(id=child_id, dob=date(2018, 1, 1), character_image_url="https://cdn.test/child.png")

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
        created["committed"] = True

    monkeypatch.setattr(custom_story_workflow_service.settings, "CUSTOM_STORY_EXECUTE_WORKFLOW_DEFAULT", False)
    monkeypatch.setattr(custom_story_workflow_service.StoryInputSafetyService, "validate", _validate_should_not_run)
    service.children = SimpleNamespace(get_for_user=_get_child)
    service.workflows = SimpleNamespace(create=_create_workflow)
    service.session = SimpleNamespace(commit=_commit)

    response = await service.create(uuid4(), payload)

    assert response.workflow_id
    assert response.request_number == 1
    assert created["committed"] is True
    assert created["request_number"] == 1
    assert created["age_group"] == "6-9"
    assert created["reader_category"] == "Growing Reader"
    assert created["use_child_character"] is False
    assert created["execute_image"] is False
    assert created["execute_narration"] is True
    assert created["skip_validation"] is False
    assert created["execute_workflow"] is False
    assert created["context"] == "A gentle story about helping."
    assert response.reader_category == "Growing Reader"
    assert response.age_group == "6-9"


@pytest.mark.asyncio
async def test_create_allows_imagined_cast_without_child_character_image():
    payload = StoryGenerationRequest(
        child_id=uuid4(),
        reader_category="Early Reader",
        category="adventure",
    )
    service = CustomStoryWorkflowService.__new__(CustomStoryWorkflowService)
    created = {}

    async def _get_child(user_id, child_id):
        return SimpleNamespace(id=child_id, dob=date(2019, 1, 1), character_image_url=None)

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

    service.children = SimpleNamespace(get_for_user=_get_child)
    service.workflows = SimpleNamespace(create=_create_workflow)
    service.session = SimpleNamespace(commit=_commit)

    response = await service.create(uuid4(), payload)

    assert response.workflow_id
    assert created["processing_mode"] == "delayed"
    assert created["use_child_character"] is False


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
        def __init__(self, session):
            calls["session"] = session

        async def create(self, requested_user_id, requested_payload):
            calls["create"] = (requested_user_id, requested_payload)
            return response_data

    monkeypatch.setattr(story_routes, "CustomStoryWorkflowService", _FakeCustomStoryWorkflowService)
    background_tasks = BackgroundTasks()
    route_response = Response()

    response = await story_routes.create_custom_story_workflow(
        payload,
        background_tasks,
        route_response,
        SimpleNamespace(id=user_id),
        object(),
    )

    assert response.data == response_data
    assert response.message == "Custom story workflow saved successfully; execution skipped"
    assert route_response.status_code == 201
    assert background_tasks.tasks == []
    assert calls["create"] == (user_id, payload)


@pytest.mark.asyncio
async def test_create_custom_story_workflow_queues_background_when_requested(monkeypatch):
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
        def __init__(self, session):
            self.session = session

        async def create(self, requested_user_id, requested_payload):
            assert requested_user_id == user_id
            assert requested_payload == payload
            return response_data

    monkeypatch.setattr(story_routes, "CustomStoryWorkflowService", _FakeCustomStoryWorkflowService)
    background_tasks = BackgroundTasks()
    route_response = Response()

    response = await story_routes.create_custom_story_workflow(
        payload,
        background_tasks,
        route_response,
        SimpleNamespace(id=user_id),
        object(),
    )

    assert response.data == response_data
    assert response.message == "Custom story workflow started successfully"
    assert route_response.status_code == 202
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is story_routes.execute_custom_story_workflow_background
    assert background_tasks.tasks[0].args == (workflow_id,)


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
async def test_publish_story_creates_final_story_and_sets_workflow_story_id():
    workflow = _workflow(
        title="The Moon Bell",
        summary="A child listens carefully.",
        moral="Listening helps.",
        story_json={
            "cover_image_url": "https://cdn.test/cover.png",
            "pages": [{"page_number": 1, "text": "Mira listened.", "image_url": "https://cdn.test/page.png"}],
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
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
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
        story_json={"pages": [{"page_number": 1, "text": "Mira listened."}]},
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
