from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.entity.story import AgeGroup
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
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
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
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
        ],
    }


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
        age_group="5-7",
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
        age_group="5-7",
    )

    with pytest.raises(AppException, match="expected 2"):
        _normalize_story_output(raw_story_json, plan, story)


def test_build_page_narration_maps_emotion_and_age_group():
    assert build_page_narration("triumph", "8-12") == {
        "tone": "celebratory",
        "pace": "medium",
        "voice_style": "expressive cinematic storyteller",
    }
    assert build_page_narration("unknown", "2-4") == {
        "tone": "curious",
        "pace": "slow",
        "voice_style": "gentle bedtime storyteller",
    }


def test_build_image_plan_context_reduces_story_plan_and_story_json_to_visual_fields():
    story_plan = {
        "title": "Mira and the Moon Map",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
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
