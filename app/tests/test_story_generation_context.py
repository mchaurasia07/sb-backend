from types import SimpleNamespace
import json

import pytest

from app.core.exceptions import AppException
from app.entity.story import AgeGroup
from app.service.ai.base import TextGenerationResult
from app.service.story_narration_profile import build_page_narration
from app.service.story_service import StoryService, _compact_json, _normalize_story_output
from app.utils.prompt_loader import load_prompt


def test_build_story_generation_context_reduces_story_plan_to_narrative_fields():
    story_plan = {
        "title": "Mira and the Moon Map",
        "summary": "Mira learns to solve a problem step by step.",
        "theme": "patience and planning",
        "learning_goal": "solve problems step by step",
        "moral_theme": "patience and planning",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "central_problem": "The moon map opens only when Mira follows each clue carefully.",
        "hero_want": "Mira wants to find the moonlit reading nook.",
        "emotional_need": "Mira needs to trust slow, careful steps.",
        "stakes": "If Mira rushes, the glowing path fades before she can follow it.",
        "climax_choice": "Mira pauses, checks the final clue, and chooses the careful path.",
        "resolution_payoff": "The reading nook opens and Mira feels proud of her patience.",
        "moral_explanation": "Careful steps can solve a big puzzle.",
        "story_spine": {
            "hero_want": "Mira wants to find the moonlit reading nook.",
            "blocking_problem": "The map opens only when Mira follows each clue carefully.",
            "failed_attempt": "Mira rushes and the silver path fades.",
            "lesson_learned": "Mira learns to solve one clue at a time.",
            "climax_choice": "Mira pauses and chooses the careful path.",
            "resolution": "The reading nook opens and Mira feels proud.",
        },
        "language_profile": {
            "reading_stage": "Early Reader",
            "sentence_length": "5-12 words per sentence",
            "vocabulary_level": "simple everyday vocabulary",
            "repetition_level": "light repetition",
            "dialogue_complexity": "short dialogue",
        },
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira finds a folded moon map.",
                "characters_present": ["Mira"],
                "child_action": "Mira opens the moon map and traces the first silver path.",
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
                "growth_step": "Mira practices slowing down before choosing.",
                "domain_detail": "The moon map glows with one silver path.",
                "page_turn_hook": "A silver path begins to glow.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
        ],
    }

    reduced = StoryService._build_story_generation_context(story_plan)

    assert reduced == {
        "title": "Mira and the Moon Map",
        "summary": "Mira learns to solve a problem step by step.",
        "theme": "patience and planning",
        "learning_goal": "solve problems step by step",
        "moral_theme": "patience and planning",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "central_problem": "The moon map opens only when Mira follows each clue carefully.",
        "hero_want": "Mira wants to find the moonlit reading nook.",
        "emotional_need": "Mira needs to trust slow, careful steps.",
        "stakes": "If Mira rushes, the glowing path fades before she can follow it.",
        "climax_choice": "Mira pauses, checks the final clue, and chooses the careful path.",
        "resolution_payoff": "The reading nook opens and Mira feels proud of her patience.",
        "moral_explanation": "Careful steps can solve a big puzzle.",
        "story_spine": {
            "hero_want": "Mira wants to find the moonlit reading nook.",
            "blocking_problem": "The map opens only when Mira follows each clue carefully.",
            "failed_attempt": "Mira rushes and the silver path fades.",
            "lesson_learned": "Mira learns to solve one clue at a time.",
            "climax_choice": "Mira pauses and chooses the careful path.",
            "resolution": "The reading nook opens and Mira feels proud.",
        },
        "language_profile": {
            "reading_stage": "Early Reader",
            "sentence_length": "5-12 words per sentence",
            "vocabulary_level": "simple everyday vocabulary",
            "repetition_level": "light repetition",
            "dialogue_complexity": "short dialogue",
        },
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira finds a folded moon map.",
                "characters_present": ["Mira"],
                "child_action": "Mira opens the moon map and traces the first silver path.",
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
                "growth_step": "Mira practices slowing down before choosing.",
                "domain_detail": "The moon map glows with one silver path.",
                "page_turn_hook": "A silver path begins to glow.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
        ],
    }


