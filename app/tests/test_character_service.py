import json

import pytest

from app.core.exceptions import AppException
from app.service.character_service import CharacterService


def _identity_profile() -> dict:
    return {
        "age_appearance": "early-reader child",
        "face_shape": "round",
        "cheek_shape": "soft round",
        "jawline_shape": "soft childlike",
        "chin_shape": "small rounded",
        "skin_tone": "warm medium",
        "hair_color": "dark brown",
        "hair_length": "short",
        "hair_texture": "smooth",
        "hair_style": "neatly combed",
        "hair_direction": "slightly side-swept",
        "eye_color": "brown",
        "eye_shape": "almond",
        "eye_size": "natural child-sized",
        "eyebrow_shape": "soft arched",
        "eyebrow_thickness": "medium",
        "nose_shape": "small rounded",
        "mouth_shape": "small rounded",
        "smile_characteristics": "gentle closed-mouth smile",
        "ear_visibility": "partly visible",
        "distinctive_features": ["round cheeks"],
        "identity_summary": (
            "A child with a round face, soft round cheeks, warm medium skin tone, brown almond eyes, "
            "a small rounded nose, and short dark brown smooth hair swept slightly to one side."
        ),
        "mouth_description": "small rounded mouth with gentle closed-mouth smile",
        "smile_type": "gentle closed-mouth smile",
    }


def test_parse_character_description_json_validates_structured_profile():
    raw = """
    {
      "age_appearance": "early-reader child",
      "face_shape": "round",
      "cheek_shape": "soft round",
      "jawline_shape": "soft childlike",
      "chin_shape": "small rounded",
      "skin_tone": "warm medium",
      "hair_color": "dark brown",
      "hair_length": "short",
      "hair_texture": "smooth",
      "hair_style": "neatly combed",
      "hair_direction": "slightly side-swept",
      "eye_color": "brown",
      "eye_shape": "almond",
      "eye_size": "natural child-sized",
      "eyebrow_shape": "soft arched",
      "eyebrow_thickness": "medium",
      "nose_shape": "small rounded",
      "mouth_shape": "small rounded",
      "smile_characteristics": "gentle closed-mouth smile",
      "ear_visibility": "partly visible",
      "distinctive_features": ["round cheeks", ""],
      "identity_summary": "A child with a round face, soft round cheeks, warm medium skin tone, brown almond eyes, a small rounded nose, and short dark brown smooth hair swept slightly to one side."
    }
    """

    parsed = CharacterService._parse_character_description_json(raw)

    assert parsed == _identity_profile()


def test_parse_character_description_json_rejects_missing_required_field():
    payload = _identity_profile()
    payload["eye_color"] = ""

    with pytest.raises(AppException, match="eye_color"):
        CharacterService._parse_character_description_json(json.dumps(payload))


def test_summarize_character_identity_profile_uses_vision_profile_fields():
    summary = CharacterService._summarize_character_identity_profile(_identity_profile())

    assert "round face" in summary
    assert "brown almond eyes" in summary
    assert "short dark brown smooth hair" in summary
