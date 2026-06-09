from __future__ import annotations

from typing import Any

from app.core.age_groups import AGE_GROUP_0_3, AGE_GROUP_3_6, AGE_GROUP_6_9, DEFAULT_AGE_GROUP, normalize_age_group


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

EMOTION_ALIASES = {
    "amused": "playfulness",
    "appreciative": "kindness",
    "affection": "friendship",
    "affectionate": "friendship",
    "care": "kindness",
    "caring": "kindness",
    "comfort": "calm",
    "comforting": "calm",
    "concern": "kindness",
    "concerned": "kindness",
    "content": "calm",
    "curious": "curiosity",
    "delight": "joy",
    "delighted": "joy",
    "determined": "determination",
    "empathetic": "kindness",
    "excited": "excitement",
    "happy": "joy",
    "heartwarming": "kindness",
    "hope": "confidence",
    "hopeful": "confidence",
    "joyful": "joy",
    "laughing": "playfulness",
    "longing": "wonder",
    "love": "friendship",
    "loving": "friendship",
    "nurturing": "kindness",
    "overwhelmed_joy": "triumph",
    "peace": "calm",
    "peaceful": "calm",
    "proud": "confidence",
    "reflective": "calm",
    "relief": "calm",
    "relieved": "calm",
    "responsibility": "determination",
    "responsible": "determination",
    "tender": "kindness",
    "triumphant": "triumph",
    "understanding": "kindness",
    "worried": "kindness",
}

VOICE_STYLE_BY_AGE_GROUP = {
    AGE_GROUP_0_3: "gentle lullaby bedtime storyteller",
    AGE_GROUP_3_6: "warm animated storyteller",
    AGE_GROUP_6_9: "expressive adventure storyteller",
}


def normalize_page_emotion(emotion: Any) -> str:
    """Normalize story-writer page emotion to the supported narration set."""
    if not isinstance(emotion, str):
        return DEFAULT_PAGE_EMOTION
    value = emotion.strip().lower().replace("-", "_").replace(" ", "_")
    if value in TONE_BY_EMOTION:
        return value
    if value in EMOTION_ALIASES:
        return EMOTION_ALIASES[value]

    tokens = [
        token.strip("()[]{}.,;:!?\"'").replace("-", "_").replace(" ", "_")
        for token in emotion.lower().replace("/", ",").split(",")
    ]
    for token in tokens:
        if token in TONE_BY_EMOTION:
            return token
        if token in EMOTION_ALIASES:
            return EMOTION_ALIASES[token]

    for alias, normalized in EMOTION_ALIASES.items():
        if alias.replace("_", " ") in emotion.lower() or alias in value:
            return normalized
    return DEFAULT_PAGE_EMOTION


def voice_style_for_age_group(age_group: Any) -> str:
    """Return the fixed narration voice style for a story age group."""
    value = normalize_age_group(age_group)
    return VOICE_STYLE_BY_AGE_GROUP.get(str(value), VOICE_STYLE_BY_AGE_GROUP[DEFAULT_AGE_GROUP])


def build_page_narration(emotion: Any, age_group: Any = None) -> dict[str, str]:
    """Derive deterministic narration controls from page emotion and age group."""
    normalized_emotion = normalize_page_emotion(emotion)
    return {
        "tone": TONE_BY_EMOTION[normalized_emotion],
        "pace": PACE_BY_EMOTION[normalized_emotion],
        "voice_style": voice_style_for_age_group(age_group),
    }