def test_story_generation_prompt_aligns_with_story_plan_contract():
    prompt = load_prompt("prompts/story/story_generation_prompt.txt")

    assert "## STORY SPINE AUTHORITY" in prompt
    assert "Use story_spine as the narrative backbone" in prompt
    assert "Want -> Attempt -> Result -> New Challenge -> Better Attempt -> Resolution" in prompt
    assert "Write each page as a lived story moment, not a summary" in prompt
    assert "Avoid page-summary writing" in prompt
    assert "Keep the selected child's want, feelings, choices, and growth central" in prompt
    assert "The child remains the actor who changes the outcome" in prompt
    assert "Follow language_profile as the primary guide for vocabulary, sentence length" in prompt
    assert "Age band fallback targets" in prompt
    assert "read-aloud and audiobook narration" in prompt
    assert "The final story page must show the resolution_payoff" in prompt
    assert "before the moral field explains it" in prompt
    assert "- child_action" in prompt
    assert '"pages": [' in prompt
    assert '"page_number": 1' in prompt
    assert '"emotion": ""' in prompt
    assert '"text": ""' in prompt
    assert '"moral": ""' in prompt


def test_cast_mode_prompt_paths_are_split():
    child_story = SimpleNamespace(use_child_character=True)
    imagined_story = SimpleNamespace(use_child_character=False)

    assert StoryService._story_plan_prompt_path(child_story).endswith("story_plan_child_hero_prompt.txt")
    assert StoryService._story_plan_prompt_path(imagined_story).endswith("story_plan_imagined_cast_prompt.txt")
    assert StoryService._story_generation_prompt_path(child_story).endswith("story_generation_child_hero_prompt.txt")
    assert StoryService._story_generation_prompt_path(imagined_story).endswith(
        "story_generation_imagined_cast_prompt.txt"
    )


def test_imagined_cast_story_plan_prompt_does_not_force_selected_child_as_hero():
    template = load_prompt("prompts/story/story_plan_imagined_cast_prompt.txt")
    story = SimpleNamespace(age_group=AgeGroup.EARLY_READER)
    child = SimpleNamespace(
        first_name="Mira",
        gender="female",
        age=6,
        character_metadata={},
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={
            "category": "space adventure",
            "learning_goal": "problem solving",
            "context": "A story about a rocket that loses its map.",
        },
        theme="space adventure",
        hobby="reading",
        pages=8,
        character_context={
            "use_child_character": False,
            "cast_mode": StoryService.CAST_MODE_IMAGINED,
            "character_description": "Invent the best story hero from the inputs.",
            "child_age_label": "Early Reader",
            "child_age_visual_guidance": "age-appropriate proportions for the reader band",
            "cast_mode_instructions": "IMAGINED_CAST: create a named hero and complete recurring cast.",
        },
    )

    assert "The hero is always the selected child" not in prompt
    assert "The selected child solves the conflict" not in prompt
    assert "Do NOT use the selected child profile as a story character" in prompt
    assert 'visual_bible.hero.character_id must NOT be "hero_child"' in prompt
    assert "Mira" not in prompt


def test_story_generation_imagined_prompt_preserves_visual_bible_hero():
    prompt = load_prompt("prompts/story/story_generation_imagined_cast_prompt.txt")

    assert "Keep the selected child as the hero" not in prompt
    assert "Use the hero in visual_bible.hero as the main character" in prompt
    assert "The selected child profile is not introduced as a character" in prompt
    assert "child_action means the invented hero's planned action" in prompt


@pytest.mark.asyncio
async def test_child_hero_image_plan_attaches_child_character_reference():
    child = SimpleNamespace(first_name="Mira", character_image_url="https://cdn.test/mira.png")

    class _Children:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return child

    service = StoryService.__new__(StoryService)
    service.children = _Children()
    story = SimpleNamespace(
        id="story-id",
        user_id="user-id",
        child_id="child-id",
        use_child_character=True,
        ai_provider="openai",
        title="Mira's Map",
    )
    image_plan = {
        "visual_bible": {"hero": {"name": "Mira", "character_id": "hero_child"}},
        "cover": {"title_text": "Mira's Map"},
        "character_reference_manifest": [],
    }

    result = await service._ensure_image_plan_character_references(story, image_plan)

    manifest = result["character_reference_manifest"]
    assert manifest[0]["character_id"] == "hero_child"
    assert manifest[0]["reference_image_url"] == "https://cdn.test/mira.png"
    assert result["visual_bible"]["hero"]["reference_image_url"] == "https://cdn.test/mira.png"


