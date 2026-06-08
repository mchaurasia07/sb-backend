from __future__ import annotations

import json
import re
from typing import Any


GROUP_CHARACTER_WORDS = {
    "children",
    "class",
    "classmates",
    "friends",
    "group",
    "kids",
    "students",
}
ADULT_CHARACTER_HINTS = {
    "adult",
    "aunt",
    "aunty",
    "father",
    "grandfather",
    "grandma",
    "grandmother",
    "ma'am",
    "maam",
    "man",
    "mother",
    "parent",
    "teacher",
    "uncle",
    "woman",
}
CHILD_CHARACTER_HINTS = {
    "boy",
    "child",
    "children",
    "classmate",
    "friend",
    "girl",
    "kid",
    "student",
    "young",
}


def compact_visual_bible_json_for_image_prompt(
    visual_bible: dict[str, Any] | None,
    *,
    page_type: str,
    image_brief: dict[str, Any] | None = None,
    scene_plan_page: dict[str, Any] | None = None,
    story_page: dict[str, Any] | None = None,
    max_characters: int = 6,
    max_locations: int = 3,
    max_objects: int = 8,
) -> str:
    """Return compact JSON for the visual context needed by one image prompt."""
    return json.dumps(
        compact_visual_bible_for_image_prompt(
            visual_bible,
            page_type=page_type,
            image_brief=image_brief,
            scene_plan_page=scene_plan_page,
            story_page=story_page,
            max_characters=max_characters,
            max_locations=max_locations,
            max_objects=max_objects,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def compact_visual_bible_for_image_prompt(
    visual_bible: dict[str, Any] | None,
    *,
    page_type: str,
    image_brief: dict[str, Any] | None = None,
    scene_plan_page: dict[str, Any] | None = None,
    story_page: dict[str, Any] | None = None,
    max_characters: int = 6,
    max_locations: int = 3,
    max_objects: int = 8,
) -> dict[str, Any]:
    bible = visual_bible if isinstance(visual_bible, dict) else {}
    scene_page = scene_plan_page if isinstance(scene_plan_page, dict) else {}
    story_page_context = story_page if isinstance(story_page, dict) else {}
    image_brief_context = image_brief if isinstance(image_brief, dict) else {}

    context_text = _context_text(scene_page, story_page_context, image_brief_context)
    page_number = _page_number(scene_page, story_page_context)
    requested_characters = _requested_character_refs(scene_page, image_brief_context)
    characters = _select_characters(
        bible,
        context_text=context_text,
        requested_refs=requested_characters,
        page_type=page_type,
        max_characters=max_characters,
    )
    locations = _select_locations(
        bible,
        context_text=context_text,
        page_number=page_number,
        page_type=page_type,
        max_locations=max_locations,
    )
    objects = _select_objects(
        bible,
        context_text=context_text,
        page_number=page_number,
        page_type=page_type,
        max_objects=max_objects,
    )

    compact = _clean_dict(
        {
            "style": bible.get("style"),
            "age_group": bible.get("age_group"),
            "illustration_notes": _truncate_text(bible.get("illustration_notes"), 800),
            "characters": [_compact_character(character) for character in characters],
            "locations": [_compact_location(location) for location in locations],
            "important_objects": [_compact_object(obj) for obj in objects],
            "color_palette_global": bible.get("color_palette_global"),
            "style_consistency_rules": _compact_string_list(bible.get("style_consistency_rules"), limit=6),
            "_prompt_scope": _clean_dict(
                {
                    "page_type": page_type,
                    "page_number": page_number,
                    "requested_characters": requested_characters,
                    "selected_characters": [_text(character.get("name")) for character in characters],
                    "selected_locations": [_text(location.get("name")) for location in locations],
                    "selected_objects": [_text(obj.get("name")) for obj in objects],
                    "note": "Scoped visual bible for this one image prompt; full workflow visual bible remains stored separately.",
                }
            ),
        }
    )
    return compact


def _select_characters(
    bible: dict[str, Any],
    *,
    context_text: str,
    requested_refs: list[str],
    page_type: str,
    max_characters: int,
) -> list[dict[str, Any]]:
    characters = _visual_bible_characters(bible)
    if not characters:
        return []

    selected_indexes: list[int] = []
    request_text = " ".join(requested_refs)
    search_text = f"{request_text} {context_text}"
    has_group_ref = _has_group_character_ref(search_text)

    for index, character in enumerate(characters):
        if _is_hero_character(character):
            selected_indexes.append(index)
        elif _matches_name(character.get("name"), search_text):
            selected_indexes.append(index)

    if has_group_ref:
        for index, character in enumerate(characters):
            if index in selected_indexes:
                continue
            if _looks_like_child_or_friend_character(character):
                selected_indexes.append(index)
            if len(selected_indexes) >= max_characters:
                break

    if page_type in {"cover", "back_cover"} and len(selected_indexes) < min(3, len(characters)):
        for index, _character in enumerate(characters):
            if index not in selected_indexes:
                selected_indexes.append(index)
            if len(selected_indexes) >= min(max_characters, 4, len(characters)):
                break

    if not selected_indexes:
        selected_indexes.append(0)

    unique_indexes = []
    for index in selected_indexes:
        if index not in unique_indexes:
            unique_indexes.append(index)
        if len(unique_indexes) >= max_characters:
            break
    return [characters[index] for index in unique_indexes]


def _select_locations(
    bible: dict[str, Any],
    *,
    context_text: str,
    page_number: int | None,
    page_type: str,
    max_locations: int,
) -> list[dict[str, Any]]:
    locations = [item for item in bible.get("locations") or [] if isinstance(item, dict)]
    if not locations:
        return []

    selected: list[dict[str, Any]] = []
    for location in locations:
        name = location.get("name")
        if _matches_name(name, context_text) or _page_in_location(location, page_number):
            selected.append(location)
        if len(selected) >= max_locations:
            break

    if not selected and page_type in {"cover", "back_cover"}:
        selected = locations[:max_locations]
    elif not selected and locations:
        selected = locations[:1]
    return selected[:max_locations]


def _select_objects(
    bible: dict[str, Any],
    *,
    context_text: str,
    page_number: int | None,
    page_type: str,
    max_objects: int,
) -> list[dict[str, Any]]:
    objects = [item for item in bible.get("important_objects") or [] if isinstance(item, dict)]
    if not objects:
        return []

    selected: list[dict[str, Any]] = []
    for obj in objects:
        name = obj.get("name")
        if _matches_name(name, context_text):
            selected.append(obj)
        if len(selected) >= max_objects:
            break

    if page_type in {"cover", "back_cover"} and len(selected) < min(4, len(objects)):
        for obj in objects:
            if obj not in selected:
                selected.append(obj)
            if len(selected) >= min(max_objects, 4):
                break
    elif not selected and page_number is not None:
        for obj in objects:
            try:
                first_page = int(obj.get("first_appears_on_page"))
            except (TypeError, ValueError):
                continue
            if first_page == page_number:
                selected.append(obj)
            if len(selected) >= max_objects:
                break

    return selected[:max_objects]


def _compact_character(character: dict[str, Any]) -> dict[str, Any]:
    return _clean_dict(
        {
            "name": character.get("name"),
            "role": character.get("role"),
            "anchor": _truncate_text(character.get("anchor"), 400),
            "character_image_token": _truncate_text(character.get("character_image_token"), 700),
            "appearance": character.get("appearance"),
            "outfit": character.get("outfit"),
            "signature_item": character.get("signature_item"),
            "expression_range": character.get("expression_range"),
            "locks": character.get("locks"),
            "forbidden_variations": _compact_string_list(character.get("forbidden_variations"), limit=8),
            "size_relative_to_hero": character.get("size_relative_to_hero"),
        }
    )


def _compact_location(location: dict[str, Any]) -> dict[str, Any]:
    variants = location.get("variants")
    if isinstance(variants, list):
        variants = [
            _clean_dict(
                {
                    "name": variant.get("name"),
                    "condition": variant.get("condition"),
                    "palette_shift": variant.get("palette_shift"),
                    "lighting_shift": variant.get("lighting_shift"),
                    "required_elements": _compact_string_list(variant.get("required_elements"), limit=5),
                    "forbidden_elements": _compact_string_list(variant.get("forbidden_elements"), limit=5),
                }
            )
            for variant in variants[:2]
            if isinstance(variant, dict)
        ]
    return _clean_dict(
        {
            "name": location.get("name"),
            "story_pages": location.get("story_pages"),
            "description": _truncate_text(location.get("description"), 450),
            "visual_identity": _truncate_text(location.get("visual_identity"), 450),
            "palette": location.get("palette"),
            "lighting_default": location.get("lighting_default"),
            "always_present_elements": _compact_string_list(location.get("always_present_elements"), limit=8),
            "forbidden_elements": _compact_string_list(location.get("forbidden_elements"), limit=8),
            "variants": variants,
        }
    )


def _compact_object(obj: dict[str, Any]) -> dict[str, Any]:
    return _clean_dict(
        {
            "name": obj.get("name"),
            "description": _truncate_text(obj.get("description"), 450),
            "first_appears_on_page": obj.get("first_appears_on_page"),
            "object_image_token": _truncate_text(obj.get("object_image_token"), 550),
            "continuity_requirements": _compact_string_list(obj.get("continuity_requirements"), limit=5),
            "forbidden_variations": _compact_string_list(obj.get("forbidden_variations"), limit=5),
        }
    )


def _visual_bible_characters(bible: dict[str, Any]) -> list[dict[str, Any]]:
    characters = [item for item in bible.get("characters") or [] if isinstance(item, dict)]
    hero = bible.get("hero")
    if isinstance(hero, dict):
        hero_character = dict(hero)
        hero_character.setdefault("role", "hero")
        if hero_character not in characters:
            characters.insert(0, hero_character)
    return characters


def _context_text(*values: Any) -> str:
    return " ".join(_text(value) for value in _flatten_text(values))


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten_text(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            parts.extend(_flatten_text(item))
        return parts
    return []


def _requested_character_refs(scene_plan_page: dict[str, Any], image_brief: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (scene_plan_page, image_brief):
        for key in ("characters", "characters_present", "allowed_characters"):
            value = source.get(key)
            if isinstance(value, list):
                refs.extend(_text(item) for item in value if _text(item))
            elif _text(value):
                refs.append(_text(value))
    return refs


def _page_number(scene_plan_page: dict[str, Any], story_page: dict[str, Any]) -> int | None:
    for source in (scene_plan_page, story_page):
        for key in ("page", "page_number"):
            try:
                value = int(source.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _is_hero_character(character: dict[str, Any]) -> bool:
    role = _normalize_text(character.get("role"))
    return role in {"hero", "main character", "protagonist"}


def _looks_like_child_or_friend_character(character: dict[str, Any]) -> bool:
    text = _normalize_text(
        " ".join(
            _flatten_text(
                {
                    "name": character.get("name"),
                    "role": character.get("role"),
                    "story_function": character.get("story_function"),
                    "character_image_token": character.get("character_image_token"),
                    "appearance": character.get("appearance"),
                }
            )
        )
    )
    if any(hint in text for hint in ADULT_CHARACTER_HINTS):
        return False
    return any(hint in text for hint in CHILD_CHARACTER_HINTS)


def _has_group_character_ref(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(_phrase_in_text(word, normalized) for word in GROUP_CHARACTER_WORDS)


def _matches_name(name: Any, text: str) -> bool:
    name_text = _normalize_text(name)
    if not name_text:
        return False
    normalized_text = _normalize_text(text)
    if _phrase_in_text(name_text, normalized_text):
        return True
    name_parts = [part for part in name_text.split() if len(part) > 2]
    if len(name_parts) > 1 and any(_phrase_in_text(part, normalized_text) for part in name_parts):
        return True
    return False


def _page_in_location(location: dict[str, Any], page_number: int | None) -> bool:
    if page_number is None:
        return False
    for key in ("story_pages", "pages", "appears_on_pages"):
        value = location.get(key)
        if _page_in_spec(value, page_number):
            return True
    return False


def _page_in_spec(value: Any, page_number: int) -> bool:
    if value is None:
        return False
    if isinstance(value, int):
        return value == page_number
    if isinstance(value, list):
        return any(_page_in_spec(item, page_number) for item in value)
    if isinstance(value, dict):
        return any(_page_in_spec(item, page_number) for item in value.values())
    text = str(value)
    for start, end in re.findall(r"(\d+)\s*[-\u2013\u2014]\s*(\d+)", text):
        if int(start) <= page_number <= int(end):
            return True
    return any(int(number) == page_number for number in re.findall(r"\d+", text))


def _compact_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_truncate_text(_text(item), 260) for item in value[:limit] if _text(item)]


def _truncate_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


def _normalize_text(value: Any) -> str:
    text = _text(value).lower()
    text = text.replace("'", " ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _phrase_in_text(phrase: str, text: str) -> bool:
    phrase_text = _normalize_text(phrase)
    text_value = _normalize_text(text)
    return bool(phrase_text) and f" {phrase_text} " in f" {text_value} "


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
