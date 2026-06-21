import base64
from datetime import UTC, datetime
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import mysql

from app.core.exceptions import AppException
from app.core.config import settings
from app.entity.generic_story_workflow import GenericStoryWorkflow, GenericStoryWorkflowStep
from app.entity.story_batch_job import StoryBatchJobStatus, StoryBatchJobType
from app.entity.story_step import StepStatus
from app.model.request.generic_story_workflow import (
    GenericStoryWorkflowCreateRequest,
    GenericStoryWorkflowExecuteRequest,
    GenericStoryWorkflowRetryRequest,
)
from app.model.response.generic_story_workflow import GenericStoryWorkflowListResponse, GenericStoryWorkflowResponse
from app.repository.generic_story_workflow_repository import GenericStoryWorkflowRepository
from app.routes.v1 import generic_stories as generic_story_routes
from app.service.generic_story_batch_service import GenericStoryBatchService
from app.service.generic_story_workflow_service import (
    STORY_LANGUAGE_VARIANTS_KEY,
    GenericStoryWorkflowService,
    _repair_json,
)
from app.service.story_narration_profile import normalize_page_emotion


def _locked_visual_character(name: str, *, design_fingerprint: str) -> dict:
    return {
        "name": name,
        "role": "hero",
        "appearance": {
            "age": "seven years old",
            "height_build": "short child, slim build",
            "skin_tone": "warm brown skin",
            "face_shape": "round face",
            "eye_shape": "large almond eyes",
            "eye_color": "dark brown",
            "hair": {"color": "black", "length": "short", "style": "neatly combed bob"},
            "outfit": {
                "type": "cotton kurta",
                "primary_color": "blue",
                "secondary_color": "white",
                "pattern": "plain fabric",
            },
            "footwear": "brown sandals",
            "accessories": ["small silver bracelet always present"],
        },
        "locks": {
            "face_lock": "round face, warm brown skin, large almond dark brown eyes",
            "hair_lock": "short black neatly combed bob",
            "outfit_lock": "blue cotton kurta, white leggings, and brown sandals",
            "accessory_lock": "small silver bracelet always present; no random accessories",
        },
        "forbidden_variations": ["pink dress", "pigtails", "missing silver bracelet"],
        "design_fingerprint": design_fingerprint,
    }


def test_generic_story_workflow_has_user_created_at_index():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in GenericStoryWorkflow.__table__.indexes
    }

    assert indexes["ix_generic_story_workflows_user_created_at"] == ("user_id", "created_at")
    assert indexes["ix_generic_story_workflows_user_story_created_at"] == (
        "user_id",
        "generic_story_id",
        "created_at",
    )


def test_generic_story_workflow_create_routes_support_singular_and_plural_paths():
    route_methods: dict[str, set[str]] = {"/workflow": set(), "/workflows": set()}
    for route in generic_story_routes.router.routes:
        path = getattr(route, "path", None)
        if path in route_methods:
            route_methods[path].update(getattr(route, "methods", set()))

    assert "POST" in route_methods["/workflow"]
    assert "POST" in route_methods["/workflows"]


def test_generic_story_workflow_events_route_is_not_registered():
    route_methods: set[str] = set()
    for route in generic_story_routes.router.routes:
        if getattr(route, "path", None) == "/workflows/{workflow_id}/events":
            route_methods.update(getattr(route, "methods", set()))

    assert "GET" not in route_methods


