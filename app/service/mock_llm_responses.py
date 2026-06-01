"""Mock LLM responses for testing story generation flow without OpenAI calls."""

import json
from typing import Any


def get_mock_story_plan(child_name: str = "Emma", age_group: str = "5-7") -> dict[str, Any]:
    """Return valid mock story plan JSON that passes validation."""
    # Determine page count and age band based on age_group
    if age_group == "2-4":
        page_count = 6
        age_band = "Toddler"
    elif age_group == "8-12":
        page_count = 12
        age_band = "Advanced"
    else:  # Default to 5-7 (Early Reader)
        page_count = 10
        age_band = "Early Reader"

    plan = {
        "title": f"{child_name}'s Amazing Adventure",
        "final_page_count": page_count,
        "age_band": age_band,
        "global_visual_style": "soft pastel watercolor with hand-drawn charcoal outlines",
        "summary": f"Join {child_name} on an exciting adventure where they discover courage and kindness. With a helpful companion, {child_name} overcomes challenges and learns valuable lessons.",
        "theme": "adventure",
        "learning_goal": "personal growth",
        "moral_theme": "courage and kindness",
        "setting": "a magical garden kingdom with rolling hills and sparkling streams",
        "tone": "whimsical and adventurous",
        "visual_bible": {
            "style": "soft pastel watercolor with hand-drawn charcoal outlines",
            "hero": {
                "name": child_name,
                "appearance": f"A curious and brave {child_name} with bright eyes.",
                "outfit": "adventure-ready colorful outfit",
                "signature_item": "a magical compass",
            },
            "companion": {
                "name": "Luna",
                "appearance": "A wise and friendly owl with silver feathers and kind eyes.",
            },
            "father": {"appearance": ""},
            "mother": {"appearance": ""},
            "recurring_characters": [],
        },
        "characters": [
            {
                "name": child_name,
                "role": "hero",
                "anchor_description": f"A curious and brave {child_name} with bright eyes, wearing colorful clothing, ready for adventure",
                "visual_traits": {
                    "hair": "flowing and natural",
                    "clothing": "adventure-ready colorful outfit",
                    "signature_item": "a magical compass",
                },
            },
            {
                "name": "Luna",
                "role": "companion",
                "anchor_description": "A wise and friendly owl with silver feathers and kind eyes, always ready to help",
                "visual_traits": {
                    "hair": "feathered",
                    "clothing": "natural owl feathers",
                    "signature_item": "a small golden bell",
                },
            },
        ],
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Emma stands at the entrance of a magical garden, with Luna perched on a nearby branch",
                "narration_sample": "One sunny morning, Emma discovered a secret garden entrance. Luna the owl waited there, ready for an adventure!",
                "child_action": "Emma takes a deep breath and steps through the golden gate",
                "learning_goal_integration": "Shows bravery in facing the unknown",
                "environment": {
                    "lighting": "golden morning sunlight",
                    "time_of_day": "early morning",
                    "dominant_colors": ["gold", "green", "sky blue"],
                },
                "mood": "curious wonder",
                "hook_to_next": "What mysteries await Emma in the magical garden?",
                "visual_continuity_notes": "Emma wears colorful outfit, carries compass, Luna has silver feathers and golden bell",
                "image_gen_prompt": "A young girl named Emma standing at a magical garden gate entrance, with a wise silver owl named Luna nearby, morning sunlight, watercolor style",
            },
            {
                "page_number": 2,
                "story_role": "setup",
                "scene_description": "The garden path with colorful flowers and a mysterious fountain ahead",
                "narration_sample": "The path twisted through beautiful flowers of every color. Luna flew ahead, leading the way deeper into the garden.",
                "child_action": "Emma follows the path with courage, even though she feels a little nervous",
                "learning_goal_integration": "Demonstrates perseverance despite uncertainty",
                "environment": {
                    "lighting": "dappled sunlight through trees",
                    "time_of_day": "mid-morning",
                    "dominant_colors": ["pink", "purple", "green"],
                },
                "mood": "adventurous curiosity",
                "hook_to_next": "What will Emma find at the end of the path?",
                "visual_continuity_notes": "Emma in same colorful outfit with compass, Luna flying with golden bell",
                "image_gen_prompt": "A magical garden path with colorful flowers, Luna the owl flying ahead, Emma walking with compass, dappled sunlight, watercolor illustration",
            },
            {
                "page_number": 3,
                "story_role": "conflict",
                "scene_description": "A sparkling fountain with three possible paths, but one is blocked by wilted flowers",
                "narration_sample": "At the fountain, Emma saw three paths. But one path had sad, wilted flowers. Emma knew something was wrong.",
                "child_action": "Emma uses her compass to help decide which path to take",
                "learning_goal_integration": "Shows problem-solving with available tools",
                "environment": {
                    "lighting": "clear and bright",
                    "time_of_day": "noon",
                    "dominant_colors": ["turquoise", "white", "brown"],
                },
                "mood": "thoughtful determination",
                "hook_to_next": "Will Emma choose the right path to help the garden?",
                "visual_continuity_notes": "Emma with compass deciding, Luna perched nearby, both unchanged",
                "image_gen_prompt": "A magical fountain with three paths, Emma holding a glowing compass, Luna perched nearby, wilted flowers on one path, bright noon light, watercolor",
            },
            {
                "page_number": 4,
                "story_role": "escalation",
                "scene_description": "Emma discovers a hidden garden room where flowers are slowly fading",
                "narration_sample": "Behind the fountain, Emma found a hidden room. The flowers here were getting weaker. 'We can help,' Emma whispered to Luna.",
                "child_action": "Emma bravely enters the hidden room to investigate and help",
                "learning_goal_integration": "Demonstrates courage and compassion",
                "environment": {
                    "lighting": "soft mysterious glow",
                    "time_of_day": "afternoon",
                    "dominant_colors": ["lavender", "silver", "pale gold"],
                },
                "mood": "gentle determination",
                "hook_to_next": "What can Emma do to save the fading flowers?",
                "visual_continuity_notes": "Emma in colorful outfit with compass, Luna with golden bell, same loyal pair",
                "image_gen_prompt": "A hidden magical garden room with fading flowers, Emma and Luna discovering it, soft glowing light, watercolor illustration",
            },
            {
                "page_number": 5,
                "story_role": "escalation",
                "scene_description": "Emma finds a dried-up crystal spring that once watered the flowers",
                "narration_sample": "Emma spotted a crystal spring, but its water had dried up. 'If we can bring the water back, the flowers will be happy again!' she thought.",
                "child_action": "Emma uses her compass to find the source of the spring",
                "learning_goal_integration": "Shows resourcefulness and determination",
                "environment": {
                    "lighting": "afternoon light with shadows",
                    "time_of_day": "late afternoon",
                    "dominant_colors": ["crystal blue", "stone gray", "moss green"],
                },
                "mood": "focused determination",
                "hook_to_next": "Can Emma restore the magical spring?",
                "visual_continuity_notes": "Emma with compass, Luna helping guide, adventure outfit intact",
                "image_gen_prompt": "A dried crystal spring in a magical garden, Emma examining it with her compass, Luna nearby, restoration hint, watercolor",
            },
            {
                "page_number": 6,
                "story_role": "climax",
                "scene_description": "Emma discovers a blocked waterfall and uses her bravery and cleverness to help restore it",
                "narration_sample": "Emma found the waterfall! Stones were blocking it. With Luna's help and her own courage, Emma carefully moved the stones. Splash! The water flowed again!",
                "child_action": "Emma bravely works with Luna to unblock the waterfall and restore the spring",
                "learning_goal_integration": "Uses courage and problem-solving to solve the main challenge",
                "environment": {
                    "lighting": "dramatic golden light on water",
                    "time_of_day": "sunset",
                    "dominant_colors": ["gold", "turquoise", "rainbow"],
                },
                "mood": "triumph and joy",
                "hook_to_next": "What happens now that the water flows again?",
                "visual_continuity_notes": "Emma triumphant with compass, Luna celebrating, both heroes of the moment",
                "image_gen_prompt": "Emma and Luna unblocking a waterfall, water flowing, sunset golden light, rainbow mist, magical celebration, watercolor",
            },
            {
                "page_number": 7,
                "story_role": "resolution",
                "scene_description": "The hidden garden comes back to life with blooming flowers and returning creatures",
                "narration_sample": "As the water flowed, something magical happened! Flowers bloomed. Birds sang. Butterflies danced. The garden was alive again!",
                "child_action": "Emma watches the garden transform, proud of what she and Luna accomplished",
                "learning_goal_integration": "Celebrates the positive impact of her courage and kindness",
                "environment": {
                    "lighting": "warm sunset transforming to twilight",
                    "time_of_day": "sunset to dusk",
                    "dominant_colors": ["gold", "pink", "purple", "starlight"],
                },
                "mood": "pure joy and wonder",
                "hook_to_next": None,
                "visual_continuity_notes": "Emma and Luna in the transformed garden, their adventure complete",
                "image_gen_prompt": "The magical garden transformed with blooming flowers, birds, butterflies, Emma and Luna celebrating, sunset colors, twilight stars, watercolor",
            },
            {
                "page_number": 8,
                "story_role": "resolution",
                "scene_description": "Emma returns home with Luna, knowing she's a hero who helped the magical garden",
                "narration_sample": "As stars appeared, Emma and Luna headed home. Emma smiled, knowing she had the courage to help. The garden would never forget her kindness.",
                "child_action": "Emma leaves the garden with Luna, feeling proud and happy",
                "learning_goal_integration": "Reflects on personal growth and the value of courage",
                "environment": {
                    "lighting": "starlight and moonlight",
                    "time_of_day": "nighttime",
                    "dominant_colors": ["midnight blue", "silver", "starlight"],
                },
                "mood": "peaceful fulfillment",
                "hook_to_next": None,
                "visual_continuity_notes": "Emma and Luna heading home under stars, unchanged throughout adventure",
                "image_gen_prompt": "Emma and Luna walking under stars and moonlight, heading home from the magical garden, peaceful night scene, watercolor",
            },
        ],
    }

    while len(plan["pages"]) < page_count:
        next_page = len(plan["pages"]) + 1
        plan["pages"].append(
            {
                "page_number": next_page,
                "story_role": "resolution" if next_page == page_count else "build",
                "scene_description": f"Emma and Luna continue through the magical garden on step {next_page}",
                "child_action": "Emma keeps going with courage and kindness",
                "learning_goal_integration": "Shows steady personal growth through action",
                "mood": "hopeful determination",
                "visual_continuity_notes": "Emma keeps the compass and Luna stays nearby",
            }
        )

    for page in plan["pages"]:
        page["characters_present"] = page.get("characters_present") or [child_name, "Luna"]
        page["emotional_beat"] = page.get("emotional_beat") or page.get("mood") or "wonder"
        visual_note = page.get("visual_continuity_notes")
        page["continuity_requirements"] = page.get("continuity_requirements") or ([visual_note] if visual_note else [])

    # Trim pages to match page_count if needed
    if len(plan["pages"]) > page_count:
        plan["pages"] = plan["pages"][:page_count]
        # Re-number pages if trimmed
        for i, page in enumerate(plan["pages"]):
            page["page_number"] = i + 1

    return plan


