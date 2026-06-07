from app.service.image_plan_validator import ImagePlanValidator
from app.service.plan_validator import PlanValidator


def _story_plan(page_count: int = 8) -> dict:
    return {
        "title": "Mira and the Moon Map",
        "summary": "Mira learns to solve a puzzle step by step.",
        "theme": "adventure",
        "learning_goal": "problem solving",
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
        "content_anchors": {
            "required_names": ["moon map", "silver path", "reading nook"],
            "required_facts": ["A map can guide one careful step at a time."],
            "age_safe_explanations": ["Following one clue at a time helps Mira stay calm."],
        },
        "visual_bible": {
            "style": "premium semi-realistic 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
            "companion": {"name": "Luma", "appearance": "A small glowing moth."},
            "father": {"appearance": ""},
            "mother": {"appearance": ""},
            "recurring_characters": [],
        },
        "pages": [
            {
                "page_number": index,
                "story_role": "introduction" if index == 1 else ("climax" if index == page_count - 1 else "build"),
                "scene_description": f"Mira follows clue {index}.",
                "characters_present": ["Mira"],
                "child_action": "Mira studies the map carefully.",
                "emotional_beat": "curious focus",
                "learning_goal_integration": "Mira checks one clue before moving to the next.",
                "growth_step": "Mira practices slowing down before choosing.",
                "page_turn_hook": "A new silver path begins to glow.",
                "domain_detail": f"Clue {index} glows on the moon map.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
            for index in range(1, page_count + 1)
        ],
    }


def _story_json(page_count: int = 2) -> dict:
    return {
        "pages": [
            {"page_number": index, "emotion": "wonder", "text": f"Page {index} text."}
            for index in range(1, page_count + 1)
        ]
    }


def _image_plan(page_count: int = 2) -> dict:
    return {
        "visual_bible": {
            "hero": {
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
            "companion": {"appearance": "A small glowing moth."},
            "recurring_characters": [],
        },
        "cover": {
            "visual_focus": "Mira holding the moon map.",
            "emotion": "wonder",
            "image_prompt": "Mira smiles while the glowing moon map opens in a moonlit library.",
        },
        "pages": [
            {
                "page_number": index,
                "story_role": "introduction" if index == 1 else "resolution",
                "visual_importance": "high" if index == page_count else "medium",
                "emotion": "wonder",
                "scene_action": "Mira points to a glowing path on the map.",
                "environment": "Moonlit library with warm shelves.",
                "characters_present": ["Mira"],
                "image_prompt": f"Page {index}: Mira follows a glowing map clue in the library.",
            }
            for index in range(1, page_count + 1)
        ],
        "back_cover": {
            "emotion": "warm joy",
            "image_prompt": "Mira closes the moon map with a peaceful smile.",
        },
    }


def test_plan_validator_accepts_new_story_planner_schema():
    result = PlanValidator().validate(
        _story_plan(),
        age_group="3-6",
        source_inputs={"category": "adventure", "learning_goal": "problem solving", "context": ""},
    )

    assert result.ok, result.errors


def test_plan_validator_accepts_descriptive_roles_and_null_signature_item():
    plan = _story_plan(page_count=8)
    plan["visual_bible"]["hero"]["signature_item"] = None
    for index, page in enumerate(plan["pages"], start=1):
        page["story_role"] = "First Day Moment" if index == 1 else "Kindness Build"

    result = PlanValidator().validate(
        plan,
        age_group="0-3",
        source_inputs={"category": "adventure", "learning_goal": "problem solving", "context": ""},
    )

    assert result.ok, result.errors
    assert plan["pages"][0]["story_role"] == "first_day_moment"


def test_plan_validator_rejects_old_page_metadata_schema():
    plan = _story_plan()
    plan["pages"][0].pop("continuity_requirements")
    plan["pages"][0]["environment"] = {"lighting": "old schema"}

    result = PlanValidator().validate(
        plan,
        age_group="3-6",
        source_inputs={"category": "adventure", "learning_goal": "problem solving", "context": ""},
    )

    assert not result.ok
    assert any("continuity_requirements" in error for error in result.errors)


def test_image_plan_validator_accepts_new_image_plan_schema():
    story_json = _story_json(page_count=2)
    result = ImagePlanValidator().validate(_image_plan(page_count=2), story_json=story_json)

    assert result.ok, result.errors


def test_image_plan_validator_rejects_old_character_consistency_schema():
    image_plan = _image_plan(page_count=2)
    image_plan.pop("visual_bible")
    image_plan["character_consistency"] = {"name": "Mira"}

    result = ImagePlanValidator().validate(image_plan, story_json=_story_json(page_count=2))

    assert not result.ok
    assert any("visual_bible" in error for error in result.errors)