def _generic_steps_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260614_0052_add_generic_story_workflow_steps.py"
    )
    spec = importlib.util.spec_from_file_location("generic_story_workflow_steps_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeGenericWorkflowSteps:
    def __init__(self):
        self.records = []

    async def create(self, workflow_id, step_name, retry_count=0):
        record = SimpleNamespace(
            id=uuid4(),
            workflow_id=workflow_id,
            step_name=GenericStoryWorkflowStep(step_name),
            status=StepStatus.PENDING,
            input_json=None,
            prompt=None,
            output_json=None,
            error_message=None,
            retry_count=retry_count,
            started_at=None,
            completed_at=None,
            created_at=datetime.now(UTC),
        )
        self.records.append(record)
        return record

    async def latest_for_workflow_step(self, workflow_id, step_name):
        matching = [
            record
            for record in self.records
            if record.workflow_id == workflow_id and record.step_name == GenericStoryWorkflowStep(step_name)
        ]
        return matching[-1] if matching else None

    async def list_by_workflow(self, workflow_id):
        return [record for record in self.records if record.workflow_id == workflow_id]

    async def update(self, step):
        return step


@pytest.mark.asyncio
async def test_seed_step_records_creates_pending_rows_for_ordered_steps():
    workflow = SimpleNamespace(id=uuid4())
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.steps = _FakeGenericWorkflowSteps()

    await service._seed_step_records(workflow)

    assert [record.step_name for record in service.steps.records] == service.ORDERED_STEPS
    assert all(record.status == StepStatus.PENDING for record in service.steps.records)


@pytest.mark.asyncio
async def test_seed_step_records_only_creates_missing_rows():
    workflow = SimpleNamespace(id=uuid4())
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.steps = _FakeGenericWorkflowSteps()
    existing = await service.steps.create(workflow.id, GenericStoryWorkflowStep.IMAGE_GENERATION.value)

    await service._seed_step_records(workflow)

    assert len(service.steps.records) == len(service.ORDERED_STEPS)
    assert service.steps.records.count(existing) == 1
    assert [record.step_name for record in service.steps.records] == [
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.CHARACTER_EXTRACTION,
        GenericStoryWorkflowStep.SCENE_PLAN_GENERATION,
        GenericStoryWorkflowStep.VISUAL_BIBLE_GENERATION,
        GenericStoryWorkflowStep.STORY_GENERATION,
        GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        GenericStoryWorkflowStep.NARRATION_GENERATION,
        GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
    ]


def test_generic_workflow_steps_migration_marks_completed_legacy_workflow_steps():
    migration = _generic_steps_migration()
    workflow = {
        "id": str(uuid4()),
        "status": "COMPLETED",
        "current_step": None,
        "generic_story_id": str(uuid4()),
        "title": "The Moon Bell",
        "cover_image": "https://cdn.example.test/cover.png",
        "character_analysis_json": {"characters": [{"name": "Mira"}]},
        "scene_plan_json": {"pages": [{"page_number": 1}]},
        "visual_bible_json": {"characters": [{"name": "Mira"}]},
        "story_json": {
            "title": "The Moon Bell",
            "cover_image_url": "https://cdn.example.test/cover.png",
            "pages": [
                {
                    "page_number": 1,
                    "image_url": "https://cdn.example.test/page-1.png",
                    "audio_url": "https://cdn.example.test/page-1.wav",
                }
            ],
        },
        "image_plan_json": {"cover": {"image_prompt": "Cover prompt."}},
    }

    statuses = {step: migration._backfilled_step_status(workflow, step) for step in migration.ORDERED_STEPS}

    assert set(statuses.values()) == {"COMPLETED"}
    assert migration._backfilled_step_output(workflow, "VISUAL_BIBLE_GENERATION")["characters"][0]["name"] == "Mira"
    assert migration._backfilled_step_output(workflow, "PUBLISH_GENERIC_STORY")["title"] == "The Moon Bell"


def test_generic_workflow_steps_migration_marks_failed_and_submitted_image_steps():
    migration = _generic_steps_migration()
    workflow = {
        "id": str(uuid4()),
        "status": "FAILED",
        "current_step": "IMAGE_GENERATION",
        "error_message": "image batch failed",
        "story_json": {"title": "The Moon Bell", "pages": [{"page_number": 1}]},
        "image_plan_json": {"pages": [{"page_number": 1}]},
    }

    assert migration._backfilled_step_status(workflow, "STORY_GENERATION") == "COMPLETED"
    assert migration._backfilled_step_status(workflow, "IMAGE_PLAN_GENERATION") == "COMPLETED"
    assert migration._backfilled_step_status(workflow, "IMAGE_GENERATION") == "FAILED"

    workflow["status"] = "IN_PROGRESS"
    workflow["error_message"] = None

    assert migration._backfilled_step_status(workflow, "IMAGE_GENERATION") == "SUBMITTED_BATCH_JOB"

    workflow["story_json"]["pages"][0]["image_url"] = "https://cdn.example.test/page-1.png"

    assert migration._backfilled_step_status(workflow, "IMAGE_GENERATION") == "COMPLETED"


@pytest.mark.asyncio
async def test_execute_steps_persists_completed_step_record_output():
    workflow = _retry_workflow(status="PENDING", current_step=None, character_analysis_json=None)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.steps = _FakeGenericWorkflowSteps()

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _rollback():
        return None

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        assert step == GenericStoryWorkflowStep.CHARACTER_EXTRACTION
        workflow_arg.character_analysis_json = {
            "source_title": "The Moon Bell",
            "characters": [{"name": "Mira"}],
        }

    service.workflows = SimpleNamespace(update=_update)
    service.session = SimpleNamespace(commit=_commit, rollback=_rollback)
    service._execute_single_step = _execute_single_step
    service._log_workflow_event = lambda *args, **kwargs: None

    await service._execute_steps(
        workflow,
        [GenericStoryWorkflowStep.CHARACTER_EXTRACTION],
        payload=GenericStoryWorkflowExecuteRequest(),
        public_base_url="https://api.example.test",
        event_name="workflow_started",
        requested_step=GenericStoryWorkflowStep.CHARACTER_EXTRACTION.value,
    )

    step_record = service.steps.records[0]
    assert step_record.status == StepStatus.COMPLETED
    assert step_record.output_json["characters"][0]["name"] == "Mira"
    assert step_record.input_json["actual_story_chars"] > 0


def test_latest_workflow_lookup_uses_mysql_index_hint():
    statement = GenericStoryWorkflowRepository._latest_for_user_generic_story_id_statement(uuid4(), uuid4())
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "FORCE INDEX (ix_generic_story_workflows_user_story_created_at)" in sql
    assert "ORDER BY generic_story_workflows.created_at DESC" in sql
    assert "LIMIT" in sql


def test_workflow_list_lookup_does_not_select_large_json_columns():
    statement = GenericStoryWorkflowRepository._list_statement(user_id=uuid4(), page=1, page_size=20)
    sql = str(statement.compile(dialect=mysql.dialect()))

    assert "character_analysis_json" not in sql
    assert "scene_plan_json" not in sql
    assert "visual_bible_json" not in sql
    assert "story_json" not in sql
    assert "image_plan_json" not in sql
    assert "input_request" not in sql
    assert "generic_story_workflows.title" in sql
    assert "generic_story_workflows.status" in sql


def test_workflow_list_lookup_filters_title_case_insensitively():
    statement = GenericStoryWorkflowRepository._list_statement(
        title=" Moon ",
        page=1,
        page_size=20,
    )
    compiled = statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(compiled)

    assert "lower(generic_story_workflows.title) LIKE '%%moon%%'" in sql


def test_workflow_response_serializes_generic_story_id_like_db_value():
    generic_story_id = uuid4()
    workflow = SimpleNamespace(
        id=uuid4(),
        workflow_name="generic_story",
        status="COMPLETED",
        current_step=None,
        error_message=None,
        generic_story_id=generic_story_id,
        actual_story="A story with enough text.",
        age_group="3-6",
        language="en",
        requested_pages=10,
        title="Story",
        summary=None,
        theme=None,
        genre=None,
        moral=None,
        learning_goal=None,
        cover_image=None,
        character_analysis_json=None,
        scene_plan_json=None,
        visual_bible_json=None,
        story_json=None,
        image_plan_json=None,
        input_request=None,
        ai_provider="google",
        text_model="gemini",
        image_model="imagen",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    dumped = GenericStoryWorkflowResponse.model_validate(workflow).model_dump(mode="json")

    assert dumped["generic_story_id"] == str(generic_story_id)


def test_workflow_list_response_omits_large_json_payloads():
    fields = GenericStoryWorkflowListResponse.model_fields

    assert "character_analysis_json" not in fields
    assert "scene_plan_json" not in fields
    assert "visual_bible_json" not in fields
    assert "story_json" not in fields
    assert "image_plan_json" not in fields
    assert "input_request" not in fields
    assert "user_id" in fields
    assert "title" in fields
    assert "status" in fields


def test_workflow_create_request_has_illustration_type_without_requested_pages():
    fields = GenericStoryWorkflowCreateRequest.model_fields

    assert "illustration_type" in fields
    assert "requested_pages" not in fields


def test_generic_workflow_create_request_accepts_custom_story_payload_shape():
    request = GenericStoryWorkflowCreateRequest.model_validate(
        {
            "reader_category": "Early Reader",
            "category": "adventure",
            "learning_goal": "sharing and teamwork",
            "context": "A tiny moon bus helps children find a lost star.",
            "languages": ["en", "hi"],
            "execute_image": True,
            "execute_narration": True,
            "skip_validation": False,
            "execute_workflow": True,
        }
    )

    assert request.child_id is None
    assert request.age_group == "3-6"
    assert request.category == "adventure"
    assert request.context == "A tiny moon bus helps children find a lost star."
    assert request.language == "en"
    assert request.languages == ["en", "hi"]
    assert request.use_child_character is False


def test_workflow_execute_request_ignores_multi_image_mode_flag():
    default_request = GenericStoryWorkflowExecuteRequest.model_validate({})
    request = GenericStoryWorkflowExecuteRequest.model_validate({"multi_image_mode": False})
    typo_request = GenericStoryWorkflowExecuteRequest.model_validate({"mutil_image_mode": False})
    retry_request = GenericStoryWorkflowRetryRequest.model_validate({"mutil_image_mode": False})

    assert default_request.skip_image_generation is False
    assert default_request.skip_narration_generation is True
    assert not hasattr(default_request, "multi_image_mode")
    assert not hasattr(request, "multi_image_mode")
    assert not hasattr(typo_request, "multi_image_mode")
    assert not hasattr(retry_request, "multi_image_mode")
    assert retry_request.skip_narration_generation is True


def test_scene_plan_prompt_does_not_accept_requested_pages_override():
    prompt = Path("prompts/generic_story/scene_plan_prompt.txt").read_text(encoding="utf-8")

    assert "requested_pages" not in prompt
    assert "0-3 = 8-9 pages" in prompt
    assert "3-6 = 8-10 pages" in prompt
    assert "6-9 = 10-12 pages" in prompt
    assert "0-3 = 6-8 pages" not in prompt
    assert "6-9 = 10-11 pages" not in prompt
    assert "Your job is to plan scenes, not write final page prose" in prompt
    assert "adaptation_type, optimal page count" in prompt
    assert "If the story is too short" in prompt
    assert "If the story is too long" in prompt
    assert "0-3 = 10-25 words/page" in prompt
    assert "3-6 = 35-65 words/page" in prompt
    assert "6-9 = 50-90 words/page" in prompt
    assert "Use Character Analysis JSON as the canonical source for character names" in prompt
    assert "Do not use vague group labels such as" in prompt
    assert "Side characters, helpers, parents, grandparents, friends, classmates, animals, and antagonists" in prompt
    assert "Every visible named character must use the same canonical name across pages." in prompt
    assert "Actual Story may be raw copied text with dialogue" in prompt
    assert "Do not mechanically split the raw story by paragraph length." in prompt
    assert "scene boundaries, visual motifs, and visual storytelling needs" in prompt
    assert "# VISUAL STORYTELLING GUIDANCE" in prompt
    assert '"image_brief": ""' in prompt
    assert '"visual_direction": {' in prompt
    assert '"shot_type": "close-up|medium scene|wide scene|overhead view|group scene"' in prompt
    assert '"focal_subject": ""' in prompt
    assert "Interior story pages must not include title areas" in prompt
    assert "Readable text on interior pages is allowed only when it is a physical object inside the scene" in prompt
    assert "Story-page visual_direction, image_brief, and scene_summary must not include cover" in prompt
    assert '"key_visual_elements": []' in prompt
    assert "Every page must include image_brief and visual_direction." in prompt
    assert "technical film jargon" in prompt
    assert '"cinematography": {' not in prompt
    assert '"camera_arc": ""' not in prompt
    assert (
        "0-3 story_role values: introduction, exploration, observation, repetition, discovery, celebration, ending"
        in prompt
    )
    assert (
        "3-6 story_role values: setup, goal, problem, attempt, small_failure, improved_attempt, success, ending"
        in prompt
    )
    assert (
        "6-9 story_role values: setup, goal, problem, attempt, learning, planning, execution, climax, resolution"
        in prompt
    )
    assert "no villain, danger, forced conflict, forced failure, or distress" in prompt
    assert "no traditional climax required" in prompt
    assert "no meaningful failure required" in prompt
    assert "no complex problem solving" in prompt
    assert "driven by discovery, routine, comfort, repetition, or gentle surprise instead of conflict" in prompt
    assert "scene_summary should describe only the current page scene" in prompt
    assert "Do not summarize multiple future events into one page" in prompt
    assert "Each page should represent a single story moment" in prompt
    assert "sensory details" in prompt
    assert "merging similar actions" in prompt
    assert "Every page continuity object is mandatory" in prompt
    assert "Characters, goals, emotions, objects, and location states should carry forward" in prompt
    assert "Do not repeat continuity items that are no longer important to the story" in prompt
    assert "continuity.characters, continuity.objects, and continuity.location_state" in prompt
    assert '"story_role": "use only the selected age group' in prompt


def test_repair_json_closes_unterminated_string_and_containers():
    raw = '{"cover":{"title_text":"Moon Story","book_cover_prompt":"A child follows the moon'

    repaired = _repair_json(raw)

    assert json.loads(repaired) == {
        "cover": {
            "title_text": "Moon Story",
            "book_cover_prompt": "A child follows the moon",
        }
    }


def test_repair_json_strips_fence_and_trailing_text():
    raw = '```json\n{"pages":[{"page":1,"visual_focus":"Moon"}]}\n```\nextra text'

    repaired = _repair_json(raw)

    assert json.loads(repaired) == {"pages": [{"page": 1, "visual_focus": "Moon"}]}


def test_generic_scene_plan_page_count_ranges_match_prompt():
    assert GenericStoryWorkflowService._scene_plan_page_count_range("0-3") == (8, 9)
    assert GenericStoryWorkflowService._scene_plan_page_count_range("3-6") == (8, 10)
    assert GenericStoryWorkflowService._scene_plan_page_count_range("6-9") == (10, 12)
    assert GenericStoryWorkflowService._scene_plan_page_count_range("2-4") == (8, 9)
    assert GenericStoryWorkflowService._scene_plan_page_count_range("4-6") == (8, 10)
    assert GenericStoryWorkflowService._scene_plan_word_range("0-3") == "10-25 words/page"
    assert GenericStoryWorkflowService._scene_plan_word_range("3-6") == "35-65 words/page"
    assert GenericStoryWorkflowService._scene_plan_word_range("6-9") == "50-90 words/page"


def test_normalize_scene_plan_metadata_sets_word_range_and_page_continuity():
    plan = {
        "pages": [
            {
                "page": 1,
                "location": "garden",
                "characters": ["Mira", "Luma"],
            },
            {
                "page": 2,
                "location": "moon gate",
                "characters": ["Mira"],
                "continuity": {
                    "characters": ["Mira keeps holding the silver key."],
                    "objects": "bad-shape",
                },
            },
        ],
    }

    normalized = GenericStoryWorkflowService._normalize_scene_plan_metadata(plan, age_group="3-6")

    assert normalized["adaptation_strategy"]["selected_page_count"] == 2
    assert normalized["adaptation_strategy"]["selected_word_range"] == "35-65 words/page"
    assert normalized["pages"][0]["continuity"] == {
        "characters": ["Keep characters consistent: Mira, Luma."],
        "objects": [],
        "location_state": ["Continue location state: garden."],
    }
    assert normalized["pages"][1]["continuity"] == {
        "characters": ["Mira keeps holding the silver key."],
        "objects": [],
        "location_state": ["Continue location state: moon gate."],
    }


def test_story_generation_prompt_requires_natural_hindi_marathi_localization():
    prompt = Path("prompts/generic_story/story_generation_prompt.txt").read_text(encoding="utf-8")

    assert "Scene Plan Page Count:" in prompt
    assert "Scene Plan Page Numbers:" in prompt
    assert "Output pages.length must equal Scene Plan Page Count exactly." in prompt
    assert "Output page_number values must follow Scene Plan Page Numbers exactly" in prompt
    assert "Use the Scene Plan adaptation_strategy.selected_word_range when present." in prompt
    assert "Preserve each page's continuity.characters, continuity.objects, and continuity.location_state" in prompt
    assert (
        "Do not repeat scene_summary, main_action, source_connection, page_turn_hook, or continuity text directly"
        in prompt
    )
    assert "Write in modern spoken children's Hindi." in prompt
    assert "Write in modern spoken children's Marathi." in prompt
    assert "Do not write shuddh, Sanskrit-heavy, or textbook-style Hindi." in prompt
    assert "Do not write shuddh, Sanskrit-heavy, or textbook-style Marathi." in prompt
    assert "common English words are allowed" in prompt
    assert "school, bag, game, bus, train, toy, team, park, picnic, idea, sorry, thank you" in prompt
    assert "Keep Hindi grammar and sentence flow natural" in prompt
    assert "Keep Marathi grammar and sentence flow natural" in prompt
    assert "Do not overuse English or create random Hinglish." in prompt
    assert "Do not overuse English or create random Manglish." in prompt
    assert "Write each language independently from the Scene Plan." in prompt
    assert "Do NOT write English first and then translate it into Hindi or Marathi." in prompt
    assert "text.hi in Hindi using Devanagari script" in prompt
    assert "text.mr in Marathi using Devanagari script" in prompt
    assert "Do not leave Hindi fields in English." in prompt
    assert "Do not leave Marathi fields in English." in prompt
    assert "complete natural story sentences, not literal translations or fragments" in prompt
    assert "Would a 3-9 year old understand this when read aloud?" in prompt
    assert "Page emotion must reflect the Scene Plan page's dominant feeling. Do not reuse \"wonder\" on every page." in prompt
    assert "For the output page emotion field, use only one of these supported values:" in prompt
    assert "concerned/caring/tender -> kindness" in prompt
    assert "do not set every page to wonder" in prompt


def test_image_plan_prompt_uses_story_json_page_count_as_source_of_truth():
    prompt = Path("prompts/generic_story/image_plan_prompt.txt").read_text(encoding="utf-8")

    assert "Story JSON Page Count:" in prompt
    assert "Story JSON Page Numbers:" in prompt
    assert "Output pages.length must equal Story JSON Page Count exactly." in prompt
    assert "Output page values must equal Story JSON Page Numbers exactly" in prompt
    assert "Story JSON is the source of truth for page count" in prompt
    assert "Do not use age-band ranges, Scene Plan page count, or adaptation_strategy.selected_page_count" in prompt
    assert "Every visible named character, including side characters, must keep the same Visual Bible identity" in prompt
    assert "continuity_notes must include the character's Visual Bible locks" in prompt
    assert "Side characters must have the same level of continuity detail as the hero." in prompt
    assert "Do not change clothing, colors, hairstyle, face, body shape, age, accessories, or species" in prompt
    assert "Apply basic scene etiquette" in prompt
    assert "no outdoor shoes inside temples/prayer rooms/sacred spaces or on beds/bedding" in prompt
    assert "Every visible named character on every page has continuity_notes" in prompt
    assert "Use Scene Plan image_brief and visual_direction as source guidance" in prompt
    assert "Do not ignore scene_plan.pages[].image_brief or scene_plan.pages[].visual_direction" in prompt
    assert "Convert scene_plan.pages[].visual_direction into concrete image-plan camera_shot" in prompt
    assert "image-plan camera_shot, composition, visual_focus, environment, and continuity_notes must reflect it" in prompt
    assert '"allowed_in_scene_text": []' in prompt
    assert '"object_states": {}' in prompt
    assert "allowed_in_scene_text is the only place to request readable text on story pages" in prompt
    assert "Do not carry a recurring object into a page just because it appeared earlier or will appear later" in prompt
    assert "Do not list objects that are absent from this page" in prompt
    assert "object_states is mandatory for every visible important object" in prompt
    assert "Object state continuity is sequential and plausible across pages" in prompt
    assert "Sound effects are not readable image text unless explicitly listed in allowed_in_scene_text" in prompt
    assert "Page composition, visual_focus, environment, and continuity_notes must not mention title area" in prompt
    assert "Each page must show one clear visual moment" in prompt
    assert "not a split scene, montage, collage, sequence, or multi-vignette page" in prompt
    assert "premium finished illustration" in prompt
    assert "Each character listed in characters must appear exactly once" in prompt
    assert "Never describe floating heads, detached faces, cropped heads, or partial child/adult cutouts" in prompt
    assert "no duplicate heads, duplicate faces, floating heads, detached faces" in prompt
    assert "Do not use split scene, panel, panels, vignette, vignettes, montage, sequence, or collage" in prompt
    assert "do not spoil the final solution/outcome on the front cover" in prompt
    assert "scene_plan.cinematography" not in prompt


def test_character_and_image_generation_prompts_enforce_named_character_consistency():
    character_prompt = Path("prompts/generic_story/character_extraction_prompt.txt").read_text(encoding="utf-8")
    visual_bible_prompt = Path("prompts/generic_story/visual_bible_generator_prompt.txt").read_text(encoding="utf-8")
    image_prompt = Path("prompts/generic_story/image_generation_prompt.txt").read_text(encoding="utf-8")

    assert "Extract every named, recurring, or story-important visible character" in character_prompt
    assert "Do not collapse named characters into vague labels" in character_prompt
    assert "Side characters must receive the same consistency treatment as the hero." in character_prompt
    assert "Visual Diversity Seed:" in visual_bible_prompt
    assert "invent missing visual details exactly once" in visual_bible_prompt
    assert "Do not reuse the same repeated child look across stories." in visual_bible_prompt
    assert "Do not default every girl to the same pigtails, pink kurta" in visual_bible_prompt
    assert "Invented details are locked and reused by cover and all pages." in visual_bible_prompt
    assert "Important recurring objects must have ONE fixed visual identity." in visual_bible_prompt
    assert '"state_variables": []' in visual_bible_prompt
    assert '"forbidden_variations": []' in visual_bible_prompt
    assert '"visual_diversity_seed": "{visual_diversity_seed}"' in visual_bible_prompt
    assert '"cast_design_fingerprint": ""' in visual_bible_prompt
    assert '"design_fingerprint": ""' in visual_bible_prompt
    assert "Character consistency is mandatory for every visible character, not only the hero." in image_prompt
    assert "Do not replace a named character with a lookalike" in image_prompt
    assert "faceless named characters" in image_prompt
    assert "cropped heads" in image_prompt
    assert "Draw each named character listed in IMAGE PLAN.characters exactly once" in image_prompt
    assert "Do not carry a recurring/signature object into this page unless it is listed" in image_prompt
    assert "Natural incidental background details are allowed only when they are required by the listed environment" in image_prompt
    assert "Important object consistency is mandatory for objects listed in IMAGE PLAN.important_objects on this page." in image_prompt
    assert "Before drawing, silently check IMAGE PLAN.object_states." in image_prompt
    assert "water should rise gradually only on the pages where the plan says it rises" in image_prompt
    assert "No floating heads, detached heads, disembodied faces" in image_prompt
    assert "Respect basic scene etiquette" in image_prompt
    assert "do not draw outdoor shoes on feet" in image_prompt
    assert "This does not change the locked footwear design" in image_prompt
    assert "finished picture-book quality" in image_prompt
    assert "not a split scene, comic panel layout, montage, collage, or multiple vignettes" in image_prompt


def test_visual_diversity_seed_is_stable_per_workflow_and_varies_by_workflow():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        title="The Neem Tree",
        age_group="6-9",
        theme="kindness",
        genre="realistic",
        actual_story="A child learns from her grandmother under a neem tree.",
    )
    same_workflow = SimpleNamespace(**workflow.__dict__)
    different_workflow = SimpleNamespace(**{**workflow.__dict__, "id": uuid4()})

    assert GenericStoryWorkflowService._visual_diversity_seed(workflow) == GenericStoryWorkflowService._visual_diversity_seed(same_workflow)
    assert GenericStoryWorkflowService._visual_diversity_seed(workflow) != GenericStoryWorkflowService._visual_diversity_seed(different_workflow)


@pytest.mark.asyncio
async def test_generate_visual_bible_passes_and_stores_visual_diversity_seed(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        id=uuid4(),
        title="The Neem Tree",
        actual_story="Saavi talks with Grandma under a neem tree.",
        age_group="6-9",
        theme="kindness",
        genre="realistic",
        scene_plan_json={"pages": []},
        character_analysis_json={"chars": [{"name": "Saavi", "type": "human", "role": "hero"}]},
    )
    captured_prompt = {}

    async def _generate_json(prompt, *, max_tokens):
        captured_prompt["text"] = prompt
        return {
            "characters": [
                {
                    "name": "Saavi",
                    "role": "hero",
                    "appearance": {
                        "age": "seven years old",
                        "height_build": "short child, sturdy build",
                        "skin_tone": "warm brown skin",
                        "face_shape": "soft oval face",
                        "eye_shape": "wide almond eyes",
                        "eye_color": "dark brown",
                        "hair": {"color": "black", "length": "shoulder length", "style": "single side braid"},
                        "outfit": {"type": "cotton kurta", "primary_color": "leaf green", "secondary_color": "cream leggings", "pattern": "plain fabric"},
                        "footwear": "brown sandals",
                        "accessories": ["yellow hair clip always present"],
                    },
                    "locks": {
                        "face_lock": "soft oval face, warm brown skin, wide almond dark brown eyes",
                        "hair_lock": "black shoulder-length hair in one side braid with yellow hair clip",
                        "outfit_lock": "leaf green cotton kurta, cream leggings, and brown sandals",
                        "accessory_lock": "yellow hair clip always present; no random accessories",
                    },
                    "forbidden_variations": ["pigtails", "pink dress", "missing yellow hair clip"],
                }
            ],
            "locations": [],
            "important_objects": [],
        }

    monkeypatch.setattr(settings, "STORY_MOCK_LLM_RESPONSES", False)
    service._generate_json = _generate_json

    visual_bible = await service._generate_visual_bible(workflow)
    seed = GenericStoryWorkflowService._visual_diversity_seed(workflow)

    assert f"Visual Diversity Seed:\n{seed}" in captured_prompt["text"]
    assert visual_bible["visual_diversity_seed"] == seed
    assert visual_bible["characters"][0]["design_fingerprint"]
    assert visual_bible["cast_design_fingerprint"]


def test_validate_visual_bible_rejects_missing_character_locks():
    workflow = SimpleNamespace(
        actual_story="A child helps in a garden.",
        theme="kindness",
        genre="realistic",
        character_analysis_json={"chars": [{"name": "Mira", "type": "human", "role": "hero"}]},
        scene_plan_json={},
    )
    visual_bible = {
        "characters": [
            {
                "name": "Mira",
                "appearance": {"skin_tone": "warm brown", "face_shape": "round face"},
                "locks": {"face_lock": "round face"},
                "forbidden_variations": ["blue dress"],
            }
        ]
    }

    with pytest.raises(AppException) as exc_info:
        GenericStoryWorkflowService._validate_visual_bible_character_locks(visual_bible, workflow)

    assert exc_info.value.code == "GENERIC_VISUAL_BIBLE_CHARACTER_LOCKS_MISSING"


def test_validate_visual_bible_rejects_duplicate_human_design_fingerprints():
    workflow = SimpleNamespace(
        actual_story="Two classmates solve a puzzle.",
        theme="teamwork",
        genre="school",
        character_analysis_json={
            "chars": [
                {"name": "Mira", "type": "human", "role": "hero"},
                {"name": "Riya", "type": "human", "role": "friend"},
            ]
        },
        scene_plan_json={},
    )
    visual_bible = {
        "characters": [
            _locked_visual_character("Mira", design_fingerprint="same child design"),
            _locked_visual_character("Riya", design_fingerprint="same child design"),
        ]
    }

    with pytest.raises(AppException) as exc_info:
        GenericStoryWorkflowService._validate_visual_bible_character_locks(visual_bible, workflow)

    assert exc_info.value.code == "GENERIC_VISUAL_BIBLE_CHARACTER_DESIGNS_DUPLICATE"


def test_validate_visual_bible_allows_duplicate_fingerprints_when_story_justifies_matching():
    workflow = SimpleNamespace(
        actual_story="Twin sisters wear matching school uniforms for the race.",
        theme="teamwork",
        genre="school",
        character_analysis_json={
            "chars": [
                {"name": "Mira", "type": "human", "role": "hero"},
                {"name": "Riya", "type": "human", "role": "friend"},
            ]
        },
        scene_plan_json={},
    )
    visual_bible = {
        "characters": [
            _locked_visual_character("Mira", design_fingerprint="same twin design"),
            _locked_visual_character("Riya", design_fingerprint="same twin design"),
        ]
    }

    GenericStoryWorkflowService._validate_visual_bible_character_locks(visual_bible, workflow)


def test_page_emotion_normalization_preserves_scene_specific_feelings():
    assert normalize_page_emotion("excited, happy, joyful") == "excitement"
    assert normalize_page_emotion("concerned, empathetic, worried") == "kindness"
    assert normalize_page_emotion("relieved, tender, responsible") == "calm"
    assert normalize_page_emotion("responsible, affectionate, joyful") == "determination"
    assert normalize_page_emotion("amused, joyful, heartwarming") == "playfulness"
    assert normalize_page_emotion("overwhelmed joy, surprise, love") == "triumph"
    assert normalize_page_emotion("content, peaceful, loving, reflective") == "calm"


def test_normalize_story_json_uses_scene_plan_emotion_when_story_emotion_is_generic():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="6-9",
        language="en",
        scene_plan_json={
            "title": "Zoya and the Birthday Puppy",
            "summary": "Zoya helps a puppy.",
            "pages": [
                {"page": 1, "emotion": "hopeful, longing, understanding"},
                {"page": 2, "emotion": "excited, happy, joyful"},
                {"page": 3, "emotion": "concerned, empathetic, worried"},
                {"page": 4, "emotion": "content, peaceful, loving, reflective"},
            ],
        },
    )
    raw = {
        "title": {"en": "Zoya and the Birthday Puppy", "hi": "Hindi", "mr": "Marathi"},
        "summary": {"en": "Zoya helps a puppy.", "hi": "Hindi", "mr": "Marathi"},
        "pages": [
            {"page_number": 1, "emotion": "wonder", "text": {"en": "Page 1.", "hi": "Hindi 1.", "mr": "Marathi 1."}},
            {"page_number": 2, "emotion": "wonder", "text": {"en": "Page 2.", "hi": "Hindi 2.", "mr": "Marathi 2."}},
            {"page_number": 3, "emotion": "wonder", "text": {"en": "Page 3.", "hi": "Hindi 3.", "mr": "Marathi 3."}},
            {"page_number": 4, "emotion": "wonder", "text": {"en": "Page 4.", "hi": "Hindi 4.", "mr": "Marathi 4."}},
        ],
        "moral": {"en": "Kindness matters.", "hi": "Hindi", "mr": "Marathi"},
    }

    normalized = service._normalize_story_json(raw, workflow)

    assert [page["emotion"] for page in normalized["pages"]] == ["confidence", "excitement", "kindness", "calm"]


