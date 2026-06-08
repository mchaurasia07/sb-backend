from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.entity.custom_story_workflow import CustomStoryWorkflowStatus, CustomStoryWorkflowStep
from app.entity.story_step import StepStatus
from app.service.custom_story_workflow_service import CustomStoryWorkflowService


def _workflow(**overrides):
    data = {
        "id": uuid4(),
        "user_id": uuid4(),
        "child_id": uuid4(),
        "story_id": None,
        "generation_mode": "INPUT_DRIVEN",
        "processing_mode": "instant",
        "age_group": SimpleNamespace(value="3-6"),
        "category": "adventure",
        "learning_goal": "listening",
        "context": "moon bell",
        "event_description": None,
        "status": CustomStoryWorkflowStatus.PENDING,
        "current_step": None,
        "error_message": None,
        "input_request": {
            "mode": "INPUT_DRIVEN",
            "category": "adventure",
            "skip_image_generation": False,
            "skip_validation": False,
        },
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
async def test_skipped_image_generation_creates_completed_step_record():
    workflow = _workflow(
        input_request={"skip_image_generation": True, "skip_validation": False},
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
            return SimpleNamespace(id=uuid4())

    service.workflows = SimpleNamespace(update=_update_workflow)
    service.session = SimpleNamespace(commit=_commit)
    service._batch_runner = lambda workflow_arg: _BatchRunner()

    await service._execute_step(
        SimpleNamespace(),
        workflow,
        CustomStoryWorkflowStep.IMAGE_GENERATION,
    )

    assert calls["batch_submit"] == (workflow.id, workflow.story_json, workflow.image_plan_json)
    assert workflow.current_step == CustomStoryWorkflowStep.IMAGE_GENERATION.value
    assert workflow.status == CustomStoryWorkflowStatus.IN_PROGRESS
