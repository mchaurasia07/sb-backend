from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ImagePlanValidationResult:
    ok: bool
    errors: list[str]


class ImagePlanValidator:
    """Semantic validator for an image plan generated from a story JSON.

    This validator is intentionally tolerant of extra fields so the image plan
    schema can evolve over time.
    """

    _REQUIRED_STRUCTURED_KEYS = {"character", "scene", "environment", "mood", "style"}

    def validate(self, image_plan: dict[str, Any], *, story_json: dict[str, Any]) -> ImagePlanValidationResult:
        errors: list[str] = []

        if not isinstance(image_plan, dict):
            return ImagePlanValidationResult(ok=False, errors=["Image plan must be a JSON object."])

        story_pages = story_json.get("pages")
        if not isinstance(story_pages, list) or not story_pages:
            return ImagePlanValidationResult(
                ok=False,
                errors=["Story JSON must include a non-empty `pages` array for image planning."],
            )

        expected_page_numbers: list[int] = []
        for idx, page in enumerate(story_pages):
            if not isinstance(page, dict):
                errors.append(f"story.pages[{idx}] must be an object.")
                continue
            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"story.pages[{idx}].page_number must be a positive integer.")
                continue
            expected_page_numbers.append(page_number)

        if expected_page_numbers:
            expected = list(range(1, len(story_pages) + 1))
            if expected_page_numbers != expected:
                errors.append("Story pages must be sequential and ordered with page_number 1..N.")
        else:
            expected_page_numbers = list(range(1, len(story_pages) + 1))

        character_consistency = image_plan.get("character_consistency")
        if not isinstance(character_consistency, dict):
            errors.append("Missing or invalid `character_consistency` (must be an object).")
        else:
            name = character_consistency.get("name")
            anchor_traits = character_consistency.get("anchor_traits")
            if not isinstance(name, str) or not name.strip():
                errors.append("character_consistency.name must be a non-empty string.")
            if not isinstance(anchor_traits, str) or not anchor_traits.strip():
                errors.append("character_consistency.anchor_traits must be a non-empty string.")

        self._validate_item(image_plan.get("cover"), "cover", errors)
        self._validate_item(image_plan.get("back_cover"), "back_cover", errors)

        pages = image_plan.get("pages")
        if not isinstance(pages, list) or not pages:
            errors.append("Missing or invalid `pages` (must be a non-empty array).")
            return ImagePlanValidationResult(ok=(len(errors) == 0), errors=errors)

        actual_page_numbers: list[int] = []
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"pages[{idx}] must be an object.")
                continue

            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"pages[{idx}].page_number must be a positive integer.")
            else:
                actual_page_numbers.append(page_number)

            image_prompt = page.get("image_prompt")
            if not isinstance(image_prompt, str) or not image_prompt.strip():
                errors.append(f"pages[{idx}].image_prompt must be a non-empty string.")

        if actual_page_numbers:
            if actual_page_numbers != expected_page_numbers:
                errors.append("Image plan pages must match story pages exactly (page_number 1..N).")

        return ImagePlanValidationResult(ok=(len(errors) == 0), errors=errors)

    def _validate_item(self, item: Any, label: str, errors: list[str]) -> None:
        if not isinstance(item, dict):
            errors.append(f"Missing or invalid `{label}` (must be an object).")
            return

        image_prompt = item.get("image_prompt")
        if not isinstance(image_prompt, str) or not image_prompt.strip():
            errors.append(f"{label}.image_prompt must be a non-empty string.")


class ImagePlanValidationError(RuntimeError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Image plan validation failed.")