@pytest.mark.asyncio
async def test_imagined_cast_image_plan_does_not_attach_child_character_reference():
    class _Children:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            raise AssertionError("imagined cast should not load child character reference")

    service = StoryService.__new__(StoryService)
    service.children = _Children()
    story = SimpleNamespace(
        id="story-id",
        user_id="user-id",
        child_id="child-id",
        use_child_character=False,
        ai_provider="openai",
        title="Robot Map",
    )
    image_plan = {
        "visual_bible": {
            "hero": {
                "name": "Brave Blue Robot",
                "character_id": "brave_blue_robot",
                "appearance": "A small rounded blue robot.",
            }
        },
        "cover": {"title_text": "Robot Map"},
        "character_reference_manifest": [],
    }

    result = await service._ensure_image_plan_character_references(story, image_plan)

    assert result["character_reference_manifest"] == []
    assert "reference_image_url" not in result["visual_bible"]["hero"]


def test_story_generation_context_softens_medical_harm_language():
    story_plan = {
        "title": "The Sparkling Park",
        "summary": "Pollution is making people sick near the park.",
        "theme": "Adventure, Environmental Awareness",
        "learning_goal": "Environmental Awareness",
        "moral_theme": "Caring for nature helps everyone.",
        "setting": "a community park",
        "tone": "hopeful",
        "central_problem": "People fall ill because the park has become an unhealthy health risk.",
        "hero_want": "Amayra wants the park to feel bright again.",
        "emotional_need": "Amayra needs to feel capable of helping.",
        "stakes": "Animals get sick and families stop playing there.",
        "climax_choice": "Amayra invites neighbors to clean the stream together.",
        "resolution_payoff": "The park feels clean and cheerful again.",
        "moral_explanation": "Small helpful actions can grow when people work together.",
        "content_anchors": {},
        "visual_bible": {},
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "People getting sick makes the park feel sad.",
                "characters_present": ["Amayra"],
                "emotional_beat": "concern",
                "learning_goal_integration": "Amayra notices what needs care.",
                "growth_step": "Amayra looks closely before acting.",
                "domain_detail": "litter near a stream",
                "page_turn_hook": "She spots a cleanup sign.",
                "continuity_requirements": [],
            }
        ],
    }

    reduced = StoryService._build_story_generation_context(story_plan)
    combined = " ".join(
        [
            reduced["summary"],
            reduced["central_problem"],
            reduced["stakes"],
            reduced["pages"][0]["scene_description"],
        ]
    ).lower()

    assert "fall ill" not in combined
    assert "sick" not in combined
    assert "health risk" not in combined
    assert "unclean" in combined
    assert "hard to enjoy" in combined or "cannot enjoy" in combined


def test_story_plan_template_renders_all_current_placeholders():
    template = load_prompt("prompts/story/story_plan_prompt.txt")
    story = SimpleNamespace(age_group=AgeGroup.EARLY_READER)
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        gender="girl",
        character_image_url="https://example.test/character.png",
        character_metadata={"description": "A curious child with bright eyes.", "style": "storybook"},
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={"learning_goal": "problem solving", "context": "moonlit library"},
        theme="adventure",
        hobby="reading",
        pages=10,
        character_context={
            "character_description": "A curious child with bright eyes.",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
    )

    assert "{character_profile_json}" not in prompt
    assert "{first_name}" not in prompt
    assert "{pages}" not in prompt
    assert "Mira" in prompt
    assert '"profile_summary": "A curious child with bright eyes."' in prompt


