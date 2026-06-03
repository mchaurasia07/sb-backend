from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.entity.generic_story_workflow import GenericStoryWorkflow, GenericStoryWorkflowStep
from app.model.request.generic_story_workflow import GenericStoryWorkflowCreateRequest
from app.model.response.generic_story_workflow import GenericStoryWorkflowResponse
from app.service.generic_story_workflow_service import (
    STORY_LANGUAGE_VARIANTS_KEY,
    GenericStoryWorkflowService,
)


def test_generic_story_workflow_has_user_created_at_index():
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in GenericStoryWorkflow.__table__.indexes
    }

    assert indexes["ix_generic_story_workflows_user_created_at"] == ("user_id", "created_at")


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
        age_group="5-7",
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

    assert dumped["generic_story_id"] == generic_story_id.hex


def test_normalize_story_json_matches_existing_story_contract():
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    workflow = SimpleNamespace(
        age_group="5-7",
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
        age_group="5-7",
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
    workflow = SimpleNamespace(age_group="5-7", scene_plan_json={"pages": [{"page_number": 1}, {"page_number": 2}]})

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
    assert "VISUAL BIBLE" in workflow.story_json["cover_image_prompt"]
    assert "Round face, short black hair, yellow dress." in workflow.story_json["cover_image_prompt"]
    assert workflow.story_json["cover_planned_image_prompt"] == "Cover prompt with Mars and title."
    assert workflow.story_json["cover_image_dummy"] is True
    assert workflow.story_json["pages"][0]["image_url"].startswith("data:image/png;base64,")
    assert "VISUAL BIBLE" in workflow.story_json["pages"][0]["image_prompt"]
    assert "Page 1 Mars prompt." in workflow.story_json["pages"][0]["image_prompt"]
    assert workflow.story_json["pages"][0]["planned_image_prompt"] == "Page 1 Mars prompt."
    assert workflow.story_json["pages"][0]["image_dummy"] is True


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
        age_group="5-7",
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
        age_group="5-7",
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
        age_group="5-7",
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
            "visual_bible": {
                "characters": [{"name": "Mira", "appearance": "Yellow dress and short black hair."}]
            },
            "cover": {"image_prompt": "Cover prompt."},
            "pages": [{"page_number": 1, "image_prompt": "Page image prompt."}],
        },
    )
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)

    async def _get_owned(user_id, requested_workflow_id):
        assert requested_workflow_id == workflow_id
        return workflow

    service._get_owned = _get_owned

    steps = await service.get_steps(uuid4(), workflow_id)
    by_name = {step.step_name: step for step in steps}

    assert len(steps) == 7
    assert steps[0].genric_story_id == generic_story_id.hex
    assert by_name["CHARACTER_EXTRACTION"].status == "COMPLETED"
    assert by_name["CHARACTER_EXTRACTION"].summary["character_count"] == 2
    assert by_name["IMAGE_GENERATION"].summary["uses_dummy_images"] is True
    assert by_name["IMAGE_GENERATION"].output["visual_bible"]["characters"][0]["name"] == "Mira"
    assert by_name["IMAGE_GENERATION"].output["final_prompts"] == [
        {"page": "cover", "prompt": "Cover prompt."},
        {"page": 1, "prompt": "Page image prompt."},
    ]
    assert by_name["IMAGE_GENERATION"].output["pages"][0]["planned_image_prompt"] == "Page image prompt."
    assert by_name["NARRATION_GENERATION"].summary["uses_dummy_audio"] is True
    assert "professional children's audiobook narrator" in by_name["NARRATION_GENERATION"].output["pages"][0]["tts_prompt"]
    assert by_name["PUBLISH_GENERIC_STORY"].summary["generic_story_id"] == str(generic_story_id)

    image_steps = await service.get_steps(uuid4(), workflow_id, step_name="IMAGE_GENERATION")

    assert len(image_steps) == 1
    assert image_steps[0].step_name == "IMAGE_GENERATION"
    assert image_steps[0].output["final_prompts"][0]["page"] == "cover"


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


class _FakeGenericStories:
    def __init__(self):
        self.created = None
        self.contents = None

    async def get_by_title(self, title):
        return None

    async def create(self, **data):
        self.created = data
        return SimpleNamespace(id=uuid4(), **data)

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
        self.bytes_by_url = {}

    async def save_story_image(self, story_id, image_bytes, filename, public_base_url):
        self.saved.append((story_id, image_bytes, filename, public_base_url))
        return f"https://cdn.example.test/photo/stories/{story_id}/{filename}"

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


@pytest.mark.asyncio
async def test_create_workflow_persists_requested_title(monkeypatch):
    service = GenericStoryWorkflowService.__new__(GenericStoryWorkflowService)
    fake_workflows = _FakeWorkflows()
    service.workflows = fake_workflows
    service.session = SimpleNamespace(commit=lambda: None)

    async def _commit():
        return None

    service.session.commit = _commit

    response = await service.create(
        uuid4(),
        GenericStoryWorkflowCreateRequest(
            title="Journey to Mars",
            actual_story="Mira built a small rocket, visited Mars, and learned that curiosity grows when we ask careful questions.",
            age_group="5-7",
            requested_pages=6,
            theme="space adventure",
            genre="adventure",
            learning_goal="curiosity",
        ),
    )

    assert fake_workflows.created["title"] == "Journey to Mars"
    assert fake_workflows.created["input_request"]["title"] == "Journey to Mars"
    assert fake_workflows.created["ai_provider"] == "google"
    assert response.title == "Journey to Mars"


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
    assert response.updated_languages == ["en", "hi"]
    assert response.cover_image_url.endswith("/cover.png")
    assert response.page_image_urls[1].endswith("/page_1.png")
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

    monkeypatch.setattr(
        "app.service.generic_story_workflow_service.get_story_audio_storage_service",
        lambda: audio_storage,
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
    assert workflow.story_json["pages"][0]["audio_url"] == "old-en-1"
    assert workflow.story_json[STORY_LANGUAGE_VARIANTS_KEY]["hi"]["pages"][1]["audio_url"] == response.page_audio_urls[2]


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
        age_group="5-7",
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
            "pages": [
                {"page_number": 1, "text": "Mira listened."},
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
    assert service.generic_stories.contents[1] == [{"language": "en", "story_json": workflow.story_json}]
    assert workflow.generic_story_id is not None
    assert workflow.status == "COMPLETED"


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
        age_group="5-7",
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
            "pages": [
                {
                    "page_number": 1,
                    "emotion": "wonder",
                    "text": "Mira listened.",
                    "image_url": "https://cdn.example.test/page-1.png",
                    "audio_url": "https://cdn.example.test/page-1.wav",
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
    assert by_language["hi"]["pages"][0]["image_url"].endswith(f"/{generic_story_id}/page_1.png")
    assert "audio_url" not in by_language["hi"]["pages"][0]
    assert by_language["en"]["pages"][0]["audio_url"] == "https://cdn.example.test/page-1.wav"
    assert [item[0] for item in image_storage.saved] == [generic_story_id, generic_story_id]
    assert [item[2] for item in image_storage.saved] == ["cover.png", "page_1.png"]