@pytest.mark.asyncio
async def test_generate_story_json_prompt_includes_scene_page_count_and_numbers():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    captured = {}

    async def _generate_json(prompt, *, max_tokens):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        return {
            "title": {"en": "The Moon Bell", "hi": "Chaand ki Ghanti", "mr": "Chandrachi Ghanta"},
            "summary": {"en": "Mira listens.", "hi": "Mira listens.", "mr": "Mira listens."},
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": {"en": "Mira listened.", "hi": "Mira listened.", "mr": "Mira listened."},
                },
                {
                    "page_number": 2,
                    "emotion": "joy",
                    "text": {"en": "The bell rang.", "hi": "The bell rang.", "mr": "The bell rang."},
                },
            ],
            "moral": {"en": "Listen carefully.", "hi": "Listen carefully.", "mr": "Listen carefully."},
        }

    service._generate_json = _generate_json
    workflow = SimpleNamespace(
        actual_story="Mira helps the moon bell.",
        age_group="3-6",
        language="en",
        title="The Moon Bell",
        character_analysis_json={"characters": [{"name": "Mira"}]},
        scene_plan_json={"pages": [{"page": 1}, {"page": 2}]},
        visual_bible_json={"characters": [{"name": "Mira"}]},
    )

    await service._generate_story_json(workflow)

    assert "Scene Plan Page Count:\n2" in captured["prompt"]
    assert "Scene Plan Page Numbers:\n[1, 2]" in captured["prompt"]
    assert captured["max_tokens"] == 24000


@pytest.mark.asyncio
async def test_generate_image_plan_prompt_includes_story_page_count_and_numbers():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    captured = {}

    async def _generate_json(prompt, *, max_tokens):
        captured["prompt"] = prompt
        captured["max_tokens"] = max_tokens
        return {
            "cover": {},
            "pages": [
                {"page": 1, "visual_focus": "Mira listens.", "camera_shot": "medium", "composition": "Mira centered.", "continuity_notes": []},
                {"page": 2, "visual_focus": "The bell rings.", "camera_shot": "wide", "composition": "Bell above Mira.", "continuity_notes": []},
            ],
        }

    service._generate_json = _generate_json
    service._workflow_visual_bible = lambda workflow: {"characters": [{"name": "Mira"}]}
    service._workflow_illustration_style = lambda workflow: "Premium storybook style."
    service._validate_and_normalize_image_cover_plan = lambda image_plan, workflow: None
    workflow = SimpleNamespace(
        scene_plan_json={"pages": [{"page": 1}, {"page": 2}]},
        visual_bible_json={"characters": [{"name": "Mira"}]},
        story_json={
            "title": "The Moon Bell",
            "summary": "Mira listens.",
            "moral": "Listen carefully.",
            "pages": [
                {"page_number": 1, "emotion": "wonder", "text": "Mira listened."},
                {"page_number": 2, "emotion": "joy", "text": "The bell rang."},
            ],
        },
    )

    await service._generate_image_plan(workflow)

    assert "Story JSON Page Count:\n2" in captured["prompt"]
    assert "Story JSON Page Numbers:\n[1, 2]" in captured["prompt"]
    assert "Story JSON is the source of truth for page count" in captured["prompt"]
    assert '"allowed_in_scene_text": []' in captured["prompt"]
    assert captured["max_tokens"] == 16000


def test_workflow_log_event_uses_consistent_ids_and_fields(caplog):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow_id = uuid4()
    generic_story_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        generic_story_id=generic_story_id,
        current_step="STORY_GENERATION",
        status="IN_PROGRESS",
    )

    with caplog.at_level(logging.INFO, logger="app.service.generic_story_workflow_service"):
        service._log_workflow_event(
            "step_started",
            workflow,
            step=GenericStoryWorkflowStep.STORY_GENERATION,
            reason="manual run",
            page_number=2,
        )

    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert message.startswith("generic_story_workflow event=step_started")
    assert f"workflow_id={workflow_id}" in message
    assert f"generic_story_id={generic_story_id}" in message
    assert "step=STORY_GENERATION" in message
    assert "status=IN_PROGRESS" in message
    assert 'reason="manual run"' in message
    assert "page_number=2" in message


def test_normalize_story_json_matches_existing_story_contract():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="3-6",
        scene_plan_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "moral_explanation": "Careful listening helps friends solve problems.",
            "pages": [{"page_number": 1}, {"page_number": 2}],
        },
    )

    story_json = service._normalize_story_json(
        {
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "pages": [
                {"page_number": 1, "emotion": "wonder", "text": "Mira heard the moon bell hum softly."},
                {"page_number": 2, "emotion": "joy", "text": "She listened, helped, and the bell rang again."},
            ],
            "moral": "Listening carefully can help us solve problems together.",
        },
        workflow,
    )

    public_story_json = service._story_json_without_language_variants(story_json)
    assert set(public_story_json) == {"title", "summary", "pages", "moral"}
    assert story_json["pages"][0]["page_number"] == 1
    assert story_json["pages"][0]["text"] == "Mira heard the moon bell hum softly."
    assert story_json["pages"][0]["narration"]["voice_style"] == "warm animated storyteller"
    assert story_json["moral"] == "Listening carefully can help us solve problems together."
    assert story_json[STORY_LANGUAGE_VARIANTS_KEY]["hi"]["pages"][0]["text"] == "Mira heard the moon bell hum softly."


def test_normalize_story_json_keeps_multilingual_page_text_variants():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="3-6",
        language="hi",
        scene_plan_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "moral_explanation": "Careful listening helps friends solve problems.",
            "pages": [{"page_number": 1}],
        },
    )

    story_json = service._normalize_story_json(
        {
            "title": {"en": "The Moon Bell", "hi": "चाँद की घंटी", "mr": "चंद्राची घंटा"},
            "summary": {
                "en": "Mira helps the moon bell.",
                "hi": "मीरा चाँद की घंटी की मदद करती है.",
                "mr": "मीरा चंद्राच्या घंटेची मदत करते.",
            },
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": {
                        "en": "Mira heard the moon bell hum softly.",
                        "hi": "मीरा ने चाँद की घंटी को धीरे से गुनगुनाते सुना.",
                        "mr": "मीराने चंद्राची घंटा अलगद गुणगुणताना ऐकली.",
                    },
                }
            ],
            "moral": {
                "en": "Listening carefully helps friends.",
                "hi": "ध्यान से सुनना दोस्तों की मदद करता है.",
                "mr": "काळजीपूर्वक ऐकल्याने मित्रांना मदत होते.",
            },
        },
        workflow,
    )

    assert story_json["title"] == "चाँद की घंटी"
    assert story_json["pages"][0]["text"] == "मीरा ने चाँद की घंटी को धीरे से गुनगुनाते सुना."
    assert story_json[STORY_LANGUAGE_VARIANTS_KEY]["en"]["pages"][0]["text"] == "Mira heard the moon bell hum softly."
    assert story_json[STORY_LANGUAGE_VARIANTS_KEY]["mr"]["moral"] == "काळजीपूर्वक ऐकल्याने मित्रांना मदत होते."


def test_normalize_story_json_rejects_scene_page_count_mismatch():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(age_group="3-6", scene_plan_json={"pages": [{"page_number": 1}, {"page_number": 2}]})

    with pytest.raises(AppException, match="expected 2"):
        service._normalize_story_json(
            {
                "pages": [
                    {"page_number": 1, "emotion": "wonder", "text": "One page only."},
                ]
            },
            workflow,
        )


def test_generate_dummy_images_saves_prompts_and_data_urls():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        title="Journey to Mars",
        cover_image=None,
        story_json={
            "title": "Journey to Mars",
            "pages": [
                {"page_number": 1, "text": "Mira sees Mars."},
                {"page_number": 2, "text": "Mira returns home."},
            ],
        },
        image_plan_json={
            "visual_bible": {
                "characters": [
                    {
                        "name": "Mira",
                        "appearance": "Round face, short black hair, yellow dress.",
                    }
                ]
            },
            "cover": {"image_prompt": "Cover prompt with Mars and title."},
            "pages": [
                {"page_number": 1, "image_prompt": "Page 1 Mars prompt."},
                {"page_number": 2, "image_prompt": "Page 2 Earth prompt."},
            ],
        },
    )

    service._generate_dummy_images(workflow)

    assert workflow.cover_image.startswith("data:image/png;base64,")
    assert workflow.story_json["cover_image_url"] == workflow.cover_image
    assert "CHARACTER AND CONTINUITY MODEL SHEET" in workflow.story_json["cover_image_prompt"]
    assert "Round face, short black hair, yellow dress." in workflow.story_json["cover_image_prompt"]
    assert workflow.story_json["cover_planned_image_prompt"] == "Cover prompt with Mars and title."
    assert workflow.story_json["cover_image_dummy"] is True
    assert workflow.story_json["pages"][0]["image_url"].startswith("data:image/png;base64,")
    assert "CHARACTER AND CONTINUITY MODEL SHEET" in workflow.story_json["pages"][0]["image_prompt"]
    assert "Page 1 Mars prompt." in workflow.story_json["pages"][0]["image_prompt"]
    assert workflow.story_json["pages"][0]["planned_image_prompt"] == "Page 1 Mars prompt."
    assert workflow.story_json["pages"][0]["image_dummy"] is True


def test_render_image_prompt_uses_page_scoped_visual_context():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_image_prompt(
        page_type="story_page",
        story_title="The Moon Bell",
        visual_bible={
            "style": "Premium storybook style.",
            "age_group": "3-6",
            "characters": [
                {
                    "name": "Mira",
                    "role": "hero",
                    "anchor": "Mira anchor.",
                    "appearance": {
                        "age": "eight years old",
                        "height_build": "small child, slim build",
                        "skin_tone": "warm medium brown skin",
                        "face_shape": "round face",
                        "eye_shape": "large almond eyes",
                        "eye_color": "dark brown",
                        "hair": {"color": "black", "length": "short", "style": "bob"},
                        "outfit": {"type": "dress", "primary_color": "yellow", "pattern": "plain"},
                        "footwear": "white sneakers",
                        "accessories": ["red bracelet always present"],
                    },
                    "locks": {
                        "face_lock": "round face",
                        "hair_lock": "short black bob",
                        "outfit_lock": "plain yellow dress",
                        "accessory_lock": "red bracelet always present",
                    },
                    "forbidden_variations": ["blue dress", "long hair"],
                },
                {
                    "name": "Rohan",
                    "anchor": "Rohan anchor.",
                    "appearance": "green kurta",
                },
            ],
            "locations": [{"name": "garden", "visual_identity": "moonlit garden"}],
            "important_objects": [
                {"name": "moon bell", "description": "silver bell", "continuity_requirements": ["always silver"]},
                {"name": "red kite", "description": "small kite"},
            ],
        },
        page_image_plan={
            "page": 1,
            "visual_focus": "Mira rings the moon bell in the garden.",
            "composition": "Mira centered.",
            "environment": "moonlit garden",
            "emotion": "quiet wonder",
            "camera_shot": "medium",
            "characters": ["Mira"],
            "important_objects": ["moon bell"],
        },
    )

    assert "plain yellow dress" in prompt
    assert "moon bell" in prompt
    assert "SCENE TO DRAW:\nVisual focus: Mira rings the moon bell in the garden." in prompt
    assert "Action and composition: Mira centered." in prompt
    assert "Environment: moonlit garden" in prompt
    assert "Allowed characters only: Mira" in prompt
    assert "Required objects only: moon bell" in prompt
    assert prompt.index("SCENE TO DRAW:") < prompt.index("CHARACTER AND CONTINUITY MODEL SHEET:")
    assert "ILLUSTRATION STYLE:\nPremium storybook style." in prompt
    assert "AGE GROUP:\nEarly Reader (3-6 years)" in prompt
    assert "ASPECT RATIO:\n1:1" in prompt
    assert "Rohan anchor" not in prompt
    assert "red kite" not in prompt


def test_render_image_prompt_does_not_fallback_to_all_characters_when_page_lists_characters():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_image_prompt(
        page_type="story_page",
        story_title="Baby's Little World",
        visual_bible={
            "characters": [
                {"name": "Mama", "appearance": "soft teal kurta"},
                {"name": "Papa", "appearance": "light blue shirt"},
            ],
            "locations": [],
            "important_objects": [],
        },
        page_image_plan={
            "page": 3,
            "visual_focus": "Dadi holds Baby near flowers.",
            "characters": ["Dadi", "Baby"],
        },
    )

    assert "Allowed characters only: Dadi, Baby" in prompt
    assert "soft teal kurta" not in prompt
    assert "light blue shirt" not in prompt


def test_render_cover_prompt_prioritizes_exact_title_text():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_image_prompt(
        page_type="cover",
        story_title="Grandma's Rakhi Story",
        visual_bible={"characters": [], "locations": [], "important_objects": []},
        page_image_plan={
            "book_cover_prompt": "A finished front cover showing the whole Rakhi story promise.",
            "visual_focus": "Rohan and Meera smiling together.",
            "genre_signal": "warm family festival story",
            "composition": "Characters below a clear title area.",
            "title_layout": "Large title centered in the top third with clean space.",
        },
    )

    assert "This is a finished front book cover based on the whole story" in prompt
    assert "Overall cover direction: A finished front cover showing the whole Rakhi story promise." in prompt
    assert "Story promise and genre signal: warm family festival story" in prompt
    assert "Required title layout: Large title centered in the top third with clean space." in prompt
    assert "Do not copy page 1 or any interior page composition" in prompt
    assert 'Render this exact visible title text: "Grandma\'s Rakhi Story"' in prompt
    assert '"title_text":"Grandma\'s Rakhi Story"' in prompt
    assert "ASPECT RATIO:\n3:4" in prompt
    assert "fully readable, correctly spelled, and unobstructed" in prompt
    assert "Do not use a banner, label, card, sticker, plaque, black rectangle" in prompt


def test_render_cover_prompt_locks_visual_bible_identity_and_cover_continuity():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_image_prompt(
        page_type="cover",
        story_title="The Moon Bell",
        visual_bible={
            "characters": [
                {
                    "name": "Mira",
                    "role": "hero",
                    "anchor": "Mira anchor.",
                    "appearance": {
                        "age": "eight years old",
                        "height_build": "small child, slim build",
                        "skin_tone": "warm medium brown skin",
                        "eye_shape": "large almond eyes",
                        "eye_color": "dark brown",
                        "face_shape": "round face",
                        "hair": {"color": "black", "length": "short", "style": "bob"},
                        "outfit": {"type": "dress", "primary_color": "yellow", "pattern": "plain"},
                        "footwear": "white sneakers",
                        "accessories": ["red bracelet always present"],
                    },
                    "locks": {
                        "face_lock": "round face",
                        "hair_lock": "short black bob",
                        "outfit_lock": "plain yellow dress",
                        "accessory_lock": "red bracelet always present",
                    },
                    "forbidden_variations": ["blue dress", "long hair"],
                },
                {
                    "name": "Rohan",
                    "anchor": "Rohan anchor.",
                    "appearance": "green kurta",
                },
            ],
            "locations": [],
            "important_objects": [],
        },
        page_image_plan={
            "book_cover_prompt": "Front cover based on the whole story promise with exact Visual Bible appearance.",
            "visual_focus": "Mira stands beneath the glowing moon bell.",
            "composition": "Mira below a clean title area with the moon bell as the story signal.",
            "title_layout": "Large readable title at the top.",
            "characters": ["Mira"],
            "continuity_notes": [
                "Mira keeps round face, short black bob, plain yellow dress, red bracelet, and no blue dress."
            ],
        },
    )

    assert "same face, hair, outfit, accessories, body scale, and forbidden variations" in prompt
    assert "same canonical character models used for all interior story pages" in prompt
    assert "not marketing redesigns, alternate costumes, older/younger versions" in prompt
    assert "GLOBAL CHARACTER REFERENCE JSON" in prompt
    assert '"name":"Mira"' in prompt
    assert '"face_lock":"round face"' in prompt
    assert '"hair_lock":"short black bob"' in prompt
    assert '"outfit_lock":"plain yellow dress"' in prompt
    assert "character consistency is stricter than cover styling" in prompt
    assert (
        "Continuity requirements: Mira keeps round face, short black bob, plain yellow dress, red bracelet, "
        "and no blue dress."
    ) in prompt
    assert "face=round face" in prompt
    assert "age_body=eight years old, small child, slim build" in prompt
    assert "skin=warm medium brown skin" in prompt
    assert "eyes=large almond eyes, dark brown" in prompt
    assert "hair=short black bob" in prompt
    assert "outfit=plain yellow dress" in prompt
    assert "footwear=white sneakers" in prompt
    assert "accessory=red bracelet always present" in prompt
    assert "forbid=blue dress, long hair" in prompt
    assert "Rohan anchor" not in prompt


def test_render_page_prompt_forbids_written_text():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_image_prompt(
        page_type="story_page",
        story_title="Grandma's Rakhi Story",
        visual_bible={"characters": [], "locations": [], "important_objects": []},
        page_image_plan={"page": 1, "visual_focus": "Grandma smiles."},
    )

    assert "This is an interior story page" in prompt
    assert "Do not render story prose" in prompt
    assert "top/bottom overlay text" in prompt
    assert "blackboard note, chair name label" in prompt
    assert "must not appear as a caption" in prompt
    assert '"title_text"' not in prompt


def test_multi_image_page_items_remove_story_text_from_image_context():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service._workflow_visual_bible = lambda workflow_arg: {
        "characters": [{"name": "Saavi", "anchor": "Saavi model sheet."}],
        "locations": [],
        "important_objects": [],
    }
    workflow = SimpleNamespace(
        title="The Moon Bell",
        story_json={},
        image_plan_json={
            "pages": [
                {
                    "page": 1,
                    "visual_focus": "Saavi sits with friends at lunch.",
                    "characters": ["Saavi"],
                    "important_objects": ["chair name label"],
                }
            ]
        },
    )
    story_json = {
        "title": "The Moon Bell",
        "pages": [
            {
                "page_number": 1,
                "emotion": "kindness",
                "text": "This story prose must not be painted at the top of the image.",
            }
        ],
    }

    items = service._multi_image_page_items(workflow, story_json)

    assert items[0]["story_page"] == {"page_number": 1, "emotion": "kindness"}
    assert "text" not in items[0]["story_page"]
    assert "CHARACTER MODEL SHEET" not in items[0]["visual_context"]
    assert "Saavi model sheet" not in items[0]["visual_context"]
    compact_items = service._multi_image_item_payloads(items)
    assert "rendered_prompt" not in compact_items[0]
    assert compact_items[0]["page_image_plan"] == items[0]["page_image_plan"]
    assert compact_items[0]["visible_character_contract"] == {
        "visible_names": ["Saavi"],
        "exact_count_per_name": 1,
        "rules": [
            "draw each listed named character once only",
            "no duplicate heads, duplicate faces, reflections, portraits, photos, or lookalikes",
            "every visible head must be attached to one coherent body",
            "no unlisted background people or head-only cutouts",
        ],
    }


