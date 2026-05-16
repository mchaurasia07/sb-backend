from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class PlanValidationResult:
    ok: bool
    errors: list[str]


class PlanValidator:
    """Semantic validator for story plans returned by an LLM.

    This validator is intentionally tolerant of extra fields so the plan schema
    can evolve over time.
    """

    _AGE_BAND_PAGE_RANGES: dict[str, tuple[int, int]] = {
        "Toddler": (4, 6),
        "Early Reader": (8, 10),
        "Advanced": (10, 14),
    }

    _AGE_GROUP_ALLOWED_BANDS: dict[str, set[str]] = {
        "2-4": {"Toddler"},
        "5-7": {"Early Reader"},
        "8-12": {"Advanced"},
    }

    def validate(self, plan: dict[str, Any], *, age_group: str) -> PlanValidationResult:
        errors: list[str] = []

        if not isinstance(plan, dict):
            return PlanValidationResult(ok=False, errors=["Plan must be a JSON object."])

        # --- Required top-level fields (from story_plan_prompt.txt schema)
        title = plan.get("title")
        age_band = plan.get("age_band")
        final_page_count = plan.get("final_page_count")
        summary = plan.get("summary")
        moral_theme = plan.get("moral_theme")
        setting = plan.get("setting")
        tone = plan.get("tone")
        characters = plan.get("characters")
        pages = plan.get("pages")

        if not isinstance(title, str) or not title.strip():
            errors.append("Missing or invalid `title` (must be a non-empty string).")
        if not isinstance(age_band, str) or not age_band.strip():
            errors.append("Missing or invalid `age_band` (must be a non-empty string).")
        else:
            if age_band not in ("Toddler", "Early Reader", "Advanced"):
                errors.append("Invalid `age_band` (must be 'Toddler', 'Early Reader', or 'Advanced').")
        if not isinstance(final_page_count, int) or final_page_count <= 0:
            errors.append("Missing or invalid `final_page_count` (must be a positive integer).")
        if not isinstance(summary, str) or not summary.strip():
            errors.append("Missing or invalid `summary` (must be a non-empty string).")
        if not isinstance(moral_theme, str) or not moral_theme.strip():
            errors.append("Missing or invalid `moral_theme` (must be a non-empty string).")
        if not isinstance(setting, str) or not setting.strip():
            errors.append("Missing or invalid `setting` (must be a non-empty string).")
        if not isinstance(tone, str) or not tone.strip():
            errors.append("Missing or invalid `tone` (must be a non-empty string).")
        if not isinstance(characters, list) or not characters:
            errors.append("Missing or invalid `characters` (must be a non-empty array).")
        if not isinstance(pages, list) or not pages:
            errors.append("Missing or invalid `pages` (must be a non-empty array).")
            return PlanValidationResult(ok=False, errors=errors)

        # --- Characters required keys (from story_plan_prompt.txt schema)
        if isinstance(characters, list):
            for idx, character in enumerate(characters):
                if not isinstance(character, dict):
                    errors.append(f"characters[{idx}] must be an object.")
                    continue

                name = character.get("name")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"characters[{idx}].name must be a non-empty string.")

                role = character.get("role")
                if not isinstance(role, str) or not role.strip():
                    errors.append(f"characters[{idx}].role must be a non-empty string.")
                else:
                    valid_roles = {"hero", "companion", "supporter", "reframed_custom"}
                    if role.strip() not in valid_roles:
                        errors.append(f"characters[{idx}].role must be one of: {', '.join(valid_roles)}.")

                anchor_description = character.get("anchor_description")
                if not isinstance(anchor_description, str) or not anchor_description.strip():
                    errors.append(f"characters[{idx}].anchor_description must be a non-empty string.")

                visual_traits = character.get("visual_traits")
                if not isinstance(visual_traits, dict):
                    errors.append(f"characters[{idx}].visual_traits must be an object.")
                    continue

                hair = visual_traits.get("hair")
                clothing = visual_traits.get("clothing")
                signature_item = visual_traits.get("signature_item")
                if not isinstance(hair, str) or not hair.strip():
                    errors.append(f"characters[{idx}].visual_traits.hair must be a non-empty string.")
                if not isinstance(clothing, str) or not clothing.strip():
                    errors.append(f"characters[{idx}].visual_traits.clothing must be a non-empty string.")
                if not isinstance(signature_item, str) or not signature_item.strip():
                    errors.append(f"characters[{idx}].visual_traits.signature_item must be a non-empty string.")

        # --- Page sequence + required keys (from story_plan_prompt.txt schema)
        page_numbers_in_order: list[int] = []
        required_page_keys = {
            "page_number",
            "story_role",
            "scene_description",
            "narration_sample",
            "child_action",
            "learning_goal_integration",
            "environment",
            "mood",
            "visual_continuity_notes",
        }
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"pages[{idx}] must be an object.")
                continue

            # Check for missing required keys
            missing_keys = required_page_keys - set(page.keys())
            if missing_keys:
                errors.append(f"pages[{idx}] missing required fields: {', '.join(sorted(missing_keys))}.")

            page_number = page.get("page_number")
            if not isinstance(page_number, int) or page_number <= 0:
                errors.append(f"pages[{idx}].page_number must be a positive integer.")
            else:
                page_numbers_in_order.append(page_number)

            story_role = page.get("story_role")
            if not isinstance(story_role, str) or not story_role.strip():
                errors.append(f"pages[{idx}].story_role must be a non-empty string.")
            else:
                valid_roles = {"introduction", "setup", "conflict", "escalation", "climax", "resolution"}
                if story_role.strip() not in valid_roles:
                    errors.append(f"pages[{idx}].story_role must be one of: {', '.join(valid_roles)}.")

            # Validate string fields are non-empty
            for field in ["scene_description", "narration_sample", "child_action", "learning_goal_integration", "mood", "visual_continuity_notes"]:
                value = page.get(field)
                if value is not None:
                    if not isinstance(value, str) or not value.strip():
                        errors.append(f"pages[{idx}].{field} must be a non-empty string.")

            # Validate environment is a dict (content doesn't matter as long as it exists)
            environment = page.get("environment")
            if environment is not None and not isinstance(environment, dict):
                errors.append(f"pages[{idx}].environment must be an object.")

        if page_numbers_in_order:
            expected = list(range(1, len(pages) + 1))
            if page_numbers_in_order != expected:
                errors.append("Pages must be sequential and ordered with page_number 1..N with no gaps or duplicates.")

        # --- Page count vs age_band
        if isinstance(final_page_count, int) and final_page_count > 0 and final_page_count != len(pages):
            errors.append("`final_page_count` must match the number of entries in `pages`.")

        if isinstance(age_band, str) and age_band.strip():
            band = age_band.strip()
            allowed_bands = self._AGE_GROUP_ALLOWED_BANDS.get(age_group)
            if allowed_bands and band not in allowed_bands:
                errors.append(f"`age_band` must be one of {sorted(allowed_bands)} for age_group={age_group}.")

            page_range = self._AGE_BAND_PAGE_RANGES.get(band)
            if page_range and isinstance(final_page_count, int) and final_page_count > 0:
                min_pages, max_pages = page_range
                if not (min_pages <= final_page_count <= max_pages):
                    errors.append(
                        f"Page count ({final_page_count}) must be within {min_pages}-{max_pages} for age_band={band}."
                    )

        return PlanValidationResult(ok=(len(errors) == 0), errors=errors)


class PlanValidationError(RuntimeError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("Plan validation failed.")
