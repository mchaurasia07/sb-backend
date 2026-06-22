from types import SimpleNamespace
from uuid import uuid4

from app.service.generic_story_diagnostics import GenericStoryConsistencyDiagnostics


def test_generic_story_diagnostics_warns_about_missing_hero_reference_and_object_state():
    visual_bible = {
        "hero": {
            "character_id": "ria_the_pattern_maker",
            "name": "Ria",
            "body_scale_lock": "small child with consistent proportions",
        },
        "recurring_characters": [
            {
                "character_id": "leo_the_explorer",
                "name": "Leo",
                "body_scale_lock": "slightly taller child",
            }
        ],
    }
    image_plan = {
        "character_reference_manifest": [
            {
                "character_id": "leo_the_explorer",
                "name": "Leo",
                "reference_image_url": "https://cdn.test/leo.webp",
            }
        ],
        "pages": [
            {
                "page_number": 1,
                "characters_present": ["Ria"],
                "reference_character_ids": ["ria_the_pattern_maker"],
                "important_objects": ["red pencil"],
            }
        ],
    }
    image_job = SimpleNamespace(
        id=uuid4(),
        status="SUCCEEDED",
        provider_model="gemini-image",
        request_keys=["page_1"],
        request_payload={
            "reference_character_ids_by_item": {"page_1": ["ria_the_pattern_maker"]},
            "items": [
                {
                    "key": "page_1",
                    "reference_character_ids_used": [],
                    "reference_image_urls_used": [],
                    "source_image_prompt": "Ria reaches for a red pencil.",
                    "rendered_prompt": "Rendered prompt.",
                }
            ],
        },
    )

    warnings = GenericStoryConsistencyDiagnostics._warnings(
        visual_bible,
        image_plan,
        image_job,
        {"pages": [{"page_number": 1, "image_url": "https://cdn.test/page.webp"}]},
        {"pages": [{"page_number": 1, "image_url": "https://cdn.test/page.webp"}]},
    )

    assert any("Missing reference image" in warning and "Ria" in warning for warning in warnings)
    assert any("important_objects without object_states" in warning for warning in warnings)
    assert any("planned references not attached" in warning for warning in warnings)