def test_story_plan_prompt_uses_safe_intent_and_omits_unrelated_trigger_terms():
    template = load_prompt("prompts/story/story_plan_prompt.txt")
    story = SimpleNamespace(age_group=AgeGroup.EARLY_READER)
    child = SimpleNamespace(
        first_name="Amayra",
        age=7,
        gender="female",
        character_image_url="https://example.test/character.png",
        character_metadata={
            "style": "storybook",
            "identity_profile": {
                "face_shape": "round",
                "skin_tone": "warm medium",
                "eye_color": "dark brown",
                "eye_shape": "almond",
                "mouth_shape": "medium width lips",
                "mouth_description": "closed-mouth smile",
                "smile_type": "closed-mouth smile",
                "hair_color": "dark brown",
                "hair_style": "two pigtails",
                "hair_length": "medium",
                "distinctive_features": ["bright eyes", "small tooth gap"],
            },
        },
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={
            "learning_goal": "Kindness and personal hygiene",
            "context": "Story to teach her kindness with people and if someone says no she should respect it",
        },
        theme="Family",
        hobby="reading",
        pages=8,
        character_context={
            "use_child_character": True,
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO,
            "character_description": "Detailed identity is reserved for image generation.",
            "child_age_label": "7 years old",
            "child_age_visual_guidance": "older child proportions with natural child build",
        },
    )
    lowered = prompt.lower()

    assert "someone says no" not in lowered
    assert "respect it" not in lowered
    assert "personal hygiene" not in lowered
    assert "asks for space" in lowered
    assert "daily self-care routines such as brushing teeth and washing hands" in lowered
    assert "pool" not in lowered
    assert "swim" not in lowered
    assert "upper body" not in lowered
    assert "mouth" not in lowered
    assert "lip" not in lowered
    assert "tooth" not in lowered
    assert '"central_problem": ""' in prompt
    assert '"story_spine": {' in prompt
    assert '"failed_attempt": ""' in prompt
    assert '"language_profile": {' in prompt
    assert '"sentence_length": ""' in prompt
    assert "Follow language_profile" not in prompt
    assert "1-8 words per sentence" in prompt
    assert "5-12 words per sentence" in prompt
    assert "8-18 words per sentence" in prompt
    assert "Each page should happen because of the previous page" in prompt
    assert "The first meaningful attempt must not completely solve the problem" in prompt
    assert '"visual_bible": {' in prompt
    assert '"character_id": "hero_child"' in prompt
    assert '"hair_lock": ""' in prompt
    assert '"outfit_lock": ""' in prompt
    assert '"body_scale_lock": ""' in prompt
    assert '"relative_size": ""' in prompt
    assert 'visual_bible.hero.character_id must be "hero_child"' in prompt
    assert "Use the exact Visual Bible names in pages.characters_present" in prompt
    assert "same child height, build, proportions" in prompt
    assert '"role": ""' in prompt


@pytest.mark.asyncio
async def test_story_plan_generation_retries_with_compact_prompt_on_google_safety_block():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Amayra",
                age=7,
                gender="female",
                character_metadata={
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "mouth_shape": "medium width lips",
                        "hair_color": "dark brown",
                        "hair_style": "two pigtails",
                    }
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    class _FakeProvider:
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise AppException(
                    "Empty response from Google API prompt_feedback={'block_reason': "
                    "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                    code="EMPTY_RESPONSE",
                )
            plan = {
                "title": "Amayra's Kind Choice",
                "summary": "Amayra practices listening kindly.",
                "pages": [{"page_number": 1}],
                "visual_bible": {"hero": {"name": "Amayra", "appearance": "friendly child", "outfit": "blue dress"}},
            }
            return TextGenerationResult(
                text=json.dumps(plan),
                prompt_used=prompt,
                model="fake-model",
                metadata={"provider": "fake", "finish_reason": "STOP"},
            )

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    story = SimpleNamespace(
        id="story-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        category="Family",
        learning_goal="Kindness",
        context="Story to teach her kindness with people and if someone says no she should respect it",
        event_description=None,
        input_request={"use_child_character": True},
    )

    plan = await service._step_generate_plan(story, SimpleNamespace())

    assert len(service._ai_provider.prompts) == 2
    assert "SAFE STORY REQUEST JSON" in service._ai_provider.prompts[1]
    assert "someone says no" not in service._ai_provider.prompts[1].lower()
    assert plan["source_inputs"] == {
        "category": "Family",
        "learning_goal": "Kindness",
        "context": "Story to teach her kindness with people and if someone says no she should respect it",
    }
    step = service.story_steps.created[0]
    assert step.status.value == "COMPLETED"
    assert step.error_message is None
    assert step.prompt == service._ai_provider.prompts[1]


