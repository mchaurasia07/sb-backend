from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    errors: list[str]


class PlanValidator:
    """Semantic validator for the Story Planner JSON schema."""

    _AGE_GROUP_PAGE_COUNTS: dict[str, int] = {
        "2-4": 6,
        "5-7": 10,
        "8-12": 12,
    }
    def validate(
        self,
        plan: dict[str, Any],
        *,
        age_group: str,
        source_inputs: dict[str, str] | None = None,
    ) -> PlanValidationResult:
        errors: list[str] = []

        if not isinstance(plan, dict):
            return PlanValidationResult(ok=False, errors=["Plan must be a JSON object."])

        for field in ("title", "summary", "theme", "learning_goal", "moral_theme", "setting", "tone"):
            self._validate_required_string(plan, field, "plan", errors)

        if source_inputs is not None:
            self._validate_source_inputs(plan, source_inputs, errors)

        self._validate_visual_bible(plan.get("visual_bible"), errors)

        pages = plan.get("pages")
        if not isinstance(pages, list) or not pages:
            errors.append("Missing or invalid `pages` (must be a non-empty array).")
            return PlanValidationResult(ok=False, errors=errors)

        expected_count = self._AGE_GROUP_PAGE_COUNTS.get(self._enum_value(age_group))
        if expected_count is not None and len(pages) != expected_count:
            errors.append(f"`pages.length` must be {expected_count} for age_group={age_group}.")

        page_numbers: list[int] = []
        required_page_keys = {
            "page_number",
            "story_role",
            "scene_description",
            "characters_present",
            "child_action",
            "emotional_beat",
            "learning_goal_integration",
            "continuity_requirements",
        }

        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"pages[{idx}] must be an object.")
                continue

            missing_keys = required_page_keys - set(page.keys())
            if missing_keys:
                errors.append(f"pages[{idx}] missing required fields: {', '.join(sorted(missing_keys))}.")

            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"pages[{idx}].page_number must be a positive integer.")
            else:
                page_numbers.append(page_number)

            story_role = page.get("story_role")
            if not isinstance(story_role, str) or not story_role.strip():
                errors.append(f"pages[{idx}].story_role must be a non-empty string.")
            else:
                page["story_role"] = self._normalize_story_role(story_role)

            for field in ("scene_description", "child_action", "emotional_beat", "learning_goal_integration"):
                self._validate_required_string(page, field, f"pages[{idx}]", errors)
            self._validate_string_array(page, "characters_present", f"pages[{idx}]", errors, allow_empty=True)
            self._validate_string_array(page, "continuity_requirements", f"pages[{idx}]", errors, allow_empty=True)

        if page_numbers:
            expected = list(range(1, len(pages) + 1))
            if page_numbers != expected:
                errors.append("Pages must be sequential and ordered with page_number 1..N with no gaps or duplicates.")

        return PlanValidationResult(ok=(len(errors) == 0), errors=errors)

    def _validate_source_inputs(
        self,
        plan: dict[str, Any],
        source_inputs: dict[str, str],
        errors: list[str],
    ) -> None:
        expected_theme = source_inputs.get("category", "")
        expected_learning_goal = source_inputs.get("learning_goal", "")
        if plan.get("theme") != expected_theme:
            errors.append("`theme` must match the request Theme exactly.")
        if plan.get("learning_goal") != expected_learning_goal:
            errors.append("`learning_goal` must match the request Learning Goal exactly.")

    def _validate_visual_bible(self, visual_bible: Any, errors: list[str]) -> None:
        if not isinstance(visual_bible, dict):
            errors.append("Missing or invalid `visual_bible` (must be an object).")
            return

        self._validate_required_string(visual_bible, "style", "visual_bible", errors)

        hero = visual_bible.get("hero")
        if not isinstance(hero, dict):
            errors.append("visual_bible.hero must be an object.")
        else:
            for field in ("name", "appearance", "outfit"):
                self._validate_required_string(hero, field, "visual_bible.hero", errors)
            signature_item = hero.get("signature_item")
            if signature_item is not None and not isinstance(signature_item, str):
                errors.append("visual_bible.hero.signature_item must be a string or null.")

        companion = visual_bible.get("companion")
        if companion is not None:
            self._validate_optional_character_object(companion, "visual_bible.companion", errors)

        for field in ("father", "mother"):
            value = visual_bible.get(field)
            if value is not None:
                self._validate_optional_appearance_object(value, f"visual_bible.{field}", errors)

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

    def _validate_optional_character_object(self, value: Any, label: str, errors: list[str]) -> None:
        if not isinstance(value, dict):
            errors.append(f"{label} must be an object.")
            return
        name = value.get("name")
        appearance = value.get("appearance")
        if isinstance(name, str) and name.strip():
            self._validate_required_string(value, "appearance", label, errors)
        elif isinstance(appearance, str) and appearance.strip():
            return

    def _validate_optional_appearance_object(self, value: Any, label: str, errors: list[str]) -> None:
        if not isinstance(value, dict):
            errors.append(f"{label} must be an object.")
            return
        appearance = value.get("appearance")
        if appearance is not None and not isinstance(appearance, str):
            errors.append(f"{label}.appearance must be a string when provided.")

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

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _normalize_story_role(value: str) -> str:
        return value.strip().lower().replace(" ", "_").replace("-", "_")

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


class PlanValidationError(RuntimeError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Plan validation failed.")