def test_multi_image_prompt_forbids_caption_text_but_allows_planned_in_scene_text():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    prompt = service._render_multi_image_pages_prompt(
        story_title="The Moon Bell",
        age_group="3-6",
        visual_bible={
            "style": "warm watercolor",
            "characters": [
                {
                    "name": "Saavi",
                    "appearance": {"hair": {"style": "two pigtails"}, "outfit": {"type": "pink kurta"}},
                    "locks": {"hair_lock": "two pigtails", "outfit_lock": "pink kurta"},
                    "forbidden_variations": ["loose hair", "blue dress"],
                }
            ],
        },
        page_items=[
            {
                "key": "page_1",
                "page_type": "story_page",
                "page_image_plan": {
                    "page": 1,
                    "visual_focus": "Children listen in class.",
                    "important_objects": ["blackboard writing", "chair name labels"],
                    "object_states": {"blackboard writing": "visible only on the classroom blackboard"},
                },
                "source_image_prompt": "Classroom scene with blackboard.",
                "story_page": {"page_number": 1, "emotion": "happy"},
                "visual_context": "Saavi model sheet.",
                "rendered_prompt": "full nested prompt should not be included",
            }
        ],
    )

    assert "Do not render story prose" in prompt
    assert "top/bottom overlay text" in prompt
    assert "blackboard writing, a chair name label" in prompt
    assert "page_image_plan.allowed_in_scene_text" in prompt
    assert "GLOBAL CHARACTER REFERENCE JSON" in prompt
    assert '"name":"Saavi"' in prompt
    assert "Use GLOBAL CHARACTER REFERENCE JSON as the source of truth for character appearance" in prompt
    assert "Use visual_context as the source of truth for page-scoped style" in prompt
    assert "Use visible_character_contract as the exact count contract" in prompt
    assert "Use page_image_plan.object_states as the exact visible state contract" in prompt
    assert "Do not carry a recurring/signature object into an item unless it is listed" in prompt
    assert "Do not jump ahead to a future solved object state" in prompt
    assert "Draw each named character listed for an item exactly once" in prompt
    assert "No floating heads, detached heads, disembodied faces" in prompt
    assert "no duplicate heads, no duplicate faces, no detached heads" in prompt
    assert "Respect basic scene etiquette" in prompt
    assert "do not draw outdoor shoes on feet" in prompt
    assert "locked footwear remains the character's footwear when footwear is appropriate" in prompt
    assert "source_image_prompt" not in prompt
    assert "Classroom scene with blackboard." not in prompt
    assert "The required output is image parts" in prompt
    assert "Do not stop after text markers" in prompt
    assert "Return image outputs, not a text explanation." in prompt
    assert "scoped_visual_bible" not in prompt
    assert "full nested prompt should not be included" not in prompt
    assert "Do not place words in empty wall/sky/background/negative space as a caption" in prompt


def test_multi_image_page_prompt_payload_strips_duplicate_source_prompt():
    payload = GenericStoryWorkflowService._multi_image_item_payloads(
        [
            {
                "key": "page_1",
                "page_type": "story_page",
                "page_number": 1,
                "page_image_plan": {"page": 1, "allowed_in_scene_text": []},
                "source_image_prompt": "duplicated full page prompt",
            }
        ],
        include_source_image_prompt=False,
    )

    assert "source_image_prompt" not in payload[0]
    assert payload[0]["visible_character_contract"]["visible_names"] == []
    assert payload[0]["visible_character_contract"]["exact_count_per_name"] == 1


def test_multi_image_page_plan_accepts_characters_present_alias():
    page_plan = {
        "page": 1,
        "visual_focus": "Pip studies the clay pot.",
        "characters_present": ["Pip"],
        "allowed_in_scene_text": [],
    }

    plan_payload = GenericStoryWorkflowService._story_page_image_plan_for_multi_image(page_plan)
    item_payload = GenericStoryWorkflowService._multi_image_item_payload(
        {
            "key": "page_1",
            "page_type": "story_page",
            "page_number": 1,
            "page_image_plan": plan_payload,
        }
    )

    assert plan_payload["characters"] == ["Pip"]
    assert item_payload["visible_character_contract"]["visible_names"] == ["Pip"]


def test_multi_image_story_page_plan_payload_preserves_object_states():
    payload = GenericStoryWorkflowService._story_page_image_plan_for_multi_image(
        {
            "page": 4,
            "visual_focus": "Pip studies the clay pot.",
            "important_objects": ["clay pot", "water"],
            "object_states": {
                "clay pot": "same old clay pot, upright under the wilting bush",
                "water": "tiny amount shimmering at the very bottom, unreachable",
            },
        }
    )

    assert payload["important_objects"] == ["clay pot", "water"]
    assert payload["object_states"]["clay pot"] == "same old clay pot, upright under the wilting bush"
    assert "unreachable" in payload["object_states"]["water"]


def test_multi_image_story_page_plan_payload_removes_text_layout_instructions():
    payload = GenericStoryWorkflowService._story_page_image_plan_for_multi_image(
        {
            "page": 1,
            "title_text": "The Moon Bell",
            "title_layout": "Large readable title at the top.",
            "book_cover_prompt": "Cover prompt",
            "visual_focus": "Mira smiles. Clear space is reserved at the top for title text.",
            "composition": "Mira sits near the window. Add caption area at the bottom.",
            "environment": "Cozy room with top text area.",
            "characters": ["Mira"],
            "important_objects": ["moon bell"],
            "continuity_notes": [
                "Mira keeps her short black bob and plain yellow dress.",
                "Leave clear space for narration text.",
            ],
        }
    )

    payload_text = json.dumps(payload)
    assert "title_text" not in payload
    assert "title_layout" not in payload
    assert "book_cover_prompt" not in payload
    assert "Clear space" not in payload_text
    assert "caption area" not in payload_text
    assert "top text" not in payload_text
    assert payload["continuity_notes"] == ["Mira keeps her short black bob and plain yellow dress."]
    assert payload["allowed_in_scene_text"] == []


def test_workflow_multi_image_payload_drops_rendered_prompt_metadata():
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    job = SimpleNamespace(request_keys=["page_1"])
    payload = {
        "items": [
            {
                "key": "page_1",
                "page_type": "story_page",
                "page_number": 1,
                "source_image_prompt": "planned page prompt",
                "rendered_prompt": "inline rendered page prompt",
            }
        ],
        "rendered_prompts": {"page_1": "full rendered page prompt"},
    }

    items = service._workflow_multi_image_items_from_payload(job, payload)

    assert "rendered_prompt" not in items[0]
    assert items[0]["source_image_prompt"] == "planned page prompt"


def test_workflow_multi_image_payload_reads_cover_item_separately():
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    job = SimpleNamespace(request_keys=["cover", "page_1"])
    payload = {
        "cover_item": {
            "key": "cover",
            "page_type": "cover",
            "source_image_prompt": "cover prompt",
            "rendered_prompt": "full cover prompt",
        },
        "items": [
            {
                "key": "page_1",
                "page_type": "story_page",
                "page_number": 1,
                "source_image_prompt": "planned page prompt",
            }
        ],
    }

    items = service._workflow_multi_image_items_from_payload(job, payload)

    assert [item["key"] for item in items] == ["cover", "page_1"]
    assert "rendered_prompt" not in items[0]


def test_generic_batch_cover_prompt_uses_current_image_prompt_contract():
    prompt = GenericStoryBatchService._render_image_prompt(
        page_type="cover",
        story_title="Grandma's Rakhi Story",
        visual_bible={
            "characters": [
                {
                    "name": "Rohan",
                    "appearance": "small boy in blue kurta",
                    "locks": {
                        "face_lock": "round face",
                        "hair_lock": "short black hair",
                        "outfit_lock": "blue kurta",
                        "accessory_lock": "no random accessories",
                    },
                    "forbidden_variations": ["red kurta"],
                }
            ],
            "locations": [],
            "important_objects": [],
        },
        page_image_plan={
            "characters": ["Rohan"],
            "visual_focus": "Rohan smiles with his family.",
            "composition": "Rohan below a clean title area.",
            "continuity_notes": ["Rohan keeps blue kurta and short black hair."],
        },
    )

    assert "{title_instruction}" not in prompt
    assert "{visual_context}" not in prompt
    assert "Premium cinematic cartoon children's storybook illustration" in prompt
    assert 'Render this exact visible title text: "Grandma\'s Rakhi Story"' in prompt
    assert '"title_text":"Grandma\'s Rakhi Story"' in prompt
    assert "small boy in blue kurta" in prompt
    assert "same face, hair, outfit, accessories, body scale, and forbidden variations" in prompt
    assert "Continuity requirements: Rohan keeps blue kurta and short black hair." in prompt
    assert "forbid=red kurta" in prompt


def test_generic_batch_builds_openai_image_batch_request():
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    item = SimpleNamespace(
        key="cover",
        page_type="cover",
        rendered_prompt="Create a readable book cover.",
    )

    request = service._build_openai_image_batch_request(item, model="gpt-image-1-mini")

    assert request["custom_id"] == "cover"
    assert request["method"] == "POST"
    assert request["url"] == "/v1/responses"
    assert request["body"]["model"] != "gpt-image-1-mini"
    assert request["body"]["input"] == "Create a readable book cover."
    image_tool = request["body"]["tools"][0]
    assert image_tool["type"] == "image_generation"
    assert image_tool["model"] == "gpt-image-1-mini"
    assert image_tool["size"]
    assert image_tool["quality"] in {"low", "medium", "high", "auto"}


def test_generic_batch_extracts_openai_image_response_bytes():
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\r*\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    body = {
        "output": [
            {"type": "message", "content": []},
            {
                "type": "image_generation_call",
                "result": base64.b64encode(png_bytes).decode("ascii"),
                "revised_prompt": "revised",
            },
        ]
    }

    image_bytes, revised_prompt = GenericStoryBatchService._extract_openai_image_bytes(body)

    assert image_bytes == png_bytes
    assert revised_prompt == "revised"


def test_generic_batch_summarizes_openai_response_error_body():
    body = {
        "error": {
            "message": "Your organization must be verified to use the model.",
            "type": "invalid_request_error",
            "param": None,
            "code": None,
            "extra": "not exposed",
        }
    }

    assert GenericStoryBatchService._openai_response_error_summary(body) == {
        "message": "Your organization must be verified to use the model.",
        "type": "invalid_request_error",
        "param": None,
        "code": None,
    }


def test_generic_batch_extracts_legacy_openai_image_response_bytes():
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\r*\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    body = {"data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii"), "revised_prompt": "revised"}]}

    image_bytes, revised_prompt = GenericStoryBatchService._extract_openai_image_bytes(body)

    assert image_bytes == png_bytes
    assert revised_prompt == "revised"


@pytest.mark.asyncio
async def test_generic_image_batch_duplicate_check_is_provider_scoped():
    generic_story_id = uuid4()
    workflow_id = uuid4()
    batch_job_id = uuid4()
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    service.generic_stories = SimpleNamespace()
    service.workflows = SimpleNamespace()
    service.batch_jobs = SimpleNamespace()
    service.session = SimpleNamespace()
    workflow = SimpleNamespace(
        id=workflow_id,
        image_plan_json={"cover": {"image_prompt": "Cover"}},
        status="COMPLETED",
        current_step=None,
        error_message=None,
    )
    submitted_provider = None

    async def _get_story(story_id):
        assert story_id == generic_story_id
        return SimpleNamespace(id=generic_story_id)

    async def _latest_for_story_type(story_id, job_type, provider=None):
        assert story_id == generic_story_id
        assert job_type == StoryBatchJobType.IMAGE
        assert provider == "openai"
        return None

    async def _latest_workflow(user_id, story_id):
        assert story_id == generic_story_id
        return workflow

    async def _update_workflow(updated_workflow):
        return updated_workflow

    async def _commit():
        return None

    async def _missing_image_items(story_json, items):
        return items

    async def _submit_image_batch_job_only(workflow_arg, story_id, items, *, attempt, force=False, provider="google"):
        nonlocal submitted_provider
        submitted_provider = provider
        return SimpleNamespace(
            id=batch_job_id,
            generic_story_id=story_id,
            workflow_id=workflow_arg.id,
            job_type=StoryBatchJobType.IMAGE,
            status=StoryBatchJobStatus.SUBMITTED,
            provider_job_name="batch_openai",
            provider_state="validating",
            expected_item_count=len(items),
        )

    service.generic_stories.get_by_id = _get_story
    service.workflows.latest_for_user_generic_story = _latest_workflow
    service.workflows.update = _update_workflow
    service.batch_jobs.latest_for_story_type = _latest_for_story_type
    service.session.commit = _commit
    service._story_json_for_image_plan = lambda workflow_arg, story_arg: {"title": "Story", "pages": []}
    service._build_image_items = lambda workflow_arg, story_json: [
        SimpleNamespace(key="cover", page_type="cover", page_number=0)
    ]
    service._missing_image_items = _missing_image_items
    service._submit_image_batch_job_only = _submit_image_batch_job_only

    response = await service.submit_image_batch(
        user_id=uuid4(),
        generic_story_id=generic_story_id,
        force=True,
        provider="openai",
    )

    assert submitted_provider == "openai"
    assert response.batch_job_id == batch_job_id
    assert response.provider_job_name == "batch_openai"


def test_validate_image_cover_plan_normalizes_exact_story_title():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(story_json={"title": "Grandma's Rakhi Story"}, title="Wrong Title", scene_plan_json={})
    image_plan = {
        "cover": {
            "title_text": "Wrong Title",
            "visual_focus": "Rohan and Meera carry a mango together.",
            "camera_shot": "medium",
            "composition": "Characters below a clean top title area that presents the family festival story.",
        },
        "pages": [],
    }

    service._validate_and_normalize_image_cover_plan(image_plan, workflow)

    assert image_plan["cover"]["title_text"] == "Grandma's Rakhi Story"


def test_validate_image_cover_plan_requires_title_layout_context():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(story_json={"title": "Grandma's Rakhi Story"}, title=None, scene_plan_json={})
    image_plan = {
        "cover": {
            "title_text": "Grandma's Rakhi Story",
            "visual_focus": "Rohan and Meera carry a mango together.",
            "camera_shot": "medium",
            "composition": "Rohan and Meera stand near Grandma in the courtyard.",
        },
        "pages": [],
    }

    with pytest.raises(AppException, match="title placement"):
        service._validate_and_normalize_image_cover_plan(image_plan, workflow)


def test_validate_image_cover_plan_normalizes_cover_character_names_from_visual_bible():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        story_json={"title": "The Moon Bell"},
        title=None,
        scene_plan_json={},
        visual_bible_json={"characters": [{"name": "Mira"}]},
    )
    image_plan = {
        "cover": {
            "title_text": "The Moon Bell",
            "visual_focus": "Mira listens beneath the moon.",
            "camera_shot": "wide",
            "composition": "Mira below a clean title area with a bedtime genre signal.",
            "characters": ["mira"],
            "continuity_notes": ["Mira keeps her exact Visual Bible face, hair, outfit, and accessories."],
        },
        "pages": [],
    }

    service._validate_and_normalize_image_cover_plan(image_plan, workflow)

    assert image_plan["cover"]["characters"] == ["Mira"]


def test_validate_image_cover_plan_rejects_unknown_cover_character():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        story_json={"title": "The Moon Bell"},
        title=None,
        scene_plan_json={},
        visual_bible_json={"characters": [{"name": "Mira"}]},
    )
    image_plan = {
        "cover": {
            "title_text": "The Moon Bell",
            "visual_focus": "Mira listens beneath the moon.",
            "camera_shot": "wide",
            "composition": "Mira below a clean title area with a bedtime genre signal.",
            "characters": ["Rohan"],
            "continuity_notes": ["Rohan keeps his exact Visual Bible face, hair, outfit, and accessories."],
        },
        "pages": [],
    }

    with pytest.raises(AppException, match="Visual Bible character names"):
        service._validate_and_normalize_image_cover_plan(image_plan, workflow)


def test_validate_image_cover_plan_requires_cover_continuity_when_characters_are_visible():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        story_json={"title": "The Moon Bell"},
        title=None,
        scene_plan_json={},
        visual_bible_json={"characters": [{"name": "Mira"}]},
    )
    image_plan = {
        "cover": {
            "title_text": "The Moon Bell",
            "visual_focus": "Mira listens beneath the moon.",
            "camera_shot": "wide",
            "composition": "Mira below a clean title area with a bedtime genre signal.",
            "characters": ["Mira"],
            "continuity_notes": [],
        },
        "pages": [],
    }

    with pytest.raises(AppException, match="continuity notes"):
        service._validate_and_normalize_image_cover_plan(image_plan, workflow)


def test_normalize_image_plan_pages_matches_story_json_page_numbers():
    image_plan = {
        "pages": [
            {"page": 2, "visual_focus": "Second scene."},
            {"page": 1, "visual_focus": "First scene."},
        ]
    }
    story_pages = [
        {"page_number": 1, "text": "First story page."},
        {"page_number": 2, "text": "Second story page."},
    ]

    GenericStoryWorkflowService._normalize_image_plan_pages(image_plan, story_pages)

    assert [page["page"] for page in image_plan["pages"]] == [1, 2]
    assert [page["page_number"] for page in image_plan["pages"]] == [1, 2]
    assert [page["visual_focus"] for page in image_plan["pages"]] == ["First scene.", "Second scene."]