@pytest.mark.asyncio
async def test_standard_image_plan_generation_retries_with_compact_prompt_on_google_safety_block():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Mira",
                age=6,
                character_image_url="/media/mira.png",
                character_metadata={
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "hair_color": "dark brown",
                        "hair_style": "side-swept bob",
                    }
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeProvider:
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise AppException(
                    "Empty response from Google API prompt_feedback={'block_reason': "
                    "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                    code="EMPTY_RESPONSE",
                )
            image_plan = {
                "visual_bible": {
                    "hero": {
                        "character_id": "hero_child",
                        "name": "Mira",
                        "appearance": "Mira has a round face and side-swept bob.",
                        "outfit": "blue tunic with white sneakers",
                        "signature_item": "star bracelet",
                    },
                    "companion": {"appearance": ""},
                    "recurring_characters": [],
                },
                "character_reference_manifest": [
                    {"character_id": "hero_child", "name": "Mira", "role": "hero_child"}
                ],
                "cover": {
                    "title_text": "Mira's Map",
                    "visual_focus": "Mira holds a glowing map.",
                    "emotion": "wonder",
                    "characters_present": ["Mira"],
                    "reference_character_ids": ["hero_child"],
                    "image_prompt": "Mira holds a glowing map.",
                },
                "pages": [
                    {
                        "page_number": 1,
                        "story_role": "opening",
                        "visual_importance": "medium",
                        "emotion": "wonder",
                        "scene_action": "Mira opens the map.",
                        "environment": "cozy library",
                        "characters_present": ["Mira"],
                        "reference_character_ids": ["hero_child"],
                        "image_prompt": "Mira opens the map in a cozy library.",
                    }
                ],
                "back_cover": {
                    "emotion": "calm",
                    "characters_present": ["Mira"],
                    "reference_character_ids": ["hero_child"],
                    "image_prompt": "Mira smiles with the map.",
                },
            }
            return TextGenerationResult(
                text=json.dumps(image_plan),
                prompt_used=prompt,
                model="fake-model",
                metadata={"provider": "fake", "finish_reason": "STOP"},
            )

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    story = SimpleNamespace(
        id="story-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        title="Mira's Map",
        input_request={"use_child_character": True},
        use_child_character=True,
    )
    story_plan = {
        "title": "Mira's Map",
        "visual_bible": {
            "hero": {
                "appearance": (
                    "Mira keeps the same skin tone, body proportions, upper body pose, "
                    "mouth, lips, and teeth."
                ),
                "outfit": "rash guard, swim shorts, and leggings",
            }
        },
        "pages": [{"page_number": 1, "visual_brief": "Mira opens a map with no horror imagery."}],
    }
    story_json = {"title": "Mira's Map", "pages": [{"page_number": 1, "text": "Mira opened the map."}]}

    image_plan = await service._step_generate_image_plan(story, story_plan, story_json, SimpleNamespace())

    assert len(service._ai_provider.prompts) == 2
    assert "children's picture-book illustration planner" in service._ai_provider.prompts[1]
    assert "Avoid sensitive negative phrasing" in service._ai_provider.prompts[1]
    assert image_plan["pages"][0]["reference_character_ids"] == ["hero_child"]


@pytest.mark.asyncio
async def test_custom_image_plan_generation_uses_safe_prompt_without_safety_fallback():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Mira",
                age=6,
                character_image_url="/media/mira.png",
                character_metadata={
                    "identity_summary": (
                        "Mira has a round face, warm medium skin tone, brown almond eyes, "
                        "a gentle mouth, and age-appropriate body proportions."
                    ),
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "hair_color": "dark brown",
                        "hair_style": "side-swept bob",
                    },
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeProvider:
        text_model = "fake-google-text-model"

        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            raise AppException(
                "Empty response from Google API prompt_feedback={'block_reason': "
                "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                code="EMPTY_RESPONSE",
            )

    class CustomStoryWorkflow(SimpleNamespace):
        pass

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    workflow = CustomStoryWorkflow(
        id="workflow-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        title="Mira's Map",
        input_request={"use_child_character": True},
        use_child_character=True,
    )
    story_plan = {
        "title": "Mira's Map",
        "visual_bible": {
            "hero": {
                "appearance": (
                    "Mira keeps the same skin tone, body proportions, upper body pose, "
                    "mouth, lips, and teeth."
                ),
                "outfit": "rash guard, swim shorts, and leggings",
            }
        },
        "pages": [{"page_number": 1, "visual_brief": "Mira opens a map with no horror imagery."}],
    }
    story_json = {"title": "Mira's Map", "pages": [{"page_number": 1, "text": "Mira opened the map."}]}

    with pytest.raises(AppException) as exc_info:
        await service._step_generate_image_plan(workflow, story_plan, story_json, SimpleNamespace())

    assert exc_info.value.code == "EMPTY_RESPONSE"
    assert len(service._ai_provider.prompts) == 1
    prompt = service._ai_provider.prompts[0]
    lowered = prompt.lower()
    assert "safe character planning summary" in lowered
    assert "character identity lock" not in lowered
    assert "skin tone" not in lowered
    assert "body proportions" not in lowered
    assert "upper body" not in lowered
    assert "rash guard" not in lowered
    assert "swim shorts" not in lowered
    assert "leggings" not in lowered
    assert "horror" not in lowered
    assert "aggressive" not in lowered
    assert "frightening" not in lowered
    assert "mouth" not in lowered
    assert "lips" not in lowered
    assert "teeth" not in lowered


