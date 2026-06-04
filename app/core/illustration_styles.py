from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.exceptions import AppException


ROOT_DIR = Path(__file__).resolve().parents[2]
STYLE_CONFIG_PATH = ROOT_DIR / "config" / "generic_story_illustration_styles.json"
DEFAULT_ILLUSTRATION_TYPE = "cartoonish"


@lru_cache(maxsize=1)
def load_illustration_styles() -> dict[str, str]:
    with STYLE_CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not data:
        raise AppException("Illustration style config is empty", code="ILLUSTRATION_STYLE_CONFIG_INVALID")
    styles: dict[str, str] = {}
    for key, value in data.items():
        normalized_key = normalize_illustration_type(key)
        if not isinstance(value, str) or not value.strip():
            raise AppException(
                f"Illustration style '{key}' must be a non-empty string",
                code="ILLUSTRATION_STYLE_CONFIG_INVALID",
            )
        styles[normalized_key] = value.strip()
    return styles


def normalize_illustration_type(value: str | None) -> str:
    return str(value or DEFAULT_ILLUSTRATION_TYPE).strip().lower().replace(" ", "_").replace("-", "_")


def illustration_style_block(illustration_type: str | None) -> str:
    normalized = normalize_illustration_type(illustration_type)
    styles = load_illustration_styles()
    style = styles.get(normalized)
    if style is None:
        supported = ", ".join(sorted(styles))
        raise AppException(
            "Unsupported illustration type",
            code="ILLUSTRATION_TYPE_UNSUPPORTED",
            details={"illustration_type": illustration_type, "supported_illustration_types": supported},
        )
    return style
