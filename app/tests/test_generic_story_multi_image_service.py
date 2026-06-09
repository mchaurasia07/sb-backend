from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.service.generic_story_multi_image_service import GenericStoryMultiImageTestService


def _story_pages():
    return [
        {"page_number": 1, "text": "Meera sees the rakhi tray.", "image_url": "old-1"},
        {"page_number": 2, "text": "Grandma smiles warmly.", "image_url": "old-2"},
    ]


def _image_plan():
    return {
        "cover": {
            "image_prompt": "Meera and Grandma beside a colorful rakhi tray.",
            "composition_type": "cover_composition",
            "lighting_mood": "warm morning",
        },
        "pages": [
            {
                "page_number": 1,
                "characters": ["Meera", "Grandma"],
                "image_prompt": "Meera points at the rakhi tray.",
                "composition_type": "medium_scene",
                "lighting_mood": "bright morning",
            },
            {
                "page_number": 2,
                "characters": ["Meera", "Grandma"],
                "image_prompt": "Grandma explains the rakhi with a gentle smile.",
                "composition_type": "medium_scene",
                "lighting_mood": "soft warm light",
            },
        ],
    }


def _visual_bible():
    return {
        "style": {"rendering": "premium storybook 3D"},
        "style_consistency_rules": ["same character model across every image"],
        "characters": [
            {
                "name": "Meera",
                "character_image_token": "six-year-old Meera with black bob hair and yellow kurta",
                "appearance": {
                    "outfit": {"top": "yellow kurta", "bottom": "blue leggings"},
                    "hair": {"style": "black bob hair"},
                },
                "locks": {
                    "face_lock": "round warm face",
                    "hair_lock": "black bob hair",
                    "outfit_lock": "yellow kurta and blue leggings",
                },
            },
            {
                "name": "Grandma",
                "character_image_token": "kind Grandma with silver bun and green sari",
                "appearance": {"outfit": {"sari": "green sari"}},
                "locks": {"outfit_lock": "green sari"},
            },
        ],
    }


def test_builds_two_aspect_ratio_groups_for_all_book_images():
    story = SimpleNamespace(id=uuid4(), title="Rakhi Day", age_group="3-6")
    workflow = SimpleNamespace(id=uuid4(), title=None, age_group="3-6")
    story_json = {"title": "Rakhi Day", "pages": _story_pages()}

    items = GenericStoryMultiImageTestService._build_image_items(
        generic_story=story,
        workflow=workflow,
        story_json=story_json,
        image_plan=_image_plan(),
        story_pages=story_json["pages"],
    )
    groups = GenericStoryMultiImageTestService._group_items_by_aspect_ratio(items)

    assert [item.item_id for item in groups["pages"]] == ["page_1", "page_2"]
    assert [item.item_id for item in groups["cover_back_cover"]] == ["cover", "back_cover"]
    assert {item.aspect_ratio for item in groups["pages"]} == {"1:1"}
    assert {item.aspect_ratio for item in groups["cover_back_cover"]} == {"3:4"}


def test_group_prompt_includes_ordered_item_ids_and_page_numbers():
    story = SimpleNamespace(id=uuid4(), title="Rakhi Day", age_group="3-6")
    workflow = SimpleNamespace(id=uuid4(), title=None, age_group="3-6")
    story_json = {"title": "Rakhi Day", "pages": _story_pages()}
    items = GenericStoryMultiImageTestService._build_image_items(
        generic_story=story,
        workflow=workflow,
        story_json=story_json,
        image_plan=_image_plan(),
        story_pages=story_json["pages"],
    )
    groups = GenericStoryMultiImageTestService._group_items_by_aspect_ratio(items)
    service = GenericStoryMultiImageTestService.__new__(GenericStoryMultiImageTestService)

    prompt = service._render_group_prompt(
        group_name="pages",
        items=groups["pages"],
        story_title="Rakhi Day",
        age_group="Early Reader (3-6 years)",
        visual_bible=_visual_bible(),
    )

    assert "IMAGE_ITEM: <item_id>" in prompt
    assert "- page_1" in prompt
    assert "- page_2" in prompt
    assert prompt.index('"item_id":"page_1"') < prompt.index('"item_id":"page_2"')
    assert '"page_number":1' in prompt
    assert '"page_number":2' in prompt
    assert '"page_image_plan"' in prompt
    assert "Character consistency applies to every visible named character, not only the hero." in prompt
    assert "Do not replace a named character with a lookalike" in prompt
    assert "faceless named characters" in prompt
    assert "GLOBAL CHARACTER REFERENCE JSON" in prompt
    assert "Use GLOBAL CHARACTER REFERENCE JSON as the source of truth for character appearance" in prompt
    assert "Use visual_context as the source of truth for page-scoped style" in prompt
    assert "The required output is image parts" in prompt
    assert "Do not stop after text markers" in prompt
    assert "source_image_prompt" in prompt
    assert "scoped_visual_bible" not in prompt
    assert "image_plan_summary" not in prompt
    assert "Meera sees the rakhi tray." not in prompt
    assert "Use page_image_plan.characters as the exact visible-character allow-list" in prompt
    assert "Respect page_image_plan.camera_shot, composition, emotion, environment, and continuity_notes." in prompt


@pytest.mark.asyncio
async def test_apply_saved_urls_overwrites_existing_urls_in_every_language_content():
    class FakeGenericStoryRepository:
        def __init__(self):
            self.updated_languages = []
            self.flushed = False

        async def update_content(self, content):
            self.updated_languages.append(content.language)
            return content

        async def flush(self):
            self.flushed = True

    service = GenericStoryMultiImageTestService.__new__(GenericStoryMultiImageTestService)
    service.generic_stories = FakeGenericStoryRepository()
    story = SimpleNamespace(
        id=uuid4(),
        cover_image="old-cover",
        contents=[
            SimpleNamespace(
                language="en",
                story_json={
                    "cover_image_url": "old-cover",
                    "back_cover_image_url": "old-back",
                    "pages": _story_pages(),
                },
            ),
            SimpleNamespace(
                language="hi",
                story_json={
                    "cover_image_url": "old-cover-hi",
                    "back_cover_image_url": "old-back-hi",
                    "pages": [
                        {"page_number": 1, "text": "Hindi page 1", "image_url": "old-hi-1"},
                        {"page_number": 2, "text": "Hindi page 2", "image_url": "old-hi-2"},
                    ],
                },
            ),
        ],
    )

    await service._apply_saved_urls_to_all_contents(
        story,
        {
            "cover": "new-cover",
            "page_1": "new-page-1",
            "page_2": "new-page-2",
            "back_cover": "new-back",
        },
    )

    assert story.cover_image == "new-cover"
    assert service.generic_stories.updated_languages == ["en", "hi"]
    assert service.generic_stories.flushed is True
    for content in story.contents:
        assert content.story_json["cover_image_url"] == "new-cover"
        assert content.story_json["back_cover_image_url"] == "new-back"
        assert content.story_json["pages"][0]["image_url"] == "new-page-1"
        assert content.story_json["pages"][1]["image_url"] == "new-page-2"
    assert story.contents[1].story_json["pages"][0]["text"] == "Hindi page 1"


def test_missing_workflow_image_plan_fails_clearly():
    workflow = SimpleNamespace(id=uuid4(), image_plan_json=None)

    with pytest.raises(AppException) as exc_info:
        GenericStoryMultiImageTestService._require_image_plan(workflow)

    assert exc_info.value.code == "GENERIC_MULTI_IMAGE_IMAGE_PLAN_MISSING"
