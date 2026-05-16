from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]


def _resolve_prompt_path(path: str | Path) -> Path:
    if isinstance(path, Path):
        return path
    return (ROOT_DIR / path).resolve()


@lru_cache(maxsize=64)
def load_prompt(path: str | Path) -> str:
    """Load prompt template from file."""
    prompt_path = _resolve_prompt_path(path)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """Replace placeholders like {key} with values from variables.

    This intentionally does NOT use str.format() because prompt templates often
    contain literal {} braces (e.g., JSON examples) which would require escaping.
    """
    rendered = template
    for key, value in variables.items():
        if value is None:
            replacement = ""
        elif isinstance(value, bool):
            replacement = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            replacement = json.dumps(value, ensure_ascii=False)
        else:
            replacement = str(value)

        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def load_and_render_prompt(path: str | Path, variables: dict[str, Any]) -> str:
    """Load prompt template and render it with given variables."""
    return render_prompt(load_prompt(path), variables)
