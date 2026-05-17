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

    _REQUIRED_CHARACTER_IDENTITY_KEYS = {
        "hair",
        "face_shape",
        "eye_color",
        "skin_tone",
        "outfit",
        "signature_item",
    }
    _REQUIRED_STRUCTURED_KEYS = {"character", "action", "environment", "mood"}
    _REQUIRED_PAGE_KEYS = {
        "page_number",
        "story_role",
        "scene_goal",
        "character_state",
        "companion_state",
        "environment",
        "camera",
        "lighting",
        "emotion_arc",
        "continuity_anchors",
        "generation_hints",
        "visual_continuity_check",
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
            locked_visual_identity = character_consistency.get("locked_visual_identity")
            if not isinstance(name, str) or not name.strip():
                errors.append("character_consistency.name must be a non-empty string.")
            if not isinstance(anchor_traits, str) or not anchor_traits.strip():
                errors.append("character_consistency.anchor_traits must be a non-empty string.")
            if not isinstance(locked_visual_identity, dict):
                errors.append("character_consistency.locked_visual_identity must be an object.")
            else:
                self._validate_required_strings(
                    locked_visual_identity,
                    self._REQUIRED_CHARACTER_IDENTITY_KEYS,
                    "character_consistency.locked_visual_identity",
                    errors,
                )

        self._validate_cover(image_plan.get("cover"), errors)
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

            missing_keys = self._REQUIRED_PAGE_KEYS - set(page.keys())
            if missing_keys:
                errors.append(f"pages[{idx}] missing required fields: {', '.join(sorted(missing_keys))}.")

            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"pages[{idx}].page_number must be a positive integer.")
            else:
                actual_page_numbers.append(page_number)

            self._validate_required_string(page, "story_role", f"pages[{idx}]", errors)
            self._validate_required_string(page, "scene_goal", f"pages[{idx}]", errors)
            self._validate_required_string(page, "visual_continuity_check", f"pages[{idx}]", errors)
            self._validate_required_string(page, "image_prompt", f"pages[{idx}]", errors)

            self._validate_object(page, "character_state", f"pages[{idx}]", errors)
            self._validate_object(page, "companion_state", f"pages[{idx}]", errors)
            self._validate_object(page, "environment", f"pages[{idx}]", errors)
            self._validate_object(page, "camera", f"pages[{idx}]", errors)
            self._validate_object(page, "lighting", f"pages[{idx}]", errors)
            self._validate_object(page, "emotion_arc", f"pages[{idx}]", errors)
            self._validate_object(page, "continuity_anchors", f"pages[{idx}]", errors)
            self._validate_object(page, "generation_hints", f"pages[{idx}]", errors)

        if actual_page_numbers:
            if actual_page_numbers != expected_page_numbers:
                errors.append("Image plan pages must match story pages exactly (page_number 1..N).")

        return ImagePlanValidationResult(ok=(len(errors) == 0), errors=errors)

    def _validate_cover(self, item: Any, errors: list[str]) -> None:
        self._validate_item(item, "cover", errors)
        if not isinstance(item, dict):
            return
        for field in ["title_text", "title_position", "hero_pose", "iconic_story_element"]:
            self._validate_required_string(item, field, "cover", errors)

        primary_color_palette = item.get("primary_color_palette")
        if not isinstance(primary_color_palette, list) or not primary_color_palette:
            errors.append("cover.primary_color_palette must be a non-empty array.")

    def _validate_item(self, item: Any, label: str, errors: list[str]) -> None:
        if not isinstance(item, dict):
            errors.append(f"Missing or invalid `{label}` (must be an object).")
            return

        self._validate_required_string(item, "image_prompt", label, errors)

        structured = item.get("structured")
        if not isinstance(structured, dict):
            errors.append(f"{label}.structured must be an object.")
        else:
            self._validate_required_strings(structured, self._REQUIRED_STRUCTURED_KEYS, f"{label}.structured", errors)

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

    def _validate_required_strings(
        self,
        obj: dict[str, Any],
        fields: set[str],
        label: str,
        errors: list[str],
    ) -> None:
        for field in sorted(fields):
            self._validate_required_string(obj, field, label, errors)

    def _validate_object(
        self,
        obj: dict[str, Any],
        field: str,
        label: str,
        errors: list[str],
    ) -> None:
        value = obj.get(field)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{label}.{field} must be an object.")


class ImagePlanValidationError(RuntimeError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Image plan validation failed.")