def get_mock_story_json(child_name: str = "Emma", story_pages_count: int = 8) -> dict[str, Any]:
    """Return valid mock story JSON with page text (trimmed to story_pages_count)."""
    # Base page content for all story types (8 pages available)
    base_pages = [
        {
            "page_number": 1,
            "text": f"One sunny morning, {child_name} discovered a secret garden entrance. Luna the owl waited there, ready for an adventure! {child_name} took a deep breath and stepped through the golden gate.",
        },
        {
            "page_number": 2,
            "text": "The path twisted through beautiful flowers of every color. Luna flew ahead, leading the way deeper into the garden. Emma followed bravely, even though she felt a little nervous.",
        },
        {
            "page_number": 3,
            "text": "At the fountain, Emma saw three paths. But one path had sad, wilted flowers. Emma knew something was wrong. She pulled out her magic compass to help decide which path to take.",
        },
        {
            "page_number": 4,
            "text": "Behind the fountain, Emma found a hidden room. The flowers here were getting weaker. 'We can help,' Emma whispered to Luna. She bravely entered the mysterious space.",
        },
        {
            "page_number": 5,
            "text": "Emma spotted a crystal spring, but its water had dried up. 'If we can bring the water back, the flowers will be happy again!' she thought. Luna helped her search for the source.",
        },
        {
            "page_number": 6,
            "text": "Emma found the waterfall! Stones were blocking it. With Luna's help and her own courage, Emma carefully moved the stones. Splash! The water flowed again, rushing and dancing!",
        },
        {
            "page_number": 7,
            "text": "As the water flowed, something magical happened! Flowers bloomed everywhere. Birds sang. Butterflies danced. The garden was alive again! Emma felt so proud and happy.",
        },
        {
            "page_number": 8,
            "text": "As stars appeared, Emma and Luna headed home. Emma smiled, knowing she had the courage to help. The garden would never forget her kindness and bravery.",
        },
    ]

    # Trim pages to match story_pages_count
    pages = base_pages[:story_pages_count]
    while len(pages) < story_pages_count:
        page_number = len(pages) + 1
        pages.append(
            {
                "page_number": page_number,
                "emotion": "joy" if page_number == story_pages_count else "determination",
                "text": (
                    f"Emma and Luna took one more careful step through the garden. Emma used what she had learned, "
                    f"kept her courage close, and helped the magic grow brighter on page {page_number}."
                ),
            }
        )

    return {
        "title": f"{child_name}'s Amazing Adventure",
        "summary": f"Join {child_name} on an exciting adventure where they discover courage and kindness.",
        "moral_theme": "courage and kindness",
        "pages": pages,
    }