def test_character_context_uses_identity_profile_not_legacy_analysis_text():
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        character_metadata={
            "identity_profile": {
                "face_shape": "round",
                "skin_tone": "warm medium",
                "eye_color": "brown",
                "eye_shape": "almond",
                "eyebrow_shape": "soft arched",
                "nose_shape": "small rounded",
                "mouth_description": "gentle smile",
                "hair_color": "dark brown",
                "hair_style": "side-swept",
                "hair_length": "short",
                "distinctive_features": ["round cheeks"],
            },
            "analysis_text": "OLD TEXT: large head, large eyes, exaggerated cartoon child",
            "description": "fallback description",
        },
    )

    context = StoryService._extract_character_analysis(child)

    assert "Stable Visual Identity" in context
    assert "Face shape: round" in context
    assert "OLD TEXT" not in context
    assert "large head" not in context
    assert "fallback description" not in context


def test_character_reference_context_prefers_persisted_identity_summary():
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        character_metadata={
            "identity_summary": "Persisted summary from child profile metadata.",
            "identity_profile": {
                "identity_summary": "Nested summary from identity profile.",
                "face_shape": "round",
            },
        },
    )

    context = StoryService._build_character_reference_context(child)

    assert context["identity_summary"] == "Persisted summary from child profile metadata."


def test_prompt_character_identity_lock_formats_summary_profile_and_age():
    character_context = {
        "child_name": "Mira",
        "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
        "identity_summary": "Persisted summary from child profile metadata.",
        "child_age_label": "6 years old",
        "child_age_visual_guidance": "early-reader child proportions",
    }

    lock = StoryService._format_prompt_character_identity_lock(character_context)

    assert "Hero child name: Mira" in lock
    assert "Identity summary: Persisted summary from child profile metadata." in lock
    assert "Name: Mira" in lock
    assert "Child age: 6 years old" in lock
    assert "Age/body guidance: early-reader child proportions" in lock


