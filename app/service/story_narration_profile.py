from __future__ import annotations

from typing import Any


DEFAULT_PAGE_EMOTION = "wonder"

TONE_BY_EMOTION = {
    "wonder": "curious",
    "curiosity": "curious",
    "playfulness": "playful",
    "excitement": "enthusiastic",
    "determination": "encouraging",
    "surprise": "animated",
    "friendship": "warm",
    "kindness": "gentle",
    "confidence": "confident",
    "triumph": "celebratory",
    "joy": "happy",
    "calm": "soothing",
}

PACE_BY_EMOTION = {
    "wonder": "slow",
    "curiosity": "slow",
    "playfulness": "medium",
    "excitement": "medium",
    "determination": "medium",
    "surprise": "medium",
    "friendship": "medium-slow",
    "kindness": "medium-slow",
    "confidence": "medium",
    "triumph": "medium",
    "joy": "medium",
    "calm": "slow",
}

VOICE_STYLE_BY_AGE_GROUP = {
    "2-4": "gentle bedtime storyteller",
    "5-7": "warm animated storyteller",
    "8-12": "expressive cinematic storyteller",
}


def normalize_page_emotion(emotion: Any) -> str:
    """Normalize story-writer page emotion to the supported narration set."""
    if not isinstance(emotion, str):
        return DEFAULT_PAGE_EMOTION
    value = emotion.strip().lower().replace(" ", "_")
    return value if value in TONE_BY_EMOTION else DEFAULT_PAGE_EMOTION


def voice_style_for_age_group(age_group: Any) -> str:
    """Return the fixed narration voice style for a story age group."""
    value = getattr(age_group, "value", age_group)
    return VOICE_STYLE_BY_AGE_GROUP.get(str(value), VOICE_STYLE_BY_AGE_GROUP["5-7"])


def build_page_narration(emotion: Any, age_group: Any = None) -> dict[str, str]:
    """Derive deterministic narration controls from page emotion and age group."""
    normalized_emotion = normalize_page_emotion(emotion)
    return {
        "tone": TONE_BY_EMOTION[normalized_emotion],
        "pace": PACE_BY_EMOTION[normalized_emotion],
        "voice_style": voice_style_for_age_group(age_group),
    }
