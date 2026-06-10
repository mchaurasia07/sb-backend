from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ImagePlanValidationResult:
    ok: bool
    errors: list[str]


class ImagePlanValidator:
    """Semantic validator for the Image Planner JSON schema."""

    _VALID_VISUAL_IMPORTANCE = {"low", "medium", "high", "climax"}
    _REQUIRED_PAGE_KEYS = {
        "page_number",
        "story_role",
        "visual_importance",
        "emotion",
        "scene_action",
        "environment",
        "characters_present",
        "image_prompt",
    }

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

        expected_page_numbers = self._story_page_numbers(story_pages, errors)
        self._validate_visual_bible(image_plan.get("visual_bible"), errors)
        self._validate_cover(image_plan.get("cover"), errors)
        self._validate_back_cover(image_plan.get("back_cover"), errors)

        pages = image_plan.get("pages")
        if not isinstance(pages, list) or not pages:
            errors.append("Missing or invalid `pages` (must be a non-empty array).")
            return ImagePlanValidationResult(ok=(len(errors) == 0), errors=errors)

        actual_page_numbers: list[int] = []
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"pages[{idx}] must be an object.")
                continue

            missing_keys = self._REQUIRED_PAGE_KEYS - set(page.keys())
            if missing_keys:
                errors.append(f"pages[{idx}] missing required fields: {', '.join(sorted(missing_keys))}.")

            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"pages[{idx}].page_number must be a positive integer.")
            else:
                actual_page_numbers.append(page_number)

            for field in ("story_role", "emotion", "scene_action", "environment", "image_prompt"):
                self._validate_required_string(page, field, f"pages[{idx}]", errors)

            visual_importance = page.get("visual_importance")
            if not isinstance(visual_importance, str) or visual_importance.strip() not in self._VALID_VISUAL_IMPORTANCE:
                errors.append(f"pages[{idx}].visual_importance must be one of: {', '.join(sorted(self._VALID_VISUAL_IMPORTANCE))}.")

            self._validate_string_array(page, "characters_present", f"pages[{idx}]", errors, allow_empty=True)

        if actual_page_numbers and actual_page_numbers != expected_page_numbers:
            errors.append("Image plan pages must match story pages exactly (page_number 1..N).")

        return ImagePlanValidationResult(ok=(len(errors) == 0), errors=errors)

    def _story_page_numbers(self, story_pages: list[Any], errors: list[str]) -> list[int]:
        page_numbers: list[int] = []
        for idx, page in enumerate(story_pages):
            if not isinstance(page, dict):
                errors.append(f"story.pages[{idx}] must be an object.")
                continue
            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"story.pages[{idx}].page_number must be a positive integer.")
                continue
            page_numbers.append(page_number)

        if page_numbers:
            expected = list(range(1, len(story_pages) + 1))
            if page_numbers != expected:
                errors.append("Story pages must be sequential and ordered with page_number 1..N.")
                return expected
            return page_numbers
        return list(range(1, len(story_pages) + 1))

    def _validate_visual_bible(self, visual_bible: Any, errors: list[str]) -> None:
        if not isinstance(visual_bible, dict):
            errors.append("Missing or invalid `visual_bible` (must be an object).")
            return

        hero = visual_bible.get("hero")
        if not isinstance(hero, dict):
            errors.append("visual_bible.hero must be an object.")
        else:
            for field in ("name", "appearance", "outfit", "signature_item"):
                self._validate_required_string(hero, field, "visual_bible.hero", errors)
            self._validate_detailed_string(hero, "appearance", "visual_bible.hero", errors)
            self._validate_detailed_string(hero, "outfit", "visual_bible.hero", errors, min_words=4)

        companion = visual_bible.get("companion")
        if companion is not None:
            if not isinstance(companion, dict):
                errors.append("visual_bible.companion must be an object.")
            else:
                appearance = companion.get("appearance")
                if appearance is not None and (not isinstance(appearance, str) or not appearance.strip()):
                    errors.append("visual_bible.companion.appearance must be a non-empty string when provided.")

        recurring = visual_bible.get("recurring_characters")
        if recurring is None:
            return
        if not isinstance(recurring, list):
            errors.append("visual_bible.recurring_characters must be an array.")
            return
        for idx, character in enumerate(recurring):
            if not isinstance(character, dict):
                errors.append(f"visual_bible.recurring_characters[{idx}] must be an object.")
                continue
            for field in ("name", "role", "appearance"):
                self._validate_required_string(character, field, f"visual_bible.recurring_characters[{idx}]", errors)
            self._validate_detailed_string(
                character,
                "appearance",
                f"visual_bible.recurring_characters[{idx}]",
                errors,
            )

    def _validate_cover(self, cover: Any, errors: list[str]) -> None:
        if not isinstance(cover, dict):
            errors.append("Missing or invalid `cover` (must be an object).")
            return
        for field in ("visual_focus", "emotion", "image_prompt"):
            self._validate_required_string(cover, field, "cover", errors)

    def _validate_back_cover(self, back_cover: Any, errors: list[str]) -> None:
        if not isinstance(back_cover, dict):
            errors.append("Missing or invalid `back_cover` (must be an object).")
            return
        for field in ("emotion", "image_prompt"):
            self._validate_required_string(back_cover, field, "back_cover", errors)

    def _validate_required_string(
        self,
        obj: dict[str, Any],
        field: str,
        label: str,
        errors: list[str],
    ) -> None:
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}.{field} must be a non-empty string.")

    def _validate_detailed_string(
        self,
        obj: dict[str, Any],
        field: str,
        label: str,
        errors: list[str],
        *,
        min_words: int = 6,
    ) -> None:
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            return
        words = [word for word in value.replace(",", " ").split() if word.strip()]
        if len(words) < min_words:
            errors.append(f"{label}.{field} must include enough locked visual detail for image consistency.")

    def _validate_string_array(
        self,
        obj: dict[str, Any],
        field: str,
        label: str,
        errors: list[str],
        *,
        allow_empty: bool,
    ) -> None:
        value = obj.get(field)
        if not isinstance(value, list):
            errors.append(f"{label}.{field} must be an array.")
            return
        if not allow_empty and not value:
            errors.append(f"{label}.{field} must be a non-empty array.")
            return
        for item_idx, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{label}.{field}[{item_idx}] must be a non-empty string.")


class ImagePlanValidationError(RuntimeError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Image plan validation failed.")