def test_story_image_prompt_renders_child_name():
    template = load_prompt("prompts/story/image_generation_prompt.txt")

    prompt = StoryService._render_story_image_prompt(
        template,
        {"hero": {"name": "Mira", "appearance": "round face", "outfit": "blue dress"}},
        "Mira opens a glowing moon map.",
        {
            "child_name": "Mira",
            "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
            "identity_summary": "Mira has a round face and side-swept dark hair.",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
        page_type="story_page",
        target_aspect_ratio="1:1",
        page_data={"page_number": 1, "image_prompt": "Mira opens a glowing moon map."},
    )

    assert "## Hero Child Name" in prompt
    assert "## Character Identity Lock" in prompt
    assert "Child age: 6 years old" in prompt
    assert "Age/body guidance: early-reader child proportions" in prompt
    assert '"character_identity"' not in prompt
    assert "Mira has a round face and side-swept dark hair." in prompt
    assert "Stable Visual Identity" in prompt
    assert "Mira" in prompt
    assert "{child_name}" not in prompt
    assert "{story_title}" not in prompt


def test_cover_image_prompt_includes_exact_story_title():
    template = load_prompt("prompts/story/image_generation_prompt.txt")

    prompt = StoryService._render_story_image_prompt(
        template,
        {"hero": {"name": "Mira", "appearance": "round face", "outfit": "blue dress"}},
        "Mira smiles under a glowing moon map.",
        {
            "child_name": "Mira",
            "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
        page_type="cover",
        target_aspect_ratio="4:5",
        page_data={"image_prompt": "Mira smiles under a glowing moon map."},
        story_title="Mira and the Moon Map",
    )

    assert "## Story Title" in prompt
    assert "Mira and the Moon Map" in prompt
    assert '"title_text": "Mira and the Moon Map"' in prompt
    assert "render the exact Story Title" in prompt
    assert "black rectangle" in prompt


def test_compact_json_serializes_story_generation_context_without_pretty_whitespace():
    reduced = {
        "title": "Tiny Test",
        "summary": "Short.",
        "theme": "focus",
        "learning_goal": "focus",
        "moral_theme": "paying attention",
        "setting": "a playroom",
        "tone": "warm",
        "visual_bible": {},
        "pages": [{"page_number": 1, "story_role": "introduction"}],
    }

    assert _compact_json(reduced) == (
        '{"title":"Tiny Test","summary":"Short.","theme":"focus","learning_goal":"focus",'
        '"moral_theme":"paying attention","setting":"a playroom","tone":"warm","visual_bible":{},'
        '"pages":[{"page_number":1,"story_role":"introduction"}]}'
    )


def test_normalize_story_output_adds_deterministic_page_narration():
    raw_story_json = {
        "title": "Mira and the Moon Map",
        "summary": "Mira solves a moonlit puzzle.",
        "theme": "patience",
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Mira opened the moon map and watched silver paths appear.",
            }
        ],
        "moral": "Small patient steps can solve big puzzles.",
    }
    plan = {"title": "Mira and the Moon Map", "theme": "patience", "pages": [{"page_number": 1}]}
    story = SimpleNamespace(
        category="adventure",
        event_description=None,
        learning_goal=None,
        context=None,
        age_group="3-6",
    )

    normalized = _normalize_story_output(raw_story_json, plan, story)

    assert normalized["pages"] == [
        {
            "page_number": 1,
            "text": "Mira opened the moon map and watched silver paths appear.",
            "emotion": "wonder",
            "narration": {
                "tone": "curious",
                "pace": "slow",
                "voice_style": "warm animated storyteller",
            },
        }
    ]
    assert "speech_narration" not in normalized["pages"][0]
    assert normalized["moral"] == "Small patient steps can solve big puzzles."


def test_normalize_story_output_rejects_page_count_mismatch():
    raw_story_json = {
        "title": "Mira and the Moon Map",
        "pages": [{"page_number": 1, "emotion": "wonder", "text": "One page only."}],
        "moral": "Keep trying.",
    }
    plan = {"title": "Mira and the Moon Map", "pages": [{"page_number": 1}, {"page_number": 2}]}
    story = SimpleNamespace(
        category="adventure",
        event_description=None,
        learning_goal=None,
        context=None,
        age_group="3-6",
    )

    with pytest.raises(AppException, match="expected 2"):
        _normalize_story_output(raw_story_json, plan, story)


def test_build_page_narration_maps_emotion_and_age_group():
    assert build_page_narration("triumph", "6-9") == {
        "tone": "celebratory",
        "pace": "medium",
        "voice_style": "expressive adventure storyteller",
    }
    assert build_page_narration("unknown", "0-3") == {
        "tone": "curious",
        "pace": "slow",
        "voice_style": "gentle lullaby bedtime storyteller",
    }
    assert build_page_narration("unknown", "2-4")["voice_style"] == "gentle lullaby bedtime storyteller"


def test_build_image_plan_context_reduces_story_plan_and_story_json_to_visual_fields():
    story_plan = {
        "title": "Mira and the Moon Map",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira opens a glowing atlas.",
                "characters_present": ["Mira"],
                "child_action": "Mira points to the silver path.",
                "emotional_beat": "quiet wonder",
                "domain_detail": "The atlas shows one silver path across the page.",
                "continuity_requirements": ["Mira keeps the atlas open."],
            }
        ],
    }
    story_json = {
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Moonlight slipped across the atlas while Mira traced the first path.",
            }
        ],
    }

    compact_story_plan, compact_story_json = StoryService._build_image_plan_context(story_plan, story_json)

    assert compact_story_plan == {
        "title": "Mira and the Moon Map",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira opens a glowing atlas.",
                "characters_present": ["Mira"],
                "child_action": "Mira points to the silver path.",
                "emotional_beat": "quiet wonder",
                "domain_detail": "The atlas shows one silver path across the page.",
                "continuity_requirements": ["Mira keeps the atlas open."],
            }
        ],
    }
    assert compact_story_json == {
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Moonlight slipped across the atlas while Mira traced the first path.",
            }
        ],
    }