def test_normalize_image_plan_pages_rejects_duplicate_page_numbers():
    image_plan = {
        "pages": [
            {"page": 1, "visual_focus": "First scene."},
            {"page": 1, "visual_focus": "Duplicate scene."},
        ]
    }
    story_pages = [
        {"page_number": 1, "text": "First story page."},
        {"page_number": 2, "text": "Second story page."},
    ]

    with pytest.raises(AppException) as exc_info:
        GenericStoryWorkflowService._normalize_image_plan_pages(image_plan, story_pages)

    assert exc_info.value.code == "GENERIC_IMAGE_PLAN_PAGE_COUNT_MISMATCH"
    assert exc_info.value.details["expected_page_count"] == 2
    assert exc_info.value.details["received_page_count"] == 2
    assert exc_info.value.details["expected_page_numbers"] == [1, 2]
    assert exc_info.value.details["received_page_numbers"] == [1, 1]


def test_normalize_image_plan_pages_reports_expected_and_received_counts():
    image_plan = {"pages": [{"page": 1, "visual_focus": "Only scene."}]}
    story_pages = [
        {"page_number": 1, "text": "First story page."},
        {"page_number": 2, "text": "Second story page."},
    ]

    with pytest.raises(AppException) as exc_info:
        GenericStoryWorkflowService._normalize_image_plan_pages(image_plan, story_pages)

    assert exc_info.value.code == "GENERIC_IMAGE_PLAN_PAGE_COUNT_MISMATCH"
    assert str(exc_info.value) == "Image plan returned 1 pages; expected 2 story JSON pages."
    assert exc_info.value.details == {
        "reason": "page count mismatch",
        "expected_page_count": 2,
        "received_page_count": 1,
        "expected_page_numbers": [1, 2],
        "received_page_numbers": [1],
        "story_json_page_count": 2,
    }


def test_story_json_for_image_plan_prompt_removes_generated_artifacts():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    compact_story = service._story_json_for_image_plan_prompt(
        {
            "title": "The Moon Bell",
            "summary": "A child listens carefully.",
            "cover_image_prompt": "large rendered cover prompt",
            STORY_LANGUAGE_VARIANTS_KEY: {"hi": {"title": "Hindi title"}},
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": "Mira listened.",
                    "narration": {"tone": "warm", "pace": "slow", "voice_style": "gentle", "extra": "drop"},
                    "image_prompt": "large rendered page prompt",
                    "planned_image_prompt": "short plan",
                    "image_url": "https://cdn.example.test/page.png",
                    "audio_url": "https://cdn.example.test/page.wav",
                    "word_timestamps": [{"word": "Mira"}],
                    "tts_prompt": "large tts prompt",
                }
            ],
            "moral": "Listening helps.",
        }
    )

    assert compact_story == {
        "title": "The Moon Bell",
        "summary": "A child listens carefully.",
        "moral": "Listening helps.",
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Mira listened.",
                "narration": {"tone": "warm", "pace": "slow", "voice_style": "gentle"},
            }
        ],
    }


def test_generate_dummy_narration_saves_wav_data_urls_and_rendered_tts_prompt(monkeypatch):
    class _FakeTTSProvider:
        def build_prompt(self, text, *, pace, language, voice_style, tone, emotion):
            return (
                "RENDERED TTS PROMPT\n"
                f"text={text}\n"
                f"pace={pace}\n"
                f"language={language}\n"
                f"voice_style={voice_style}\n"
                f"tone={tone}\n"
                f"emotion={emotion}"
            )

    monkeypatch.setattr("app.service.generic_story_workflow_service.GoogleTTSProvider", _FakeTTSProvider)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="3-6",
        language="en",
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": "Mira looked at Mars and smiled.",
                }
            ]
        },
    )

    service._generate_dummy_narration(workflow)

    page = workflow.story_json["pages"][0]
    assert page["audio_url"].startswith("data:audio/wav;base64,")
    assert page["audio_dummy"] is True
    assert page["tts_skipped"] is True
    assert page["tts_model"]
    assert page["duration"] == GenericStoryWorkflowService.DUMMY_AUDIO_DURATION_SECONDS
    assert "RENDERED TTS PROMPT" in page["tts_prompt"]
    assert "Mira looked at Mars and smiled." in page["tts_prompt"]
    assert "voice_style=warm animated storyteller" in page["tts_prompt"]


def test_generate_dummy_narration_saves_audio_for_each_language_variant(monkeypatch):
    class _FakeTTSProvider:
        def build_prompt(self, text, *, pace, language, voice_style, tone, emotion):
            return f"text={text};language={language};pace={pace};voice_style={voice_style};tone={tone};emotion={emotion}"

    monkeypatch.setattr("app.service.generic_story_workflow_service.GoogleTTSProvider", _FakeTTSProvider)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="3-6",
        language="en",
        story_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "pages": [{"page_number": 1, "emotion": "wonder", "text": "Mira listened."}],
            "moral": "Listening helps friends solve problems.",
            STORY_LANGUAGE_VARIANTS_KEY: {
                "en": {
                    "title": "The Moon Bell",
                    "summary": "A child helps restore the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Mira listened."}],
                    "moral": "Listening helps friends solve problems.",
                },
                "hi": {
                    "title": "Chaand ki Ghanti",
                    "summary": "Mira helps the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Hindi narration text."}],
                    "moral": "Careful listening helps friends.",
                },
                "mr": {
                    "title": "Chandrachi Ghanta",
                    "summary": "Mira helps the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Marathi narration text."}],
                    "moral": "Careful listening helps friends.",
                },
            },
        },
    )

    service._generate_dummy_narration(workflow)

    variants = workflow.story_json[STORY_LANGUAGE_VARIANTS_KEY]
    assert workflow.story_json["pages"][0]["tts_prompt"].startswith("text=Mira listened.;language=en")
    assert variants["en"]["pages"][0]["audio_url"].startswith("data:audio/wav;base64,")
    assert variants["hi"]["pages"][0]["tts_prompt"].startswith("text=Hindi narration text.;language=hi")
    assert variants["mr"]["pages"][0]["tts_prompt"].startswith("text=Marathi narration text.;language=mr")


def test_narration_step_output_returns_prompts_for_all_languages():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        language="en",
        story_json={
            "pages": [
                {
                    "page_number": 1,
                    "text": "English page text.",
                    "audio_url": "https://cdn.example.test/en/page-1.wav",
                    "tts_prompt": "English narration prompt",
                    "duration": 1.2,
                }
            ],
            STORY_LANGUAGE_VARIANTS_KEY: {
                "en": {
                    "title": "The Moon Bell",
                    "pages": [
                        {
                            "page_number": 1,
                            "text": "English page text.",
                            "audio_url": "https://cdn.example.test/en/page-1.wav",
                            "tts_prompt": "English narration prompt",
                            "duration": 1.2,
                        }
                    ],
                },
                "hi": {
                    "title": "Chaand ki Ghanti",
                    "pages": [
                        {
                            "page_number": 1,
                            "text": "Hindi page text.",
                            "audio_url": "https://cdn.example.test/hi/page-1.wav",
                            "tts_prompt": "Hindi narration prompt",
                            "duration": 1.4,
                        }
                    ],
                },
                "mr": {
                    "title": "Chandrachi Ghanta",
                    "pages": [
                        {
                            "page_number": 1,
                            "text": "Marathi page text.",
                            "audio_url": "https://cdn.example.test/mr/page-1.wav",
                            "tts_prompt": "Marathi narration prompt",
                            "duration": 1.5,
                        }
                    ],
                },
            },
        },
    )

    output = service._step_output(workflow, GenericStoryWorkflowStep.NARRATION_GENERATION)

    assert output["pages"][0]["tts_prompt"] == "English narration prompt"
    assert output["languages"]["en"]["pages"][0]["tts_prompt"] == "English narration prompt"
    assert output["languages"]["hi"]["pages"][0]["tts_prompt"] == "Hindi narration prompt"
    assert output["languages"]["mr"]["pages"][0]["tts_prompt"] == "Marathi narration prompt"
    assert output["languages"]["hi"]["pages"][0]["text"] == "Hindi page text."


@pytest.mark.asyncio
async def test_generate_google_narration_calls_narration_service_once_per_language(monkeypatch):
    calls = []

    class _FakeNarrationService:
        def __init__(self, session):
            self.session = session

        async def generate_story_json_narration(
            self,
            story_json,
            *,
            story_id,
            language,
            overwrite,
            source,
            age_group,
        ):
            calls.append(
                {
                    "language": language,
                    "text": story_json["pages"][0]["text"],
                    "story_id": story_id,
                    "overwrite": overwrite,
                    "source": source,
                    "age_group": age_group,
                }
            )
            story_json["pages"][0]["audio_url"] = f"https://cdn.example.test/{language}/page-1.wav"
            story_json["pages"][0]["duration"] = 1.25
            return story_json

    monkeypatch.setattr("app.service.generic_story_workflow_service.StoryNarrationService", _FakeNarrationService)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.session = object()
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        age_group="3-6",
        language="en",
        story_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "pages": [{"page_number": 1, "emotion": "wonder", "text": "Mira listened."}],
            "moral": "Listening helps friends solve problems.",
            STORY_LANGUAGE_VARIANTS_KEY: {
                "en": {
                    "title": "The Moon Bell",
                    "summary": "A child helps restore the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Mira listened."}],
                    "moral": "Listening helps friends solve problems.",
                },
                "hi": {
                    "title": "Chaand ki Ghanti",
                    "summary": "Mira helps the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Hindi narration text."}],
                    "moral": "Careful listening helps friends.",
                },
                "mr": {
                    "title": "Chandrachi Ghanta",
                    "summary": "Mira helps the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Marathi narration text."}],
                    "moral": "Careful listening helps friends.",
                },
            },
        },
    )

    narrated_story_json = await service._generate_google_narration(workflow)

    assert [call["language"] for call in calls] == ["en", "hi", "mr"]
    assert [call["text"] for call in calls] == [
        "Mira listened.",
        "Hindi narration text.",
        "Marathi narration text.",
    ]
    assert all(call["story_id"] == workflow_id for call in calls)
    assert all(call["overwrite"] is False for call in calls)
    variants = narrated_story_json[STORY_LANGUAGE_VARIANTS_KEY]
    assert narrated_story_json["pages"][0]["audio_url"] == "https://cdn.example.test/en/page-1.wav"
    assert variants["hi"]["pages"][0]["audio_url"] == "https://cdn.example.test/hi/page-1.wav"
    assert variants["mr"]["pages"][0]["audio_url"] == "https://cdn.example.test/mr/page-1.wav"