def get_mock_image_plan(story_pages_count: int = 8) -> dict[str, Any]:
    """Return valid mock image plan JSON."""
    pages = []
    for i in range(1, story_pages_count + 1):
        pages.append(
            {
                "page_number": i,
                "story_role": "introduction" if i == 1 else ("resolution" if i == story_pages_count else "escalation"),
                "visual_importance": "climax" if i == story_pages_count - 1 else "medium",
                "emotion": "wonder",
                "scene_action": f"Emma and Luna act out story moment {i}.",
                "environment": "magical garden",
                "characters_present": ["Emma", "Luna"],
                "image_prompt": (
                    f"Page {i}: High-quality 3D Pixar-style children's illustration, cinematic lighting, "
                    "Emma in colorful adventure outfit with compass, Luna the silver owl with golden bell, "
                    "page-specific scene, watercolor style, safe margin composition, no extra fingers, "
                    "consistent character design"
                ),
            }
        )

    return {
        "visual_bible": {
            "hero": {
                "appearance": "Curious young girl with bright eyes and a round friendly face",
                "outfit": "colorful adventure outfit",
                "signature_item": "magical compass",
            },
            "companion": {
                "appearance": "Luna the silver owl with a small golden bell",
            },
            "recurring_characters": [],
        },
        "cover": {
            "visual_focus": "Emma standing excited with compass",
            "emotion": "joyful anticipation",
            "image_prompt": "Storybook cover design: 'Emma's Amazing Adventure' - Large bold playful typography. Emma standing excited with compass, Luna perched nearby, magical garden backdrop, Pixar 3D style, bright colors, high contrast",
        },
        "pages": pages,
        "back_cover": {
            "emotion": "peaceful triumph",
            "image_prompt": "Back cover: Emma and Luna under stars, magical garden healed and glowing in background, peaceful satisfied mood, watercolor Pixar style, child-friendly, calm resolution scene",
        },
    }


class MockLLMTextGenerationResult:
    """Mock result object that mimics OpenAI response."""

    def __init__(self, text: str):
        self.text = text


def get_mock_story_plan_text(child_name: str = "Emma", age_group: str = "5-7") -> str:
    """Return mock story plan as JSON string."""
    plan = get_mock_story_plan(child_name, age_group)
    return json.dumps(plan)


def get_mock_story_text(child_name: str = "Emma", story_pages_count: int = 8) -> str:
    """Return mock story as JSON string."""
    story = get_mock_story_json(child_name, story_pages_count)
    return json.dumps(story)


def get_mock_image_plan_text(story_pages_count: int = 8) -> str:
    """Return mock image plan as JSON string."""
    plan = get_mock_image_plan(story_pages_count)
    return json.dumps(plan)