@pytest.mark.asyncio
async def test_get_steps_returns_details_for_all_workflow_steps():
    workflow_id = uuid4()
    generic_story_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        status="COMPLETED",
        current_step=None,
        error_message=None,
        generic_story_id=generic_story_id,
        requested_pages=2,
        title="Journey to Mars",
        cover_image=GenericStoryWorkflowService.DUMMY_PNG_DATA_URL,
        input_request={"status": "inactive"},
        character_analysis_json={
            "source_title": "Journey to Mars",
            "characters": [{"name": "Mira"}, {"name": "Robo"}],
        },
        scene_plan_json={
            "title": "Journey to Mars",
            "pages": [{"page_number": 1}, {"page_number": 2}],
        },
        visual_bible_json={
            "characters": [{"name": "Mira", "appearance": "Yellow dress and short black hair."}],
            "locations": [{"name": "Mars garden"}],
            "important_objects": [{"name": "red rocket"}],
        },
        story_json={
            "title": "Journey to Mars",
            "moral": "Curiosity grows with careful questions.",
            "cover_image_url": GenericStoryWorkflowService.DUMMY_PNG_DATA_URL,
            "cover_image_prompt": "Cover prompt.",
            "cover_image_dummy": True,
            "pages": [
                    {
                        "page_number": 1,
                        "image_url": GenericStoryWorkflowService.DUMMY_PNG_DATA_URL,
                        "image_prompt": "Page image prompt.",
                        "planned_image_prompt": "Page image prompt.",
                        "image_dummy": True,
                    "audio_url": GenericStoryWorkflowService.DUMMY_WAV_DATA_URL,
                    "audio_dummy": True,
                    "tts_skipped": True,
                    "tts_prompt": "You are a professional children's audiobook narrator.\nStory text to speak:\nMira looked at Mars.",
                    "duration": 0.1,
                }
            ],
        },
        image_plan_json={
            "cover": {"image_prompt": "Cover prompt."},
            "pages": [{"page_number": 1, "image_prompt": "Page image prompt."}],
        },
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    async def _get_by_id(requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return workflow

    service.workflows = SimpleNamespace(get_by_id=_get_by_id)

    steps = await service.get_steps(uuid4(), workflow_id)
    by_name = {step.step_name: step for step in steps}

    assert [step.step_name for step in steps] == [
        "IMAGE_GENERATION",
        "NARRATION_GENERATION",
    ]
    assert steps[0].genric_story_id == str(generic_story_id)
    assert "CHARACTER_EXTRACTION" not in by_name
    assert "SCENE_PLAN_GENERATION" not in by_name
    assert "VISUAL_BIBLE_GENERATION" not in by_name
    assert "STORY_GENERATION" not in by_name
    assert "IMAGE_PLAN_GENERATION" not in by_name
    assert "PUBLISH_GENERIC_STORY" not in by_name
    assert by_name["IMAGE_GENERATION"].summary["uses_dummy_images"] is True
    assert by_name["IMAGE_GENERATION"].output["visual_bible"]["characters"][0]["name"] == "Mira"
    assert by_name["IMAGE_GENERATION"].output["final_prompts"] == [
        {"page": "cover", "prompt": "Cover prompt."},
        {"page": 1, "prompt": "Page image prompt."},
    ]
    assert by_name["IMAGE_GENERATION"].output["pages"][0]["planned_image_prompt"] == "Page image prompt."
    assert by_name["NARRATION_GENERATION"].summary["uses_dummy_audio"] is True
    assert "professional children's audiobook narrator" in by_name["NARRATION_GENERATION"].output["pages"][0]["tts_prompt"]

    image_steps = await service.get_steps(uuid4(), workflow_id, step_name="IMAGE_GENERATION")

    assert len(image_steps) == 1
    assert image_steps[0].step_name == "IMAGE_GENERATION"
    assert image_steps[0].output["final_prompts"][0]["page"] == "cover"

    with pytest.raises(AppException) as exc_info:
        await service.get_steps(uuid4(), workflow_id, step_name="VISUAL_BIBLE_GENERATION")

    assert exc_info.value.code == "GENERIC_STORY_STEP_NOT_EXPOSED"


@pytest.mark.asyncio
async def test_get_steps_rejects_unexposed_visual_bible_step():
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        status="COMPLETED",
        current_step=None,
        error_message=None,
        generic_story_id=uuid4(),
        requested_pages=None,
        title="Journey to Mars",
        cover_image=None,
        visual_bible_json=None,
        story_json={"title": "Journey to Mars", "pages": []},
        image_plan_json={
            "visual_bible": {
                "style": "Watercolor storybook.",
                "characters": [{"name": "Mira"}],
                "locations": [{"name": "Mars garden"}],
                "important_objects": [{"name": "red rocket"}],
            },
            "pages": [],
        },
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    async def _get_by_id(requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return workflow

    service.workflows = SimpleNamespace(get_by_id=_get_by_id)

    with pytest.raises(AppException) as exc_info:
        await service.get_steps(uuid4(), workflow_id, step_name="VISUAL_BIBLE_GENERATION")

    assert exc_info.value.code == "GENERIC_STORY_STEP_NOT_EXPOSED"


@pytest.mark.asyncio
async def test_execute_scene_plan_step_requires_character_extraction():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(character_analysis_json=None)

    with pytest.raises(AppException, match="Run CHARACTER_EXTRACTION"):
        await service._execute_single_step(
            workflow,
            GenericStoryWorkflowStep.SCENE_PLAN_GENERATION,
            public_base_url="https://api.example.test",
            payload=SimpleNamespace(publish_status=None),
        )


@pytest.mark.asyncio
async def test_image_generation_step_always_uses_multi_image_batch():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        story_json={"title": "The Moon Bell", "pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json={"pages": [{"page": 1, "image_prompt": "Mira listens."}]},
    )
    calls = []

    async def _bulk(workflow_arg, *, payload, public_base_url):
        calls.append((workflow_arg, payload, public_base_url))

    async def _single(workflow_arg, *, public_base_url):
        raise AssertionError("single image generation should not run for generic workflows")

    service._generate_cover_and_submit_multi_image_pages = _bulk
    service._generate_images = _single
    service._apply_workflow_metadata = lambda workflow_arg: None

    payload = GenericStoryWorkflowExecuteRequest.model_validate({})
    await service._execute_single_step(
        workflow,
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        public_base_url="https://api.example.test",
        payload=payload,
    )

    assert calls == [(workflow, payload, "https://api.example.test")]


@pytest.mark.asyncio
async def test_multi_image_batch_submission_continues_to_next_steps():
    workflow = _retry_workflow(
        status="PENDING",
        current_step=None,
        image_plan_json={"pages": [{"page": 1}]},
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    calls = []
    events = []

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        calls.append(step)
        if step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            workflow_arg.story_json["pages"][0]["audio_url"] = "https://cdn.example.test/page-1.wav"
        if step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            workflow_arg.generic_story_id = uuid4()

    service.workflows = SimpleNamespace(update=_update)
    service.session = SimpleNamespace(commit=_commit)
    service._execute_single_step = _execute_single_step
    service._log_workflow_event = lambda event, workflow_arg, **fields: events.append((event, fields))

    response = await service._execute_steps(
        workflow,
        [
            GenericStoryWorkflowStep.IMAGE_GENERATION,
            GenericStoryWorkflowStep.NARRATION_GENERATION,
            GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
        ],
        payload=GenericStoryWorkflowExecuteRequest(skip_narration_generation=False),
        public_base_url="https://api.example.test",
        event_name="workflow_started",
        requested_step="ALL",
    )

    assert calls == [
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
    ]
    assert any(
        event == "step_batch_submitted" and fields["step"] == GenericStoryWorkflowStep.IMAGE_GENERATION
        for event, fields in events
    )
    assert response.status == "COMPLETED"


@pytest.mark.asyncio
async def test_story_generation_step_creates_generic_story_before_remaining_steps():
    workflow = _retry_workflow(status="PENDING", current_step=None, story_json=None, generic_story_id=None)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.generic_stories = _FakeGenericStories()
    service.workflows = SimpleNamespace(update=lambda workflow_arg: workflow_arg)
    service.session = SimpleNamespace(commit=lambda: None)
    calls = []

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        calls.append(step)
        if step == GenericStoryWorkflowStep.STORY_GENERATION:
            workflow_arg.story_json = {
                "title": "The Moon Bell",
                "summary": "A child helps restore the moon bell.",
                "pages": [{"page_number": 1, "text": "Mira listened."}],
                "moral": "Listening helps friends.",
            }
        elif step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            assert workflow_arg.generic_story_id is not None
            workflow_arg.image_plan_json = {"pages": [{"page": 1}]}

    service.workflows.update = _update
    service.session.commit = _commit
    service._execute_single_step = _execute_single_step
    service._log_workflow_event = lambda *args, **kwargs: None

    response = await service._execute_steps(
        workflow,
        [
            GenericStoryWorkflowStep.STORY_GENERATION,
            GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        ],
        payload=GenericStoryWorkflowExecuteRequest(),
        public_base_url="https://api.example.test",
        event_name="workflow_started",
        requested_step="ALL",
    )

    assert calls == [GenericStoryWorkflowStep.STORY_GENERATION, GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION]
    assert service.generic_stories.created["status"] == "inactive"
    assert service.generic_stories.created["title"] == "The Moon Bell"
    assert service.generic_stories.contents[0].id == workflow.generic_story_id
    assert service.generic_stories.contents[1][0]["story_json"]["pages"][0]["text"] == "Mira listened."
    assert response.status == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_execute_stores_original_execute_request_on_workflow():
    workflow = _retry_workflow(
        status="PENDING",
        current_step=None,
        input_request={"title": "The Moon Bell", "status": "inactive"},
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _execute_steps(workflow_arg, steps, *, payload, public_base_url, event_name, requested_step):
        workflow_arg.generic_story_id = uuid4()
        workflow_arg.status = "COMPLETED"
        workflow_arg.current_step = None
        workflow_arg.error_message = None
        return GenericStoryWorkflowResponse.model_validate(workflow_arg)

    service._get_owned = _get_owned
    service._execute_steps = _execute_steps

    await service.execute(
        uuid4(),
        workflow.id,
        GenericStoryWorkflowExecuteRequest(
            step_name=GenericStoryWorkflowStep.IMAGE_GENERATION,
            skip_image_generation=False,
            skip_narration_generation=True,
            publish_status="active",
        ),
        public_base_url="https://api.example.test",
    )

    assert workflow.input_request["title"] == "The Moon Bell"
    assert workflow.input_request["status"] == "inactive"
    assert workflow.input_request["execute_request"] == {
        "step_name": "IMAGE_GENERATION",
        "skip_image_generation": False,
        "skip_narration_generation": True,
        "publish_status": "active",
    }


@pytest.mark.asyncio
async def test_image_generation_submits_single_bulk_batch_request():
    workflow_id = uuid4()
    generic_story_id = uuid4()
    page_items = [
        {
            "key": "page_1",
            "page_number": 1,
            "filename": "page_1.png",
            "page_type": "story_page",
            "page_image_plan": {"page": 1, "allowed_in_scene_text": []},
            "rendered_prompt": "full page 1 prompt",
        },
        {
            "key": "page_2",
            "page_number": 2,
            "filename": "page_2.png",
            "page_type": "story_page",
            "page_image_plan": {"page": 2, "allowed_in_scene_text": []},
            "rendered_prompt": "full page 2 prompt",
        },
    ]
    created_payloads = []
    created_jobs = []
    batches = SimpleNamespace(created=None)

    class _FakeBatchJobs:
        async def list_active_for_workflow(self, requested_workflow_id):
            assert requested_workflow_id == workflow_id
            return []

        async def create(self, **data):
            created_jobs.append(data)
            created_payloads.append(data["request_payload"])
            return SimpleNamespace(id=uuid4(), provider_job_name=None, provider_state=None)

        async def update(self, job):
            return job

    async def _flush():
        return None

    async def _create_batch(*, model, src, config):
        batches.created = {"model": model, "src": src, "config": config}
        return SimpleNamespace(name="batches/workflow-pages", state=SimpleNamespace(name="JOB_STATE_PENDING"))

    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.batch_jobs = _FakeBatchJobs()
    service.session = SimpleNamespace(flush=_flush)
    service.ai_provider = SimpleNamespace(
        client=SimpleNamespace(aio=SimpleNamespace(batches=SimpleNamespace(create=_create_batch)))
    )
    service._workflow_visual_bible = lambda workflow_arg: {}
    service._multi_image_page_items = lambda workflow_arg, story_json: page_items
    service._render_multi_image_pages_prompt = lambda **kwargs: "bulk prompt"

    workflow = SimpleNamespace(
        id=workflow_id,
        generic_story_id=generic_story_id,
        age_group="3-6",
        title="The Moon Bell",
        story_json={"title": "The Moon Bell", "pages": []},
        image_plan_json={"pages": []},
        status=None,
        current_step=None,
        error_message="previous error",
    )
    payload = GenericStoryWorkflowExecuteRequest(
        skip_narration_generation=True,
        publish_status="active",
    )

    await service._generate_cover_and_submit_multi_image_pages(
        workflow,
        payload=payload,
        public_base_url="https://api.example.test",
    )

    assert batches.created is not None
    assert len(batches.created["src"]) == 1
    assert created_jobs[0]["generic_story_id"] == generic_story_id
    assert created_payloads[0]["mode"] == "generic_story_workflow_multi_image_pages"
    assert [item["key"] for item in created_payloads[0]["items"]] == ["page_1", "page_2"]
    assert all("rendered_prompt" not in item for item in created_payloads[0]["items"])
    assert "rendered_prompts" not in created_payloads[0]
    assert created_payloads[0]["skip_narration_generation"] is True
    assert created_payloads[0]["publish_status"] == "active"
    assert created_payloads[0]["continue_after_image_generation"] is False
    assert workflow.status == "IN_PROGRESS"
    assert workflow.current_step == "IMAGE_GENERATION"
    assert workflow.error_message is None


@pytest.mark.asyncio
async def test_image_generation_cover_is_submitted_in_batch_without_direct_generation():
    workflow_id = uuid4()
    created_payloads = []
    batches = SimpleNamespace(created=None)

    class _FakeBatchJobs:
        async def list_active_for_workflow(self, requested_workflow_id):
            assert requested_workflow_id == workflow_id
            return []

        async def create(self, **data):
            created_payloads.append(data["request_payload"])
            return SimpleNamespace(id=uuid4(), provider_job_name=None, provider_state=None)

        async def update(self, job):
            return job

    async def _flush():
        return None

    async def _generate_image(*args, **kwargs):
        raise AssertionError("generic workflow should not call direct image generation")

    async def _create_batch(*, model, src, config):
        batches.created = {"model": model, "src": src, "config": config}
        return SimpleNamespace(name="batches/workflow-pages", state=SimpleNamespace(name="JOB_STATE_PENDING"))

    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.batch_jobs = _FakeBatchJobs()
    service.session = SimpleNamespace(flush=_flush)
    service.ai_provider = SimpleNamespace(
        generate_image=_generate_image,
        client=SimpleNamespace(aio=SimpleNamespace(batches=SimpleNamespace(create=_create_batch))),
    )
    service._workflow_visual_bible = lambda workflow_arg: {}
    service._multi_image_page_items = lambda workflow_arg, story_json: [
        {"key": "page_1", "page_number": 1, "filename": "page_1.png"},
    ]
    service._render_multi_image_pages_prompt = lambda **kwargs: "bulk prompt"

    workflow = SimpleNamespace(
        id=workflow_id,
        generic_story_id=uuid4(),
        age_group="3-6",
        title="The Moon Bell",
        story_json={"title": "The Moon Bell", "pages": []},
        image_plan_json={
            "cover": {"image_prompt": "cover prompt"},
            "pages": [],
        },
        status=None,
        current_step=None,
        error_message="previous error",
    )
    payload = GenericStoryWorkflowExecuteRequest(
        skip_narration_generation=False,
        publish_status=None,
    )

    await service._generate_cover_and_submit_multi_image_pages(
        workflow,
        payload=payload,
        public_base_url="https://api.example.test",
    )

    assert batches.created is not None
    assert batches.created["model"] == settings.GOOGLE_REFERENCE_IMAGE_MODEL.removeprefix("models/")
    assert len(batches.created["src"]) == 2
    assert [item["key"] for item in created_payloads[0]["items"]] == ["page_1"]
    assert created_payloads[0]["cover_item"]["key"] == "cover"
    assert created_payloads[0]["cover_item"]["page_type"] == "cover"


def _retry_workflow(**overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": uuid4(),
        "workflow_name": "generic_story",
        "status": "FAILED",
        "current_step": "IMAGE_PLAN_GENERATION",
        "error_message": "Image plan page count must match story JSON pages.",
        "generic_story_id": None,
        "actual_story": "A story with enough text to retry from the failed workflow step.",
        "age_group": "3-6",
        "language": "en",
        "requested_pages": None,
        "title": "The Moon Bell",
        "summary": "A child helps the moon bell.",
        "theme": "listening",
        "genre": "adventure",
        "moral": "Listening helps.",
        "learning_goal": "careful listening",
        "cover_image": None,
        "character_analysis_json": {"characters": [{"name": "Mira"}]},
        "scene_plan_json": {"pages": [{"page": 1}]},
        "visual_bible_json": {"characters": [{"name": "Mira"}]},
        "story_json": {"title": "The Moon Bell", "pages": [{"page_number": 1, "text": "Mira listened."}]},
        "image_plan_json": None,
        "input_request": {"status": "inactive"},
        "ai_provider": "google",
        "text_model": "gemini",
        "image_model": "imagen",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_retry_failed_workflow_resumes_from_failed_current_step():
    workflow = _retry_workflow(
        current_step="IMAGE_PLAN_GENERATION",
        input_request={"execute_request": {"skip_narration_generation": False}},
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.workflows = SimpleNamespace(update=lambda workflow_arg: workflow_arg)
    service.session = SimpleNamespace(commit=lambda: None)
    calls = []

    async def _get_owned(user_id, workflow_id):
        assert workflow_id == workflow.id
        return workflow

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        calls.append(step)
        if step == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION:
            workflow_arg.image_plan_json = {"pages": [{"page": 1}]}
        elif step == GenericStoryWorkflowStep.IMAGE_GENERATION:
            workflow_arg.story_json["cover_image_url"] = "https://cdn.example.test/cover.png"
            workflow_arg.story_json["pages"][0]["image_url"] = "https://cdn.example.test/page-1.png"
        elif step == GenericStoryWorkflowStep.NARRATION_GENERATION:
            workflow_arg.story_json["pages"][0]["audio_url"] = "https://cdn.example.test/page-1.wav"
        elif step == GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY:
            workflow_arg.generic_story_id = uuid4()

    service._get_owned = _get_owned
    service.workflows.update = _update
    service.session.commit = _commit
    service._execute_single_step = _execute_single_step

    response = await service.retry(
        uuid4(),
        workflow.id,
        public_base_url="https://api.example.test",
    )

    assert calls == [
        GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
        GenericStoryWorkflowStep.IMAGE_GENERATION,
        GenericStoryWorkflowStep.PUBLISH_GENERIC_STORY,
    ]
    assert response.status == "COMPLETED"
    assert response.current_step is None
    assert response.error_message is None
    assert response.generic_story_id is not None


@pytest.mark.asyncio
async def test_retry_failed_workflow_with_no_current_step_uses_first_incomplete_step():
    workflow = _retry_workflow(current_step=None, image_plan_json=None)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    calls = []

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        calls.append(step)
        workflow_arg.generic_story_id = uuid4()

    service._get_owned = _get_owned
    service.workflows = SimpleNamespace(update=_update)
    service.session = SimpleNamespace(commit=_commit)
    service._execute_single_step = _execute_single_step

    await service.retry(
        uuid4(),
        workflow.id,
        public_base_url="https://api.example.test",
    )

    assert calls[0] == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION


def test_retry_start_step_skips_completed_stale_current_step():
    workflow = _retry_workflow(
        current_step="SCENE_PLAN_GENERATION",
        scene_plan_json={"pages": [{"page": 1}]},
        visual_bible_json={"characters": [{"name": "Mira"}]},
        story_json={"title": "The Moon Bell", "pages": [{"page_number": 1, "text": "Mira listened."}]},
        image_plan_json=None,
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    assert service._retry_start_step(workflow) == GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION


@pytest.mark.parametrize("retry_status", ["FAILED", "IN_PROGRESS"])
@pytest.mark.asyncio
async def test_retry_uses_stored_execute_request_from_workflow_table(retry_status):
    workflow = _retry_workflow(
        status=retry_status,
        current_step="IMAGE_GENERATION",
        image_plan_json={"pages": [{"page": 1}]},
        input_request={
            "status": "inactive",
            "execute_request": {
                "step_name": "ALL",
                "skip_image_generation": False,
                "multi_image_mode": True,
                "skip_narration_generation": True,
                "publish_status": "active",
            },
        },
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    captured = {}

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _execute_steps(workflow_arg, steps, *, payload, public_base_url, event_name, requested_step):
        captured["steps"] = steps
        captured["payload"] = payload
        captured["event_name"] = event_name
        captured["requested_step"] = requested_step
        workflow_arg.generic_story_id = uuid4()
        workflow_arg.status = "COMPLETED"
        workflow_arg.current_step = None
        workflow_arg.error_message = None
        return GenericStoryWorkflowResponse.model_validate(workflow_arg)

    service._get_owned = _get_owned
    service._execute_steps = _execute_steps

    await service.retry(
        uuid4(),
        workflow.id,
        public_base_url="https://api.example.test",
    )

    payload = captured["payload"]
    assert captured["steps"][0] == GenericStoryWorkflowStep.IMAGE_GENERATION
    assert captured["event_name"] == "workflow_retry_started"
    assert captured["requested_step"] == GenericStoryWorkflowStep.IMAGE_GENERATION.value
    assert payload.skip_image_generation is False
    assert payload.skip_narration_generation is True
    assert payload.publish_status == "active"


@pytest.mark.asyncio
async def test_retry_legacy_workflow_without_stored_execute_request_uses_batch_defaults():
    workflow = _retry_workflow(
        status="IN_PROGRESS",
        current_step="IMAGE_GENERATION",
        image_plan_json={"pages": [{"page": 1}]},
        input_request={"status": "inactive", "step_name": "ALL"},
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    captured = {}

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _execute_steps(workflow_arg, steps, *, payload, public_base_url, event_name, requested_step):
        captured["steps"] = steps
        captured["payload"] = payload
        workflow_arg.generic_story_id = uuid4()
        workflow_arg.status = "COMPLETED"
        workflow_arg.current_step = None
        workflow_arg.error_message = None
        return GenericStoryWorkflowResponse.model_validate(workflow_arg)

    service._get_owned = _get_owned
    service._execute_steps = _execute_steps

    await service.retry(
        uuid4(),
        workflow.id,
        public_base_url="https://api.example.test",
    )

    payload = captured["payload"]
    assert captured["steps"][0] == GenericStoryWorkflowStep.IMAGE_GENERATION
    assert payload.skip_image_generation is False
    assert not hasattr(payload, "multi_image_mode")
    assert payload.publish_status == "inactive"


@pytest.mark.asyncio
async def test_retry_image_generation_uses_stored_request_and_bulk_batch():
    workflow = _retry_workflow(
        status="FAILED",
        current_step="IMAGE_GENERATION",
        generic_story_id=uuid4(),
        image_plan_json={"cover": {"image_prompt": "cover"}, "pages": [{"page_number": 1}]},
        input_request={
            "status": "inactive",
            "execute_request": {
                "step_name": "ALL",
                "skip_image_generation": False,
                "skip_narration_generation": True,
                "publish_status": "active",
            },
        },
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    bulk_calls = []

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _rollback():
        return None

    async def _sync_generic_story_from_workflow(*args, **kwargs):
        return None

    async def _bulk(workflow_arg, *, payload, public_base_url):
        bulk_calls.append((workflow_arg, payload, public_base_url))

    async def _single(*args, **kwargs):
        raise AssertionError("retry should not run direct image generation")

    async def _publish(workflow_arg, *, publish_status, public_base_url):
        workflow_arg.status = "COMPLETED"
        workflow_arg.generic_story_id = workflow_arg.generic_story_id or uuid4()

    service._get_owned = _get_owned
    service.workflows = SimpleNamespace(update=_update)
    service.session = SimpleNamespace(commit=_commit, rollback=_rollback)
    service._sync_generic_story_from_workflow = _sync_generic_story_from_workflow
    service._generate_cover_and_submit_multi_image_pages = _bulk
    service._generate_images = _single
    service._publish_generic_story = _publish
    service._log_workflow_event = lambda *args, **kwargs: None

    await service.retry(
        uuid4(),
        workflow.id,
        public_base_url="https://api.example.test",
    )

    assert len(bulk_calls) == 1
    assert bulk_calls[0][0] is workflow
    assert bulk_calls[0][1].skip_image_generation is False
    assert bulk_calls[0][1].skip_narration_generation is True
    assert bulk_calls[0][1].publish_status == "active"
    assert not hasattr(bulk_calls[0][1], "multi_image_mode")
    assert workflow.status == "COMPLETED"


@pytest.mark.asyncio
async def test_delete_workflow_removes_workflow_story_media_generic_story_and_child_links(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    generic_story_id = uuid4()
    generic_story = SimpleNamespace(id=generic_story_id)
    workflow = _retry_workflow(
        id=workflow_id,
        generic_story_id=generic_story_id,
        status="COMPLETED",
        current_step=None,
    )
    image_storage = SimpleNamespace(deleted=[])
    audio_storage = SimpleNamespace(deleted=[])
    child_books = SimpleNamespace(deleted=[])
    generic_stories = SimpleNamespace(deleted=[])
    workflows = SimpleNamespace(deleted=[])
    batch_jobs = SimpleNamespace()
    commits = []

    async def _delete_image_directory(story_id):
        image_storage.deleted.append(story_id)

    async def _delete_audio_directory(story_id):
        audio_storage.deleted.append(story_id)

    async def _get_owned(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow_id
        return workflow

    async def _delete_child_books(*, story_id, story_type):
        child_books.deleted.append((story_id, story_type))

    async def _get_generic_story(story_id):
        assert story_id == generic_story_id
        return generic_story

    async def _delete_generic_story(story):
        generic_stories.deleted.append(story)

    async def _delete_workflow(workflow_arg):
        workflows.deleted.append(workflow_arg)

    async def _commit():
        commits.append(True)

    async def _list_active_for_workflow(requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return []

    image_storage.delete_story_directory = _delete_image_directory
    audio_storage.delete_story_directory = _delete_audio_directory
    batch_jobs.list_active_for_workflow = _list_active_for_workflow
    generic_stories.get_by_id = _get_generic_story
    generic_stories.delete = _delete_generic_story
    child_books.delete_by_story = _delete_child_books
    workflows.delete = _delete_workflow
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_image_storage_service", lambda: image_storage)
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_story_audio_storage_service", lambda: audio_storage)

    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service._get_owned = _get_owned
    service.generic_stories = generic_stories
    service.child_books = child_books
    service.batch_jobs = batch_jobs
    service.workflows = workflows
    service.session = SimpleNamespace(commit=_commit)

    await service.delete(user_id, workflow_id)

    assert image_storage.deleted == [workflow_id, generic_story_id]
    assert audio_storage.deleted == [workflow_id, generic_story_id]
    assert child_books.deleted == [(generic_story_id, "generic")]
    assert generic_stories.deleted == [generic_story]
    assert workflows.deleted == [workflow]
    assert commits == [True]


@pytest.mark.asyncio
async def test_delete_workflow_without_generic_story_only_removes_workflow_media(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    workflow = _retry_workflow(id=workflow_id, generic_story_id=None, status="FAILED")
    image_storage = SimpleNamespace(deleted=[])
    audio_storage = SimpleNamespace(deleted=[])
    generic_stories = SimpleNamespace(deleted=[])
    child_books = SimpleNamespace(deleted=[])
    workflows = SimpleNamespace(deleted=[])
    batch_jobs = SimpleNamespace()

    async def _delete_image_directory(story_id):
        image_storage.deleted.append(story_id)

    async def _delete_audio_directory(story_id):
        audio_storage.deleted.append(story_id)

    async def _get_owned(requested_user_id, requested_workflow_id):
        return workflow

    async def _delete_workflow(workflow_arg):
        workflows.deleted.append(workflow_arg)

    async def _commit():
        return None

    async def _list_active_for_workflow(requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return []

    image_storage.delete_story_directory = _delete_image_directory
    audio_storage.delete_story_directory = _delete_audio_directory
    batch_jobs.list_active_for_workflow = _list_active_for_workflow
    workflows.delete = _delete_workflow
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_image_storage_service", lambda: image_storage)
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_story_audio_storage_service", lambda: audio_storage)

    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service._get_owned = _get_owned
    service.generic_stories = generic_stories
    service.child_books = child_books
    service.batch_jobs = batch_jobs
    service.workflows = workflows
    service.session = SimpleNamespace(commit=_commit)

    await service.delete(user_id, workflow_id)

    assert image_storage.deleted == [workflow_id]
    assert audio_storage.deleted == [workflow_id]
    assert not hasattr(child_books, "deleted_by_story")
    assert generic_stories.deleted == []
    assert workflows.deleted == [workflow]


@pytest.mark.asyncio
async def test_delete_workflow_cancels_active_batch_jobs_before_delete(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    workflow = _retry_workflow(id=workflow_id, generic_story_id=None, status="FAILED")
    job = SimpleNamespace(
        workflow_id=workflow_id,
        status=StoryBatchJobStatus.RUNNING,
        provider="google",
        provider_job_name="batches/delete-workflow-test",
        provider_state="JOB_STATE_PENDING",
        request_keys=["cover", "page_1"],
        missing_keys=[],
        error_message=None,
    )
    batches = _FakeBatchesClient()
    image_storage = SimpleNamespace(deleted=[])
    audio_storage = SimpleNamespace(deleted=[])
    workflows = SimpleNamespace(deleted=[])
    batch_jobs = SimpleNamespace(updated=[])
    commits = []

    async def _get_owned(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow_id
        return workflow

    async def _list_active_for_workflow(requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return [job]

    async def _update_job(updated_job):
        batch_jobs.updated.append(updated_job)
        return updated_job

    async def _delete_image_directory(story_id):
        image_storage.deleted.append(story_id)

    async def _delete_audio_directory(story_id):
        audio_storage.deleted.append(story_id)

    async def _delete_workflow(workflow_arg):
        workflows.deleted.append(workflow_arg)

    async def _commit():
        commits.append(True)

    batch_jobs.list_active_for_workflow = _list_active_for_workflow
    batch_jobs.update = _update_job
    image_storage.delete_story_directory = _delete_image_directory
    audio_storage.delete_story_directory = _delete_audio_directory
    workflows.delete = _delete_workflow
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_image_storage_service", lambda: image_storage)
    monkeypatch.setattr("app.service.generic_story_workflow_service.get_story_audio_storage_service", lambda: audio_storage)

    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service._get_owned = _get_owned
    service.batch_jobs = batch_jobs
    service.google_client = _FakeGoogleClient(batches)
    service.generic_stories = SimpleNamespace(deleted=[])
    service.child_books = SimpleNamespace()
    service.workflows = workflows
    service.session = SimpleNamespace(commit=_commit)

    await service.delete(user_id, workflow_id)

    assert batches.cancelled_names == ["batches/delete-workflow-test"]
    assert job.status == StoryBatchJobStatus.CANCELLED
    assert job.provider_state == "JOB_STATE_CANCELLED"
    assert job.missing_keys == ["cover", "page_1"]
    assert batch_jobs.updated == [job]
    assert image_storage.deleted == [workflow_id]
    assert audio_storage.deleted == [workflow_id]
    assert workflows.deleted == [workflow]
    assert commits == [True]


@pytest.mark.asyncio
async def test_execute_failure_preserves_failed_current_step():
    workflow = _retry_workflow(status="PENDING", current_step=None, image_plan_json=None)
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    rollback_calls = []

    async def _get_owned(user_id, workflow_id):
        return workflow

    async def _update(workflow_arg):
        return workflow_arg

    async def _commit():
        return None

    async def _rollback():
        rollback_calls.append(True)

    async def _execute_single_step(workflow_arg, step, *, public_base_url, payload):
        raise AppException("Image plan page count must match story JSON pages.")

    service._get_owned = _get_owned
    service.workflows = SimpleNamespace(update=_update)
    service.session = SimpleNamespace(commit=_commit, rollback=_rollback)
    service._execute_single_step = _execute_single_step

    with pytest.raises(AppException):
        await service.execute(
            uuid4(),
            workflow.id,
            GenericStoryWorkflowExecuteRequest(
                step_name=GenericStoryWorkflowStep.IMAGE_PLAN_GENERATION,
                skip_image_generation=False,
                skip_narration_generation=False,
            ),
            public_base_url="https://api.example.test",
        )

    assert workflow.status == "FAILED"
    assert workflow.current_step == "IMAGE_PLAN_GENERATION"
    assert rollback_calls == [True]


class _FakeGenericStories:
    def __init__(self, *, existing_by_title=None):
        self.created = None
        self.contents = None
        self.existing_by_title = existing_by_title or {}
        self.by_id = {story.id: story for story in self.existing_by_title.values() if hasattr(story, "id")}

    async def get_by_title(self, title):
        return self.existing_by_title.get(title)

    async def get_by_id(self, generic_story_id):
        return self.by_id.get(generic_story_id)

    async def create(self, **data):
        self.created = data
        story = SimpleNamespace(id=uuid4(), **data)
        self.by_id[story.id] = story
        return story

    async def upsert_contents(self, generic_story, contents):
        self.contents = (generic_story, contents)


class _FakeWorkflows:
    def __init__(self):
        self.created = None
        self.updated = []

    async def create(self, **data):
        self.created = data
        defaults = {
            "generic_story_id": None,
            "current_step": None,
            "error_message": None,
            "character_analysis_json": None,
            "scene_plan_json": None,
            "visual_bible_json": None,
            "story_json": None,
            "image_plan_json": None,
            "summary": None,
            "moral": None,
            "cover_image": None,
        }
        return SimpleNamespace(
            id=uuid4(),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            **defaults,
            **data,
        )

    async def update(self, workflow):
        self.updated.append(workflow)
        return workflow


class _FakeUpload:
    def __init__(self, filename, content, content_type="image/png"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


class _FakeImageStorage:
    def __init__(self):
        self.saved = []
        self.reduced_saved = []
        self.bytes_by_url = {}

    async def save_story_image(self, story_id, image_bytes, filename, public_base_url):
        self.saved.append((story_id, image_bytes, filename, public_base_url))
        return f"https://cdn.example.test/photo/stories/{story_id}/{filename}"

    async def save_story_reduced_image(self, story_id, image_bytes, filename, public_base_url):
        self.reduced_saved.append((story_id, image_bytes, filename, public_base_url))
        return f"https://cdn.example.test/photo/stories/{story_id}/reduced/{filename}"

    async def get_image_bytes(self, image_url):
        return self.bytes_by_url.get(image_url, b"image")


class _FakeAudioStorage:
    def __init__(self):
        self.saved = []

    async def save_story_page_audio(self, *, story_id, language, page_number, audio_bytes):
        self.saved.append((story_id, language, page_number, audio_bytes))
        return f"https://cdn.example.test/audio/stories/{story_id}/{language}/page_{page_number}.wav"


class _FakeGenericStoryContentRepository:
    def __init__(self, story):
        self.story = story
        self.updated_contents = []

    async def get_by_id(self, story_id):
        if self.story.id == story_id:
            return self.story
        return None

    async def update_content(self, content):
        self.updated_contents.append(content)
        return content


class _FakeBatchesClient:
    def __init__(self, provider_state="JOB_STATE_CANCELLED"):
        self.cancelled_names = []
        self.provider_state = provider_state

    async def cancel(self, *, name):
        self.cancelled_names.append(name)

    async def get(self, *, name):
        return SimpleNamespace(state=SimpleNamespace(name=self.provider_state))


class _FakeGoogleClient:
    def __init__(self, batches):
        self.aio = SimpleNamespace(batches=batches)


@pytest.mark.asyncio
async def test_cancel_generic_story_batch_job_marks_job_and_workflow_cancelled():
    user_id = uuid4()
    generic_story_id = uuid4()
    workflow_id = uuid4()
    batch_job_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        status="IN_PROGRESS",
        current_step="IMAGE_GENERATION",
        error_message=None,
    )
    job = SimpleNamespace(
        id=batch_job_id,
        generic_story_id=generic_story_id,
        workflow_id=workflow_id,
        job_type=StoryBatchJobType.IMAGE,
        status=StoryBatchJobStatus.RUNNING,
        provider_job_name="batches/test-generic-job",
        provider_state="JOB_STATE_PENDING",
        request_keys=["cover", "page_1"],
        missing_keys=[],
        error_message=None,
    )
    batches = _FakeBatchesClient()
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    service.generic_stories = SimpleNamespace(get_by_id=lambda story_id: None)
    service.batch_jobs = SimpleNamespace()
    service.workflows = SimpleNamespace()
    service.google_client = _FakeGoogleClient(batches)
    service.session = SimpleNamespace()

    async def _get_story(story_id):
        assert story_id == generic_story_id
        return SimpleNamespace(id=generic_story_id)

    async def _get_job(story_id, requested_batch_job_id):
        assert story_id == generic_story_id
        assert requested_batch_job_id == batch_job_id
        return job

    async def _get_workflow(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow_id
        return workflow

    async def _update_job(updated_job):
        return updated_job

    async def _update_workflow(updated_workflow):
        return updated_workflow

    async def _commit():
        return None

    service.generic_stories.get_by_id = _get_story
    service.batch_jobs.get_for_story = _get_job
    service.batch_jobs.update = _update_job
    service.workflows.get_for_user = _get_workflow
    service.workflows.update = _update_workflow
    service.session.commit = _commit

    response = await service.cancel_batch_job(
        user_id=user_id,
        generic_story_id=generic_story_id,
        batch_job_id=batch_job_id,
    )

    assert batches.cancelled_names == ["batches/test-generic-job"]
    assert job.status == StoryBatchJobStatus.CANCELLED
    assert job.provider_state == "JOB_STATE_CANCELLED"
    assert job.missing_keys == ["cover", "page_1"]
    assert workflow.status == "FAILED"
    assert workflow.current_step is None
    assert response["generic_story_id"] == generic_story_id
    assert response["workflow_id"] == workflow_id
    assert response["batch_job_id"] == batch_job_id
    assert response["status"] == "CANCELLED"
    assert response["workflow_status"] == "FAILED"


@pytest.mark.asyncio
async def test_cancel_generic_story_batch_job_rejects_completed_job():
    user_id = uuid4()
    generic_story_id = uuid4()
    workflow_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        generic_story_id=generic_story_id,
        workflow_id=workflow_id,
        job_type=StoryBatchJobType.IMAGE,
        status=StoryBatchJobStatus.SUCCEEDED,
        provider_job_name="batches/completed",
        provider_state="JOB_STATE_SUCCEEDED",
    )
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    service.generic_stories = SimpleNamespace()
    service.batch_jobs = SimpleNamespace()
    service.workflows = SimpleNamespace()

    async def _get_story(story_id):
        return SimpleNamespace(id=story_id)

    async def _get_job(story_id, batch_job_id):
        return job

    async def _get_workflow(requested_user_id, requested_workflow_id):
        return SimpleNamespace(id=requested_workflow_id, status="COMPLETED")

    service.generic_stories.get_by_id = _get_story
    service.batch_jobs.get_for_story = _get_job
    service.workflows.get_for_user = _get_workflow

    with pytest.raises(AppException) as exc_info:
        await service.cancel_batch_job(
            user_id=user_id,
            generic_story_id=generic_story_id,
            batch_job_id=job.id,
        )

    assert exc_info.value.code == "BATCH_JOB_ALREADY_COMPLETED"


@pytest.mark.asyncio
async def test_generic_story_batch_reconcile_logs_counts_and_job_details(caplog):
    service = GenericStoryBatchService.__new__(GenericStoryBatchService)
    job = SimpleNamespace(
        id=uuid4(),
        generic_story_id=uuid4(),
        workflow_id=uuid4(),
        job_type=StoryBatchJobType.IMAGE,
        status=StoryBatchJobStatus.RUNNING,
        provider_state="JOB_STATE_PENDING",
    )
    service.batch_jobs = SimpleNamespace()

    async def _list_reconcilable(limit):
        assert limit == 50
        return [job]

    async def _reconcile_batch_job(reconcile_job):
        assert reconcile_job == job
        return {
            "generic_story_id": job.generic_story_id,
            "workflow_id": job.workflow_id,
            "batch_job_id": job.id,
            "job_type": job.job_type.value,
            "status": "RUNNING",
            "provider_state": "JOB_STATE_PENDING",
            "action": "still_running",
            "message": "Provider state is JOB_STATE_PENDING",
        }

    service.batch_jobs.list_reconcilable = _list_reconcilable
    service._reconcile_batch_job = _reconcile_batch_job

    with caplog.at_level(logging.INFO, logger="app.service.generic_story_batch_service"):
        result = await service.reconcile_batch_jobs(limit=50)

    assert result["checked_count"] == 1
    assert result["processed_count"] == 0
    messages = [record.message for record in caplog.records]
    assert any("[generic_story_batch] event=reconcile_started job_count=1 limit=50" in message for message in messages)
    assert any("event=reconcile_job_started" in message and f"batch_job_id={job.id}" in message for message in messages)
    assert any("event=reconcile_job_completed" in message and "action=still_running" in message for message in messages)
    assert any("[generic_story_batch] event=reconcile_completed checked_count=1 processed_count=0" in message for message in messages)


@pytest.mark.asyncio
async def test_create_workflow_persists_requested_title(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    fake_workflows = _FakeWorkflows()
    service.workflows = fake_workflows
    service.generic_stories = _FakeGenericStories()
    service.session = SimpleNamespace(commit=lambda: None)

    async def _commit():
        return None

    service.session.commit = _commit

    response = await service.create(
        uuid4(),
        GenericStoryWorkflowCreateRequest(
            title="Journey to Mars",
            actual_story="Mira built a small rocket, visited Mars, and learned that curiosity grows when we ask careful questions.",
            age_group="3-6",
            theme="space adventure",
            genre="adventure",
            learning_goal="curiosity",
            illustration_type="Watercolor",
        ),
    )

    assert fake_workflows.created["title"] == "Journey to Mars"
    assert fake_workflows.created["requested_pages"] is None
    assert fake_workflows.created["input_request"]["title"] == "Journey to Mars"
    assert fake_workflows.created["input_request"]["illustration_type"] == "watercolor"
    assert "requested_pages" not in fake_workflows.created["input_request"]
    assert fake_workflows.created["ai_provider"] == "google"
    assert response.title == "Journey to Mars"


@pytest.mark.asyncio
async def test_create_workflow_rejects_duplicate_generic_story_title():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.workflows = _FakeWorkflows()
    service.generic_stories = _FakeGenericStories(
        existing_by_title={"Journey to Mars": SimpleNamespace(id=uuid4(), title="Journey to Mars")}
    )

    with pytest.raises(AppException) as exc_info:
        await service.create(
            uuid4(),
            GenericStoryWorkflowCreateRequest(
                title="Journey to Mars",
                actual_story=(
                    "Mira built a small rocket, visited Mars, and learned that curiosity grows "
                    "when we ask careful questions."
                ),
                age_group="3-6",
            ),
        )

    assert exc_info.value.code == "GENERIC_STORY_TITLE_EXISTS"


@pytest.mark.asyncio
async def test_create_workflow_rejects_unsupported_illustration_type():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.workflows = _FakeWorkflows()
    service.generic_stories = _FakeGenericStories()

    with pytest.raises(AppException) as exc_info:
        await service.create(
            uuid4(),
            GenericStoryWorkflowCreateRequest(
                title="Journey to Mars",
                actual_story=(
                    "Mira built a small rocket, visited Mars, and learned that curiosity grows "
                    "when we ask careful questions."
                ),
                age_group="3-6",
                illustration_type="claymation",
            ),
        )

    assert exc_info.value.code == "ILLUSTRATION_TYPE_UNSUPPORTED"


@pytest.mark.asyncio
async def test_create_workflow_requires_title_for_unique_generic_story_validation():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.workflows = _FakeWorkflows()
    service.generic_stories = _FakeGenericStories()

    with pytest.raises(AppException) as exc_info:
        await service.create(
            uuid4(),
            GenericStoryWorkflowCreateRequest(
                actual_story=(
                    "Mira built a small rocket, visited Mars, and learned that curiosity grows "
                    "when we ask careful questions."
                ),
                age_group="3-6",
            ),
        )

    assert exc_info.value.code == "GENERIC_STORY_TITLE_REQUIRED"


@pytest.mark.asyncio
async def test_upload_published_story_images_updates_all_content_languages(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    generic_story_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        generic_story_id=generic_story_id,
        status="COMPLETED",
        current_step=None,
        cover_image=None,
        story_json={
            "cover_image_url": "old-cover",
            "cover_image_dummy": True,
            "pages": [
                {"page_number": 1, "text": "Mira listened.", "image_url": "old-1", "image_dummy": True},
                {"page_number": 2, "text": "The bell rang.", "image_url": "old-2", "image_dummy": True},
            ],
        },
    )
    en_content = SimpleNamespace(
        language="en",
        story_json={
            "cover_image_url": "old-cover",
            "pages": [
                {"page_number": 1, "text": "Mira listened."},
                {"page_number": 2, "text": "The bell rang."},
            ],
        },
    )
    hi_content = SimpleNamespace(
        language="hi",
        story_json={
            "cover_image_url": "old-cover",
            "pages": [
                {"page_number": 1, "text": "Hindi page 1."},
                {"page_number": 2, "text": "Hindi page 2."},
            ],
        },
    )
    generic_story = SimpleNamespace(id=generic_story_id, cover_image=None, contents=[hi_content, en_content])
    image_storage = _FakeImageStorage()

    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_image_storage_service",
        lambda: image_storage,
    )
    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.optimize_display_image",
        lambda image_bytes, filename: b"reduced-" + image_bytes,
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.session = SimpleNamespace()
    service.workflows = _FakeWorkflows()
    service.generic_stories = _FakeGenericStoryContentRepository(generic_story)

    async def _commit():
        return None

    async def _get_owned(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow_id
        return workflow

    service.session.commit = _commit
    service._get_owned = _get_owned

    response = await service.upload_published_story_images(
        user_id,
        workflow_id,
        generic_story_id,
        {
            "cover": _FakeUpload("cover.png", b"cover"),
            "page_1": _FakeUpload("page-1.png", b"page-1"),
            "page2": _FakeUpload("page-2.png", b"page-2"),
        },
        public_base_url="https://api.example.test",
    )

    assert [item[2] for item in image_storage.saved] == ["cover.png", "page_1.png", "page_2.png"]
    assert [item[0] for item in image_storage.saved] == [generic_story_id, generic_story_id, generic_story_id]
    assert [item[2] for item in image_storage.reduced_saved] == ["cover.png", "page_1.png", "page_2.png"]
    assert [item[1] for item in image_storage.reduced_saved] == [b"reduced-cover", b"reduced-page-1", b"reduced-page-2"]
    assert response.updated_languages == ["en", "hi"]
    assert response.cover_image_url.endswith("/cover.png")
    assert response.page_image_urls[1].endswith("/page_1.png")
    assert response.reduced_cover_image_url.endswith("/reduced/cover.png")
    assert response.reduced_page_image_urls[1].endswith("/reduced/page_1.png")
    assert response.page_image_urls[2].endswith("/page_2.png")
    assert workflow.cover_image == response.cover_image_url
    assert workflow.story_json["cover_image_url"] == response.cover_image_url
    assert "cover_image_dummy" not in workflow.story_json
    assert workflow.story_json["pages"][0]["image_url"] == response.page_image_urls[1]
    assert "image_dummy" not in workflow.story_json["pages"][0]
    for content in (en_content, hi_content):
        assert content.story_json["cover_image_url"] == response.cover_image_url
        assert content.story_json["pages"][0]["image_url"] == response.page_image_urls[1]
        assert content.story_json["pages"][1]["image_url"] == response.page_image_urls[2]
    assert generic_story.cover_image == response.cover_image_url


@pytest.mark.asyncio
async def test_upload_published_story_audio_updates_requested_language(monkeypatch):
    user_id = uuid4()
    workflow_id = uuid4()
    generic_story_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        generic_story_id=generic_story_id,
        status="COMPLETED",
        current_step=None,
        language="en",
        story_json={
            "pages": [
                {"page_number": 1, "text": "Mira listened.", "audio_url": "old-en-1", "audio_dummy": True},
                {"page_number": 2, "text": "The bell rang.", "audio_url": "old-en-2", "tts_skipped": True},
            ],
            STORY_LANGUAGE_VARIANTS_KEY: {
                "en": {
                    "pages": [
                        {"page_number": 1, "text": "Mira listened.", "audio_url": "old-en-1"},
                        {"page_number": 2, "text": "The bell rang.", "audio_url": "old-en-2"},
                    ]
                },
                "hi": {
                    "pages": [
                        {"page_number": 1, "text": "Hindi page 1.", "audio_url": "old-hi-1"},
                        {"page_number": 2, "text": "Hindi page 2.", "audio_url": "old-hi-2"},
                    ]
                },
            },
        },
    )
    en_content = SimpleNamespace(
        language="en",
        story_json={
            "pages": [
                {"page_number": 1, "text": "Mira listened.", "audio_url": "old-en-1", "audio_dummy": True},
                {"page_number": 2, "text": "The bell rang.", "audio_url": "old-en-2", "tts_skipped": True},
            ],
        },
    )
    hi_content = SimpleNamespace(
        language="hi",
        story_json={
            "pages": [
                {"page_number": 1, "text": "Hindi page 1.", "audio_url": "old-hi-1"},
                {"page_number": 2, "text": "Hindi page 2.", "audio_url": "old-hi-2"},
            ],
        },
    )
    generic_story = SimpleNamespace(id=generic_story_id, cover_image=None, contents=[hi_content, en_content])
    audio_storage = _FakeAudioStorage()
    audio_durations = {b"hi-1": 2.0, b"hi-2": 4.0}

    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_story_audio_storage_service",
        lambda: audio_storage,
    )
    monkeypatch.setattr(
        GenericStoryWorkflowService,
        "_uploaded_wav_duration_seconds",
        staticmethod(lambda audio_bytes: audio_durations[audio_bytes]),
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.session = SimpleNamespace()
    service.workflows = _FakeWorkflows()
    service.generic_stories = _FakeGenericStoryContentRepository(generic_story)

    async def _commit():
        return None

    async def _get_owned(requested_user_id, requested_workflow_id):
        assert requested_user_id == user_id
        assert requested_workflow_id == workflow_id
        return workflow

    service.session.commit = _commit
    service._get_owned = _get_owned

    response = await service.upload_published_story_audio(
        user_id,
        workflow_id,
        generic_story_id,
        "hi",
        {
            "page_1": _FakeUpload("hi-1.wav", b"hi-1", "audio/wav"),
            "page2": _FakeUpload("hi-2.wav", b"hi-2", "audio/wav"),
        },
    )

    assert audio_storage.saved == [
        (workflow_id, "hi", 1, b"hi-1"),
        (workflow_id, "hi", 2, b"hi-2"),
    ]
    assert response.language == "hi"
    assert response.updated_languages == ["hi"]
    assert response.page_audio_urls[1].endswith("/hi/page_1.wav")
    assert response.page_audio_urls[2].endswith("/hi/page_2.wav")
    assert en_content.story_json["pages"][0]["audio_url"] == "old-en-1"
    assert en_content.story_json["pages"][1]["audio_url"] == "old-en-2"
    assert hi_content.story_json["pages"][0]["audio_url"] == response.page_audio_urls[1]
    assert hi_content.story_json["pages"][1]["audio_url"] == response.page_audio_urls[2]
    assert hi_content.story_json["pages"][0]["duration"] == 2.0
    assert hi_content.story_json["pages"][0]["word_timestamps"] == [
        {"word": "Hindi page 1.", "start": 0.0, "end": 2.0}
    ]
    assert hi_content.story_json["pages"][1]["duration"] == 4.0
    assert hi_content.story_json["pages"][1]["word_timestamps"] == [
        {"word": "Hindi page 2.", "start": 0.0, "end": 4.0}
    ]
    assert workflow.story_json["pages"][0]["audio_url"] == "old-en-1"
    assert workflow.story_json[STORY_LANGUAGE_VARIANTS_KEY]["hi"]["pages"][1]["audio_url"] == response.page_audio_urls[2]
    assert workflow.story_json[STORY_LANGUAGE_VARIANTS_KEY]["hi"]["pages"][1]["duration"] == 4.0
    assert workflow.story_json[STORY_LANGUAGE_VARIANTS_KEY]["hi"]["pages"][1]["word_timestamps"] == [
        {"word": "Hindi page 2.", "start": 0.0, "end": 4.0}
    ]


@pytest.mark.asyncio
async def test_publish_generic_story_creates_catalog_item_and_content(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.generic_stories = _FakeGenericStories()
    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_image_storage_service",
        lambda: _FakeImageStorage(),
    )
    workflow = SimpleNamespace(
        id=uuid4(),
        generic_story_id=None,
        age_group="3-6",
        language="en",
        title="The Moon Bell",
        summary="A child helps restore the moon bell.",
        theme="listening",
        genre="adventure",
        moral="Listening helps friends solve problems.",
        learning_goal="careful listening",
        cover_image="https://cdn.example.test/cover.png",
        input_request={"status": "inactive"},
        character_analysis_json={"characters": [{"type": "human"}, {"type": "animal"}]},
        status="IN_PROGRESS",
        story_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "cover_image_prompt": "Large rendered cover prompt",
            "cover_planned_image_prompt": "Short cover plan prompt",
            "pages": [
                {
                    "page_number": 1,
                    "text": "Mira listened.",
                    "image_prompt": "Large rendered page prompt",
                    "planned_image_prompt": "Short page plan prompt",
                    "tts_prompt": "Large rendered TTS prompt",
                },
                {"page_number": 2, "text": "The bell rang."},
            ],
            "moral": "Listening helps friends solve problems.",
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    await service._publish_generic_story(workflow, publish_status="active", public_base_url="https://api.example.test")

    assert service.generic_stories.created["title"] == "The Moon Bell"
    assert service.generic_stories.created["status"] == "active"
    assert service.generic_stories.created["total_pages"] == 2
    assert service.generic_stories.created["character_type"] == "animal, human"
    content_story_json = service.generic_stories.contents[1][0]["story_json"]
    assert service.generic_stories.contents[1][0]["language"] == "en"
    assert content_story_json["title"] == workflow.story_json["title"]
    assert "cover_image_prompt" not in content_story_json
    assert "cover_planned_image_prompt" not in content_story_json
    assert "image_prompt" not in content_story_json["pages"][0]
    assert "planned_image_prompt" not in content_story_json["pages"][0]
    assert "tts_prompt" not in content_story_json["pages"][0]
    assert workflow.story_json["pages"][0]["image_prompt"] == "Large rendered page prompt"
    assert workflow.generic_story_id is not None
    assert workflow.status == "COMPLETED"


@pytest.mark.asyncio
async def test_publish_generic_story_rejects_existing_title(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.generic_stories = _FakeGenericStories(
        existing_by_title={"The Moon Bell": SimpleNamespace(id=uuid4(), title="The Moon Bell")}
    )
    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_image_storage_service",
        lambda: _FakeImageStorage(),
    )
    workflow = SimpleNamespace(
        id=uuid4(),
        generic_story_id=None,
        age_group="3-6",
        language="en",
        title="The Moon Bell",
        summary="A child helps restore the moon bell.",
        theme="listening",
        genre="adventure",
        moral="Listening helps friends solve problems.",
        learning_goal="careful listening",
        cover_image=None,
        input_request={"status": "inactive"},
        character_analysis_json={"characters": [{"type": "human"}]},
        status="IN_PROGRESS",
        story_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "pages": [{"page_number": 1, "text": "Mira listened."}],
            "moral": "Listening helps friends solve problems.",
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(AppException) as exc_info:
        await service._publish_generic_story(
            workflow,
            publish_status="active",
            public_base_url="https://api.example.test",
        )

    assert exc_info.value.code == "GENERIC_STORY_TITLE_EXISTS"
    assert service.generic_stories.created is None
    assert workflow.generic_story_id is None


@pytest.mark.asyncio
async def test_publish_generic_story_expands_multilingual_variants_to_content_rows(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    service.generic_stories = _FakeGenericStories()
    image_storage = _FakeImageStorage()
    image_storage.bytes_by_url = {
        "https://cdn.example.test/cover.png": b"cover",
        "https://cdn.example.test/page-1.png": b"page-1",
    }
    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_image_storage_service",
        lambda: image_storage,
    )
    workflow = SimpleNamespace(
        id=uuid4(),
        generic_story_id=None,
        age_group="3-6",
        language="en",
        title="The Moon Bell",
        summary="A child helps restore the moon bell.",
        theme="listening",
        genre="adventure",
        moral="Listening helps friends solve problems.",
        learning_goal="careful listening",
        cover_image="https://cdn.example.test/cover.png",
        input_request={"status": "inactive"},
        character_analysis_json={"characters": [{"type": "human"}]},
        status="IN_PROGRESS",
        story_json={
            "title": "The Moon Bell",
            "summary": "A child helps restore the moon bell.",
            "cover_image_url": "https://cdn.example.test/cover.png",
            "cover_image_prompt": "Large rendered cover prompt.",
            "cover_planned_image_prompt": "Short cover plan prompt.",
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": "Mira listened.",
                    "image_url": "https://cdn.example.test/page-1.png",
                    "image_prompt": "Large rendered page prompt.",
                    "planned_image_prompt": "Short page plan prompt.",
                    "audio_url": "https://cdn.example.test/page-1.wav",
                    "tts_prompt": "Large rendered TTS prompt.",
                    "duration": 1.5,
                }
            ],
            "moral": "Listening helps friends solve problems.",
            STORY_LANGUAGE_VARIANTS_KEY: {
                "en": {
                    "title": "The Moon Bell",
                    "summary": "A child helps restore the moon bell.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "Mira listened."}],
                    "moral": "Listening helps friends solve problems.",
                },
                "hi": {
                    "title": "चाँद की घंटी",
                    "summary": "एक बच्ची चाँद की घंटी ठीक करने में मदद करती है.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "मीरा ने ध्यान से सुना."}],
                    "moral": "ध्यान से सुनना दोस्तों की मदद करता है.",
                },
                "mr": {
                    "title": "चंद्राची घंटा",
                    "summary": "एक मुलगी चंद्राची घंटा दुरुस्त करण्यात मदत करते.",
                    "pages": [{"page_number": 1, "emotion": "wonder", "text": "मीराने काळजीपूर्वक ऐकले."}],
                    "moral": "काळजीपूर्वक ऐकल्याने मित्रांना मदत होते.",
                },
            },
        },
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    await service._publish_generic_story(workflow, publish_status="active", public_base_url="https://api.example.test")

    contents = service.generic_stories.contents[1]
    by_language = {item["language"]: item["story_json"] for item in contents}
    generic_story_id = service.generic_stories.contents[0].id
    assert sorted(by_language) == ["en", "hi", "mr"]
    assert by_language["hi"]["pages"][0]["text"] == "मीरा ने ध्यान से सुना."
    assert by_language["mr"]["title"] == "चंद्राची घंटा"
    assert STORY_LANGUAGE_VARIANTS_KEY not in by_language["en"]
    for content_story_json in by_language.values():
        assert "cover_image_prompt" not in content_story_json
        assert "cover_planned_image_prompt" not in content_story_json
        assert "image_prompt" not in content_story_json["pages"][0]
        assert "planned_image_prompt" not in content_story_json["pages"][0]
        assert "tts_prompt" not in content_story_json["pages"][0]
    assert by_language["hi"]["pages"][0]["image_url"].endswith(f"/{generic_story_id}/page_1.png")
    assert "audio_url" not in by_language["hi"]["pages"][0]
    assert by_language["en"]["pages"][0]["audio_url"] == "https://cdn.example.test/page-1.wav"
    assert [item[0] for item in image_storage.saved] == [generic_story_id, generic_story_id]
    assert [item[2] for item in image_storage.saved] == ["cover.png", "page_1.png"]
