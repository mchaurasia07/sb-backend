from types import SimpleNamespace
import json

import pytest

from app.core.exceptions import AppException
from app.entity.story import AgeGroup
from app.model.response.story_content import StoryJsonContentResponse
from app.service.ai.base import TextGenerationResult
from app.service.story_narration_profile import build_page_narration
from app.service.story_service import StoryService, _compact_json, _normalize_story_output
from app.utils.prompt_loader import load_prompt


def test_build_story_generation_context_reduces_story_plan_to_narrative_fields():
    story_plan = {
        "title": "Mira and the Moon Map",
        "summary": "Mira learns to solve a problem step by step.",
        "theme": "patience and planning",
        "learning_goal": "solve problems step by step",
        "moral_theme": "patience and planning",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "central_problem": "The moon map opens only when Mira follows each clue carefully.",
        "hero_want": "Mira wants to find the moonlit reading nook.",
        "emotional_need": "Mira needs to trust slow, careful steps.",
        "stakes": "If Mira rushes, the glowing path fades before she can follow it.",
        "climax_choice": "Mira pauses, checks the final clue, and chooses the careful path.",
        "resolution_payoff": "The reading nook opens and Mira feels proud of her patience.",
        "moral_explanation": "Careful steps can solve a big puzzle.",
        "story_spine": {
            "hero_want": "Mira wants to find the moonlit reading nook.",
            "blocking_problem": "The map opens only when Mira follows each clue carefully.",
            "failed_attempt": "Mira rushes and the silver path fades.",
            "lesson_learned": "Mira learns to solve one clue at a time.",
            "climax_choice": "Mira pauses and chooses the careful path.",
            "resolution": "The reading nook opens and Mira feels proud.",
        },
        "language_profile": {
            "reading_stage": "Early Reader",
            "sentence_length": "5-12 words per sentence",
            "vocabulary_level": "simple everyday vocabulary",
            "repetition_level": "light repetition",
            "dialogue_complexity": "short dialogue",
        },
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira finds a folded moon map.",
                "characters_present": ["Mira"],
                "child_action": "Mira opens the moon map and traces the first silver path.",
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
                "growth_step": "Mira practices slowing down before choosing.",
                "domain_detail": "The moon map glows with one silver path.",
                "page_turn_hook": "A silver path begins to glow.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
        ],
    }

    reduced = StoryService._build_story_generation_context(story_plan)

    assert reduced == {
        "title": "Mira and the Moon Map",
        "summary": "Mira learns to solve a problem step by step.",
        "theme": "patience and planning",
        "learning_goal": "solve problems step by step",
        "moral_theme": "patience and planning",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "central_problem": "The moon map opens only when Mira follows each clue carefully.",
        "hero_want": "Mira wants to find the moonlit reading nook.",
        "emotional_need": "Mira needs to trust slow, careful steps.",
        "stakes": "If Mira rushes, the glowing path fades before she can follow it.",
        "climax_choice": "Mira pauses, checks the final clue, and chooses the careful path.",
        "resolution_payoff": "The reading nook opens and Mira feels proud of her patience.",
        "moral_explanation": "Careful steps can solve a big puzzle.",
        "story_spine": {
            "hero_want": "Mira wants to find the moonlit reading nook.",
            "blocking_problem": "The map opens only when Mira follows each clue carefully.",
            "failed_attempt": "Mira rushes and the silver path fades.",
            "lesson_learned": "Mira learns to solve one clue at a time.",
            "climax_choice": "Mira pauses and chooses the careful path.",
            "resolution": "The reading nook opens and Mira feels proud.",
        },
        "language_profile": {
            "reading_stage": "Early Reader",
            "sentence_length": "5-12 words per sentence",
            "vocabulary_level": "simple everyday vocabulary",
            "repetition_level": "light repetition",
            "dialogue_complexity": "short dialogue",
        },
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira finds a folded moon map.",
                "characters_present": ["Mira"],
                "child_action": "Mira opens the moon map and traces the first silver path.",
                "emotional_beat": "quiet wonder",
                "learning_goal_integration": "She pauses before acting.",
                "growth_step": "Mira practices slowing down before choosing.",
                "domain_detail": "The moon map glows with one silver path.",
                "page_turn_hook": "A silver path begins to glow.",
                "continuity_requirements": ["Mira keeps the moon map."],
            }
        ],
        "expected_page_count": 1,
        "expected_page_numbers": [1],
    }


def test_story_generation_prompt_aligns_with_story_plan_contract():
    prompt = load_prompt("prompts/story/story_generation_prompt.txt")

    assert "## STORY SPINE AUTHORITY" in prompt
    assert "Use story_spine as the narrative backbone" in prompt
    assert "Want -> Attempt -> Result -> New Challenge -> Better Attempt -> Resolution" in prompt
    assert "Write each page as a lived story moment, not a summary" in prompt
    assert "Avoid page-summary writing" in prompt
    assert "Keep the selected child's want, feelings, choices, and growth central" in prompt
    assert "The child remains the actor who changes the outcome" in prompt
    assert "Follow language_profile as the primary guide for vocabulary, sentence length" in prompt
    assert "Age band fallback targets" in prompt
    assert "read-aloud and audiobook narration" in prompt
    assert "The final story page must show the resolution_payoff" in prompt
    assert "before the moral field explains it" in prompt
    assert "- child_action" in prompt
    assert '"pages": [' in prompt
    assert '"page_number": 1' in prompt
    assert '"emotion": ""' in prompt
    assert '"text": ""' in prompt
    assert '"moral": ""' in prompt


def test_custom_story_generation_prompts_enforce_exact_page_count():
    for prompt_path in (
        "prompts/story/story_generation_child_hero_prompt.txt",
        "prompts/story/story_generation_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## PAGE COUNT REQUIREMENT" in prompt
        assert "story_plan_json.expected_page_count is the required page count" in prompt
        assert "pages.length must equal story_plan_json.expected_page_count" in prompt
        assert "Output page_number values must match story_plan_json.expected_page_numbers exactly" in prompt
        assert "Do not merge, collapse, summarize, skip, add, or reorder pages" in prompt
        assert "Replace the shown language keys with" in prompt
        assert "selected_languages is [\"hi\"]" in prompt
        assert "output only \"hi\"" in prompt
        assert "output exactly those three keys" in prompt


def test_custom_story_generation_prompts_require_native_hindi_marathi_localization():
    for prompt_path in (
        "prompts/story/story_generation_child_hero_prompt.txt",
        "prompts/story/story_generation_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## LANGUAGE LOCALIZATION" in prompt
        assert "Generate each language directly from the STORY PLAN" in prompt
        assert "think first as a native children's author" in prompt
        assert "Do not translate English phrases, idioms, metaphors, sentence structure" in prompt
        assert "Avoid overly formal, Sanskritized, literary, or textbook words" in prompt
        assert "Avoid highly literary, overly Sanskritized, or textbook vocabulary" in prompt
        assert "सच्चा प्यार" in prompt
        assert "शुद्ध प्यार" in prompt
        assert "धप्प!" in prompt
        assert "बॉन्क!" in prompt
        assert "अरे बाप रे!" in prompt
        assert "अरे देवा!" in prompt
        assert "Borrow English words in Hindi or Marathi only if they are genuinely part of" in prompt
        assert "Never transliterate an English word, phrase, idiom, metaphor, or sound effect" in prompt
        assert "Hindi and Marathi sound originally authored in those languages" in prompt


def test_tts_prompt_has_multilingual_voice_rules():
    prompt = load_prompt("prompts/tts_narration_template.txt")

    assert "For Hindi and Marathi, speak naturally in that language" in prompt
    assert "Do not translate or normalize the text into another language" in prompt
    assert "language must match Language above" in prompt


def test_input_safety_prompt_uses_product_age_range():
    prompt = load_prompt("prompts/story/input_safety_validation_prompt.txt")

    assert "children ages **0-9**" in prompt
    assert "unsuitable for children ages 0-9" in prompt


def test_story_plan_prompts_require_complete_final_page():
    for prompt_path in (
        "prompts/story/story_plan_child_hero_prompt.txt",
        "prompts/story/story_plan_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## ENDING COMPLETENESS" in prompt
        assert "final planned page must feel like a satisfying storybook ending" in prompt
        assert "central_problem is resolved or peacefully accepted" in prompt
        assert "page_turn_hook should be a closing image or emotional" in prompt
        assert "The final page creates completion and does not set up another event" in prompt


def test_story_plan_prompts_require_silent_editorial_evaluation_and_locked_consistency():
    for prompt_path in (
        "prompts/story/story_plan_child_hero_prompt.txt",
        "prompts/story/story_plan_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## STORY DEVELOPMENT & CREATIVE EVALUATION" in prompt
        assert "Act as an experienced children's book editor before acting as a story planner" in prompt
        assert "Select the strongest story before writing the blueprint" in prompt
        assert "Do not expose this analysis" in prompt
        assert "strict valid JSON only" in prompt
        assert "### FIRST IDEA BIAS" in prompt
        assert "Do not use the first reasonable idea" in prompt
        assert "### QUALITY STANDARDS" in prompt
        assert "originality over familiarity" in prompt
        assert "emotional storytelling over event sequencing" in prompt
        assert "curiosity-driven titles over descriptive titles" in prompt
        assert "natural morals over explicit teaching" in prompt
        assert "visual storytelling over exposition" in prompt
        assert "Optimize for reread value" in prompt
        assert "multiple high-quality alternatives" in prompt
        assert "Generate only as many alternatives" in prompt
        assert "confidently choose the best story" in prompt
        assert "story concept quality" in prompt
        assert "supporting cast design" in prompt
        assert "character economy" in prompt
        assert "Remove any character whose absence would not significantly weaken the story" in prompt
        assert "title strength" in prompt
        assert "moral integration" in prompt
        assert "emotional arc" in prompt
        assert "page-turn strength" in prompt
        assert "visual richness" in prompt
        assert "originality" in prompt
        assert "fresh and memorable to a parent who has read hundreds of children's books" in prompt
        assert "character consistency" in prompt
        assert "scene planning review" in prompt
        assert "conflict quality" in prompt
        assert "central problem should arise naturally from the setting" in prompt
        assert "not from characters behaving unreasonably or making avoidable mistakes" in prompt
        assert "visual variety" in prompt
        assert "final editorial review" in prompt
        assert "reread appeal" in prompt
        assert "rabbit, fox, bear, robot, magic forest, magic tree, unicorn" in prompt
        assert "Creativity must never weaken visual consistency" in prompt
        assert "Visual Bible is the single" in prompt
        assert "must not invent new character appearances inside page fields" in prompt

    child_prompt = load_prompt("prompts/story/story_plan_child_hero_prompt.txt")
    imagined_prompt = load_prompt("prompts/story/story_plan_imagined_cast_prompt.txt")

    assert "child-hero situation" in child_prompt
    assert "child-hero story role" in child_prompt
    assert "difficult to imagine the same emotional journey happening without this child as the hero" in child_prompt
    assert "The hero is always the selected child" in child_prompt
    assert "protagonist strength" in imagined_prompt
    assert "difficult to imagine the same story with a different hero" in imagined_prompt
    assert 'visual_bible.hero.character_id must NOT be "hero_child"' in imagined_prompt


def test_story_plan_prompts_include_developmental_language_profiles():
    for prompt_path in (
        "prompts/story/story_plan_child_hero_prompt.txt",
        "prompts/story/story_plan_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## AGE ADAPTATION & LANGUAGE PROFILE" in prompt
        assert "not only reading difficulty" in prompt
        assert "18-24 month old child" in prompt
        assert "sitting on a parent's lap" in prompt
        assert "Every sentence should be understandable from spoken" in prompt
        assert "language alone" in prompt
        assert "sound words such as baa, moo, quack" in prompt
        assert "Sentence length: 1-6 words preferred, maximum 8 words." in prompt
        assert "Sentence length: 5-12 words." in prompt
        assert "Sentence length: 8-18 words." in prompt
        assert "vocabulary, sentence rhythm, emotional depth" in prompt
        assert '"target_reader": ""' in prompt
        assert '"narration_style": ""' in prompt
        assert '"humor_style": ""' in prompt
        assert '"emotional_complexity": ""' in prompt
        assert '"concept_complexity": ""' in prompt
        assert '"sound_word_usage": ""' in prompt
        assert '"sensory_language": ""' in prompt
        assert '"page_focus": ""' in prompt
        assert '"read_aloud_rhythm": ""' in prompt


def test_story_generation_prompts_keep_moral_separate_from_story_closure():
    for prompt_path in (
        "prompts/story/story_generation_child_hero_prompt.txt",
        "prompts/story/story_generation_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "The final page must read like the story is complete" in prompt
        assert "Do not end as if another story page is" in prompt
        assert "The moral is a separate short lesson after the story" in prompt
        assert "It must not replace the" in prompt
        assert "final page's emotional closure" in prompt
        assert "final page closes the conflict and feels complete" in prompt


def test_story_generation_prompts_require_plan_fidelity_and_silent_review():
    for prompt_path in (
        "prompts/story/story_generation_child_hero_prompt.txt",
        "prompts/story/story_generation_imagined_cast_prompt.txt",
    ):
        prompt = load_prompt(prompt_path)

        assert "## PLAN FIDELITY" in prompt
        assert "story_plan_json is already finalized" in prompt
        assert "Do not redesign, improve, reinterpret, or expand the plot beyond the plan" in prompt
        assert "preserving all planned events, character roles, emotional beats" in prompt
        assert "and story progression" in prompt
        assert "Do not copy, paraphrase, or mechanically restate scene_description" in prompt
        assert "Transform them into natural, engaging story narration" in prompt
        assert "Expand each planned scene into a vivid story moment" in prompt
        assert "body language" in prompt
        assert "without changing what happens in the plan" in prompt
        assert "Prefer showing emotions through actions instead of directly stating them" in prompt
        assert "Avoid repetitive sentence patterns" in prompt
        assert "On every page, ensure the hero actively observes, decides, speaks, feels, or takes action" in prompt
        assert "Avoid pages where the hero is only watching events happen" in prompt
        assert "## SILENT STORY REVIEW" in prompt
        assert "Every page feels like part of one continuous story" in prompt
        assert "No page reads like a summary of the plan" in prompt
        assert "The hero's personality stays consistent and grows naturally" in prompt
        assert "The climax feels earned" in prompt
        assert "warm emotional closure before the moral" in prompt
        assert "The narration sounds natural when read aloud" in prompt


def test_cast_mode_prompt_paths_are_split():
    child_story = SimpleNamespace(use_child_character=True)
    imagined_story = SimpleNamespace(use_child_character=False)

    assert StoryService._story_plan_prompt_path(child_story).endswith("story_plan_child_hero_prompt.txt")
    assert StoryService._story_plan_prompt_path(imagined_story).endswith("story_plan_imagined_cast_prompt.txt")
    assert StoryService._story_generation_prompt_path(child_story).endswith("story_generation_child_hero_prompt.txt")
    assert StoryService._story_generation_prompt_path(imagined_story).endswith(
        "story_generation_imagined_cast_prompt.txt"
    )


def test_imagined_cast_story_plan_prompt_does_not_force_selected_child_as_hero():
    template = load_prompt("prompts/story/story_plan_imagined_cast_prompt.txt")
    story = SimpleNamespace(
        age_group=AgeGroup.EARLY_READER,
        input_request={
            "story_seed": "A rocket loses its map near a moon garden.",
            "visual_preference": "bright colors and lots of motion",
        },
    )
    child = SimpleNamespace(
        first_name="Mira",
        gender="female",
        age=6,
        character_metadata={},
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={
            "category": "space adventure",
            "learning_goal": "problem solving",
            "context": "A story about a rocket that loses its map.",
        },
        theme="space adventure",
        hobby="reading",
        pages=8,
        character_context={
            "use_child_character": False,
            "cast_mode": StoryService.CAST_MODE_IMAGINED,
            "character_description": "Invent the best story hero from the inputs.",
            "child_age_label": "Early Reader",
            "child_age_visual_guidance": "age-appropriate proportions for the reader band",
            "cast_mode_instructions": "IMAGINED_CAST: create a named hero and complete recurring cast.",
        },
    )

    assert "The hero is always the selected child" not in prompt
    assert "The selected child solves the conflict" not in prompt
    assert "Do NOT use the selected child profile as a story character" in prompt
    assert 'visual_bible.hero.character_id must NOT be "hero_child"' in prompt
    assert "Mira" not in prompt
    assert "{planning_preferences_json}" not in prompt
    assert '"story_seed":"A rocket loses its map near a moon garden."' in prompt
    assert '"visual_preference":"bright colors and lots of motion"' in prompt
    assert "protagonist strength" in prompt


def test_child_hero_story_plan_prompt_keeps_child_hero_and_uses_planning_preferences():
    template = load_prompt("prompts/story/story_plan_child_hero_prompt.txt")
    story = SimpleNamespace(
        age_group=AgeGroup.EARLY_READER,
        input_request={
            "story_seed": "A garden puzzle opens only when clues are solved slowly.",
            "title_preference": "warm and curious, avoid The Little",
        },
    )
    child = SimpleNamespace(
        first_name="Mira",
        gender="female",
        age=6,
        character_metadata={"description": "A curious child with bright eyes."},
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={
            "category": "garden mystery",
            "learning_goal": "solve problems step by step",
            "context": "A child discovers a patient puzzle in a garden.",
        },
        theme="garden mystery",
        hobby="reading",
        pages=8,
        character_context={
            "use_child_character": True,
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO,
            "character_description": "A curious child with bright eyes.",
            "child_age_label": "Early Reader",
            "child_age_visual_guidance": "age-appropriate proportions for the reader band",
            "cast_mode_instructions": "CHILD_HERO: preserve the selected child as the story hero.",
        },
    )

    assert "The hero is always the selected child" in prompt
    assert 'visual_bible.hero.character_id must be exactly "hero_child"' in prompt
    assert "child-hero story role" in prompt
    assert "{planning_preferences_json}" not in prompt
    assert '"story_seed":"A garden puzzle opens only when clues are solved slowly."' in prompt
    assert '"title_preference":"warm and curious, avoid The Little"' in prompt


def test_story_planning_preferences_fall_back_to_existing_context_without_new_request_fields():
    story = SimpleNamespace(input_request={})

    preferences = StoryService._story_planning_preferences(
        story,
        {
            "category": "space",
            "learning_goal": "careful observation",
            "context": "A comet leaves silver clues across the sky.",
        },
    )

    assert set(preferences) == set(
        [
            "story_seed",
            "protagonist_preference",
            "protagonist_avoid",
            "setting_preference",
            "tone_preference",
            "conflict_preference",
            "moral_preference",
            "visual_preference",
            "title_preference",
            "cultural_context",
            "avoid_elements",
        ]
    )
    assert preferences["story_seed"] == "A comet leaves silver clues across the sky."
    assert preferences["protagonist_preference"] == ""


def test_story_generation_imagined_prompt_preserves_visual_bible_hero():
    prompt = load_prompt("prompts/story/story_generation_imagined_cast_prompt.txt")

    assert "Keep the selected child as the hero" not in prompt
    assert "Use the hero in visual_bible.hero as the main character" in prompt
    assert "The selected child profile is not introduced as a character" in prompt
    assert "child_action means the invented hero's planned action" in prompt


@pytest.mark.asyncio
async def test_child_hero_image_plan_attaches_child_character_reference():
    child = SimpleNamespace(first_name="Mira", character_image_url="https://cdn.test/mira.png")

    class _Children:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return child

    service = StoryService.__new__(StoryService)
    service.children = _Children()
    story = SimpleNamespace(
        id="story-id",
        user_id="user-id",
        child_id="child-id",
        use_child_character=True,
        ai_provider="openai",
        title="Mira's Map",
    )
    image_plan = {
        "visual_bible": {"hero": {"name": "Mira", "character_id": "hero_child"}},
        "cover": {"title_text": "Mira's Map"},
        "character_reference_manifest": [],
    }

    result = await service._ensure_image_plan_character_references(story, image_plan)

    manifest = result["character_reference_manifest"]
    assert manifest[0]["character_id"] == "hero_child"
    assert manifest[0]["reference_image_url"] == "https://cdn.test/mira.png"
    assert result["visual_bible"]["hero"]["reference_image_url"] == "https://cdn.test/mira.png"


@pytest.mark.asyncio
async def test_imagined_cast_image_plan_does_not_attach_child_character_reference():
    class _Children:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            raise AssertionError("imagined cast should not load child character reference")

    service = StoryService.__new__(StoryService)
    service.children = _Children()
    story = SimpleNamespace(
        id="story-id",
        user_id="user-id",
        child_id="child-id",
        use_child_character=False,
        ai_provider="openai",
        title="Robot Map",
    )
    image_plan = {
        "visual_bible": {
            "hero": {
                "name": "Brave Blue Robot",
                "character_id": "brave_blue_robot",
                "appearance": "A small rounded blue robot.",
            }
        },
        "cover": {"title_text": "Robot Map"},
        "character_reference_manifest": [],
    }

    result = await service._ensure_image_plan_character_references(story, image_plan)

    assert result["character_reference_manifest"] == []
    assert "reference_image_url" not in result["visual_bible"]["hero"]


def test_story_generation_context_softens_medical_harm_language():
    story_plan = {
        "title": "The Sparkling Park",
        "summary": "Pollution is making people sick near the park.",
        "theme": "Adventure, Environmental Awareness",
        "learning_goal": "Environmental Awareness",
        "moral_theme": "Caring for nature helps everyone.",
        "setting": "a community park",
        "tone": "hopeful",
        "central_problem": "People fall ill because the park has become an unhealthy health risk.",
        "hero_want": "Amayra wants the park to feel bright again.",
        "emotional_need": "Amayra needs to feel capable of helping.",
        "stakes": "Animals get sick and families stop playing there.",
        "climax_choice": "Amayra invites neighbors to clean the stream together.",
        "resolution_payoff": "The park feels clean and cheerful again.",
        "moral_explanation": "Small helpful actions can grow when people work together.",
        "content_anchors": {},
        "visual_bible": {},
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "People getting sick makes the park feel sad.",
                "characters_present": ["Amayra"],
                "emotional_beat": "concern",
                "learning_goal_integration": "Amayra notices what needs care.",
                "growth_step": "Amayra looks closely before acting.",
                "domain_detail": "litter near a stream",
                "page_turn_hook": "She spots a cleanup sign.",
                "continuity_requirements": [],
            }
        ],
    }

    reduced = StoryService._build_story_generation_context(story_plan)
    combined = " ".join(
        [
            reduced["summary"],
            reduced["central_problem"],
            reduced["stakes"],
            reduced["pages"][0]["scene_description"],
        ]
    ).lower()

    assert "fall ill" not in combined
    assert "sick" not in combined
    assert "health risk" not in combined
    assert "unclean" in combined
    assert "hard to enjoy" in combined or "cannot enjoy" in combined


def test_story_plan_template_renders_all_current_placeholders():
    template = load_prompt("prompts/story/story_plan_prompt.txt")
    story = SimpleNamespace(age_group=AgeGroup.EARLY_READER)
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        gender="girl",
        character_image_url="https://example.test/character.png",
        character_metadata={"description": "A curious child with bright eyes.", "style": "storybook"},
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={"learning_goal": "problem solving", "context": "moonlit library"},
        theme="adventure",
        hobby="reading",
        pages=10,
        character_context={
            "character_description": "A curious child with bright eyes.",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
    )

    assert "{character_profile_json}" not in prompt
    assert "{first_name}" not in prompt
    assert "{pages}" not in prompt
    assert "Mira" in prompt
    assert '"profile_summary": "A curious child with bright eyes."' in prompt


def test_story_plan_prompt_uses_safe_intent_and_omits_unrelated_trigger_terms():
    template = load_prompt("prompts/story/story_plan_prompt.txt")
    story = SimpleNamespace(age_group=AgeGroup.EARLY_READER)
    child = SimpleNamespace(
        first_name="Amayra",
        age=7,
        gender="female",
        character_image_url="https://example.test/character.png",
        character_metadata={
            "style": "storybook",
            "identity_profile": {
                "face_shape": "round",
                "skin_tone": "warm medium",
                "eye_color": "dark brown",
                "eye_shape": "almond",
                "mouth_shape": "medium width lips",
                "mouth_description": "closed-mouth smile",
                "smile_type": "closed-mouth smile",
                "hair_color": "dark brown",
                "hair_style": "two pigtails",
                "hair_length": "medium",
                "distinctive_features": ["bright eyes", "small tooth gap"],
            },
        },
    )

    prompt = StoryService._render_story_plan_prompt(
        template,
        story=story,
        child=child,
        source_inputs={
            "learning_goal": "Kindness and personal hygiene",
            "context": "Story to teach her kindness with people and if someone says no she should respect it",
        },
        theme="Family",
        hobby="reading",
        pages=8,
        character_context={
            "use_child_character": True,
            "cast_mode": StoryService.CAST_MODE_CHILD_HERO,
            "character_description": "Detailed identity is reserved for image generation.",
            "child_age_label": "7 years old",
            "child_age_visual_guidance": "older child proportions with natural child build",
        },
    )
    lowered = prompt.lower()

    assert "someone says no" not in lowered
    assert "respect it" not in lowered
    assert "personal hygiene" not in lowered
    assert "asks for space" in lowered
    assert "daily self-care routines such as brushing teeth and washing hands" in lowered
    assert "pool" not in lowered
    assert "swim" not in lowered
    assert "upper body" not in lowered
    assert "mouth" not in lowered
    assert "lip" not in lowered
    assert "tooth" not in lowered
    assert '"central_problem": ""' in prompt
    assert '"story_spine": {' in prompt
    assert '"failed_attempt": ""' in prompt
    assert '"language_profile": {' in prompt
    assert '"sentence_length": ""' in prompt
    assert "Follow language_profile" not in prompt
    assert "1-8 words per sentence" in prompt
    assert "5-12 words per sentence" in prompt
    assert "8-18 words per sentence" in prompt
    assert "Each page should happen because of the previous page" in prompt
    assert "The first meaningful attempt must not completely solve the problem" in prompt
    assert '"visual_bible": {' in prompt
    assert '"character_id": "hero_child"' in prompt
    assert '"hair_lock": ""' in prompt
    assert '"outfit_lock": ""' in prompt
    assert '"body_scale_lock": ""' in prompt
    assert '"relative_size": ""' in prompt
    assert 'visual_bible.hero.character_id must be "hero_child"' in prompt
    assert "Use the exact Visual Bible names in pages.characters_present" in prompt
    assert "same child height, build, proportions" in prompt
    assert '"role": ""' in prompt


@pytest.mark.asyncio
async def test_story_plan_generation_retries_with_compact_prompt_on_google_safety_block():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Amayra",
                age=7,
                gender="female",
                character_metadata={
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "mouth_shape": "medium width lips",
                        "hair_color": "dark brown",
                        "hair_style": "two pigtails",
                    }
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    class _FakeProvider:
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise AppException(
                    "Empty response from Google API prompt_feedback={'block_reason': "
                    "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                    code="EMPTY_RESPONSE",
                )
            plan = {
                "title": "Amayra's Kind Choice",
                "summary": "Amayra practices listening kindly.",
                "pages": [{"page_number": 1}],
                "visual_bible": {"hero": {"name": "Amayra", "appearance": "friendly child", "outfit": "blue dress"}},
            }
            return TextGenerationResult(
                text=json.dumps(plan),
                prompt_used=prompt,
                model="fake-model",
                metadata={"provider": "fake", "finish_reason": "STOP"},
            )

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    story = SimpleNamespace(
        id="story-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        category="Family",
        learning_goal="Kindness",
        context="Story to teach her kindness with people and if someone says no she should respect it",
        event_description=None,
        input_request={"use_child_character": True},
    )

    plan = await service._step_generate_plan(story, SimpleNamespace())

    assert len(service._ai_provider.prompts) == 2
    assert "SAFE STORY REQUEST JSON" in service._ai_provider.prompts[1]
    assert "someone says no" not in service._ai_provider.prompts[1].lower()
    assert plan["source_inputs"] == {
        "category": "Family",
        "learning_goal": "Kindness",
        "context": "Story to teach her kindness with people and if someone says no she should respect it",
    }
    step = service.story_steps.created[0]
    assert step.status.value == "COMPLETED"
    assert step.error_message is None
    assert step.prompt == service._ai_provider.prompts[1]


@pytest.mark.asyncio
async def test_standard_image_plan_generation_retries_with_compact_prompt_on_google_safety_block():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Mira",
                age=6,
                character_image_url="/media/mira.png",
                character_metadata={
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "hair_color": "dark brown",
                        "hair_style": "side-swept bob",
                    }
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeProvider:
        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise AppException(
                    "Empty response from Google API prompt_feedback={'block_reason': "
                    "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                    code="EMPTY_RESPONSE",
                )
            image_plan = {
                "visual_bible": {
                    "hero": {
                        "character_id": "hero_child",
                        "name": "Mira",
                        "appearance": "Mira has a round face and side-swept bob.",
                        "outfit": "blue tunic with white sneakers",
                        "signature_item": "star bracelet",
                    },
                    "companion": {"appearance": ""},
                    "recurring_characters": [],
                },
                "character_reference_manifest": [
                    {"character_id": "hero_child", "name": "Mira", "role": "hero_child"}
                ],
                "cover": {
                    "title_text": "Mira's Map",
                    "visual_focus": "Mira holds a glowing map.",
                    "emotion": "wonder",
                    "characters_present": ["Mira"],
                    "reference_character_ids": ["hero_child"],
                    "image_prompt": "Mira holds a glowing map.",
                },
                "pages": [
                    {
                        "page_number": 1,
                        "story_role": "opening",
                        "visual_importance": "medium",
                        "emotion": "wonder",
                        "scene_action": "Mira opens the map.",
                        "environment": "cozy library",
                        "characters_present": ["Mira"],
                        "reference_character_ids": ["hero_child"],
                        "image_prompt": "Mira opens the map in a cozy library.",
                    }
                ],
                "back_cover": {
                    "emotion": "calm",
                    "characters_present": ["Mira"],
                    "reference_character_ids": ["hero_child"],
                    "image_prompt": "Mira smiles with the map.",
                },
            }
            return TextGenerationResult(
                text=json.dumps(image_plan),
                prompt_used=prompt,
                model="fake-model",
                metadata={"provider": "fake", "finish_reason": "STOP"},
            )

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    story = SimpleNamespace(
        id="story-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        title="Mira's Map",
        input_request={"use_child_character": True},
        use_child_character=True,
    )
    story_plan = {
        "title": "Mira's Map",
        "visual_bible": {
            "hero": {
                "appearance": (
                    "Mira keeps the same skin tone, body proportions, upper body pose, "
                    "mouth, lips, and teeth."
                ),
                "outfit": "rash guard, swim shorts, and leggings",
            }
        },
        "pages": [{"page_number": 1, "visual_brief": "Mira opens a map with no horror imagery."}],
    }
    story_json = {"title": "Mira's Map", "pages": [{"page_number": 1, "text": "Mira opened the map."}]}

    image_plan = await service._step_generate_image_plan(story, story_plan, story_json, SimpleNamespace())

    assert len(service._ai_provider.prompts) == 2
    assert "children's picture-book illustration planner" in service._ai_provider.prompts[1]
    assert "Avoid sensitive negative phrasing" in service._ai_provider.prompts[1]
    assert image_plan["pages"][0]["reference_character_ids"] == ["hero_child"]


@pytest.mark.asyncio
async def test_custom_image_plan_generation_uses_safe_prompt_without_safety_fallback():
    class _FakeChildren:
        async def get_for_user(self, user_id, child_id):
            _ = user_id, child_id
            return SimpleNamespace(
                first_name="Mira",
                age=6,
                character_image_url="/media/mira.png",
                character_metadata={
                    "identity_summary": (
                        "Mira has a round face, warm medium skin tone, brown almond eyes, "
                        "a gentle mouth, and age-appropriate body proportions."
                    ),
                    "identity_profile": {
                        "face_shape": "round",
                        "skin_tone": "warm medium",
                        "eye_color": "dark brown",
                        "eye_shape": "almond",
                        "hair_color": "dark brown",
                        "hair_style": "side-swept bob",
                    },
                },
            )

    class _FakeSteps:
        def __init__(self):
            self.created = []

        async def create(self, story_id, step_name):
            step = SimpleNamespace(
                story_id=story_id,
                step_name=step_name,
                prompt=None,
                status=None,
                started_at=None,
                completed_at=None,
                retry_count=0,
                error_message=None,
                response=None,
            )
            self.created.append(step)
            return step

        async def update(self, step):
            return step

    class _FakeSession:
        async def commit(self):
            return None

    class _FakeProvider:
        text_model = "fake-google-text-model"

        def __init__(self):
            self.prompts = []

        async def generate_text(self, prompt, **kwargs):
            _ = kwargs
            self.prompts.append(prompt)
            raise AppException(
                "Empty response from Google API prompt_feedback={'block_reason': "
                "<BlockedReason.PROHIBITED_CONTENT: 'PROHIBITED_CONTENT'>}",
                code="EMPTY_RESPONSE",
            )

    class CustomStoryWorkflow(SimpleNamespace):
        pass

    service = StoryService.__new__(StoryService)
    service.children = _FakeChildren()
    service.story_steps = _FakeSteps()
    service.session = _FakeSession()
    service._ai_provider = _FakeProvider()
    workflow = CustomStoryWorkflow(
        id="workflow-1",
        user_id="user-1",
        child_id="child-1",
        age_group=AgeGroup.EARLY_READER,
        title="Mira's Map",
        input_request={"use_child_character": True},
        use_child_character=True,
    )
    story_plan = {
        "title": "Mira's Map",
        "visual_bible": {
            "hero": {
                "appearance": (
                    "Mira keeps the same skin tone, body proportions, upper body pose, "
                    "mouth, lips, and teeth."
                ),
                "outfit": "rash guard, swim shorts, and leggings",
            }
        },
        "pages": [{"page_number": 1, "visual_brief": "Mira opens a map with no horror imagery."}],
    }
    story_json = {"title": "Mira's Map", "pages": [{"page_number": 1, "text": "Mira opened the map."}]}

    with pytest.raises(AppException) as exc_info:
        await service._step_generate_image_plan(workflow, story_plan, story_json, SimpleNamespace())

    assert exc_info.value.code == "EMPTY_RESPONSE"
    assert len(service._ai_provider.prompts) == 1
    prompt = service._ai_provider.prompts[0]
    lowered = prompt.lower()
    assert "safe character planning summary" in lowered
    assert "character identity lock" not in lowered
    assert "skin tone" not in lowered
    assert "body proportions" not in lowered
    assert "upper body" not in lowered
    assert "rash guard" not in lowered
    assert "swim shorts" not in lowered
    assert "leggings" not in lowered
    assert "horror" not in lowered
    assert "aggressive" not in lowered
    assert "frightening" not in lowered
    assert "mouth" not in lowered
    assert "lips" not in lowered
    assert "teeth" not in lowered


def test_character_context_uses_identity_profile_not_legacy_analysis_text():
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        character_metadata={
            "identity_profile": {
                "face_shape": "round",
                "skin_tone": "warm medium",
                "eye_color": "brown",
                "eye_shape": "almond",
                "eyebrow_shape": "soft arched",
                "nose_shape": "small rounded",
                "mouth_description": "gentle smile",
                "hair_color": "dark brown",
                "hair_style": "side-swept",
                "hair_length": "short",
                "distinctive_features": ["round cheeks"],
            },
            "analysis_text": "OLD TEXT: large head, large eyes, exaggerated cartoon child",
            "description": "fallback description",
        },
    )

    context = StoryService._extract_character_analysis(child)

    assert "Stable Visual Identity" in context
    assert "Face shape: round" in context
    assert "OLD TEXT" not in context
    assert "large head" not in context
    assert "fallback description" not in context


def test_character_reference_context_prefers_persisted_identity_summary():
    child = SimpleNamespace(
        first_name="Mira",
        age=6,
        character_metadata={
            "identity_summary": "Persisted summary from child profile metadata.",
            "identity_profile": {
                "identity_summary": "Nested summary from identity profile.",
                "face_shape": "round",
            },
        },
    )

    context = StoryService._build_character_reference_context(child)

    assert context["identity_summary"] == "Persisted summary from child profile metadata."


def test_prompt_character_identity_lock_formats_summary_profile_and_age():
    character_context = {
        "child_name": "Mira",
        "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
        "identity_summary": "Persisted summary from child profile metadata.",
        "child_age_label": "6 years old",
        "child_age_visual_guidance": "early-reader child proportions",
    }

    lock = StoryService._format_prompt_character_identity_lock(character_context)

    assert "Hero child name: Mira" in lock
    assert "Identity summary: Persisted summary from child profile metadata." in lock
    assert "Name: Mira" in lock
    assert "Child age: 6 years old" in lock
    assert "Age/body guidance: early-reader child proportions" in lock


def test_story_image_generation_prompt_has_stronger_text_and_consistency_rules():
    prompt = load_prompt("prompts/story/image_generation_prompt.txt")

    assert "Do NOT render any text, letters, words, numbers, logos, labels, signs" in prompt
    assert "Story pages must be completely text-free unless Current Page Data explicitly" in prompt
    assert "render ONLY the exact Story Title as the sole visible text on the cover" in prompt
    assert "do not generate subtitles, author names, publisher logos, decorative words" in prompt
    assert "Only render characters explicitly listed in Current Page Data" in prompt
    assert "Do not invent" in prompt
    assert "background children" in prompt
    assert "Do not introduce new important objects or props unless specified" in prompt
    assert "maintain consistent camera distance and character scale across story pages" in prompt
    assert "Keep the main subject fully inside the frame" in prompt
    assert "Avoid cutting off the head" in prompt
    assert "Maintain consistent color palette, rendering style, and lighting mood" in prompt
    assert "visually rich but uncluttered" in prompt
    assert "naturally look toward the action" in prompt
    assert "main story object" in prompt
    assert "Facial expressions must accurately match the page emotion" in prompt
    assert "duplicated objects" in prompt
    assert "duplicated accessories" in prompt
    assert "repeated background artifacts" in prompt


def test_story_image_prompt_renders_child_name():
    template = load_prompt("prompts/story/image_generation_prompt.txt")

    prompt = StoryService._render_story_image_prompt(
        template,
        {"hero": {"name": "Mira", "appearance": "round face", "outfit": "blue dress"}},
        "Mira opens a glowing moon map.",
        {
            "child_name": "Mira",
            "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
            "identity_summary": "Mira has a round face and side-swept dark hair.",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
        page_type="story_page",
        target_aspect_ratio="1:1",
        page_data={"page_number": 1, "image_prompt": "Mira opens a glowing moon map."},
    )

    assert "## Hero Child Name" in prompt
    assert "## Character Identity Lock" in prompt
    assert "Child age: 6 years old" in prompt
    assert "Age/body guidance: early-reader child proportions" in prompt
    assert '"character_identity"' not in prompt
    assert "Mira has a round face and side-swept dark hair." in prompt
    assert "Stable Visual Identity" in prompt
    assert "Mira" in prompt
    assert "{child_name}" not in prompt
    assert "{story_title}" not in prompt
    assert "Internally verify the face lock before rendering" in prompt
    assert "Do not output, render, or" in prompt
    assert "display verification text" in prompt
    assert "Before rendering, state explicitly" not in prompt
    assert "Statement: \"I will render this child" not in prompt


def test_cover_image_prompt_includes_exact_story_title():
    template = load_prompt("prompts/story/image_generation_prompt.txt")

    prompt = StoryService._render_story_image_prompt(
        template,
        {"hero": {"name": "Mira", "appearance": "round face", "outfit": "blue dress"}},
        "Mira smiles under a glowing moon map.",
        {
            "child_name": "Mira",
            "character_description": "Name: Mira\nStable Visual Identity:\n- Face shape: round",
            "child_age_label": "6 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
        page_type="cover",
        target_aspect_ratio="4:5",
        page_data={"image_prompt": "Mira smiles under a glowing moon map."},
        story_title="Mira and the Moon Map",
    )

    assert "## Story Title" in prompt
    assert "Mira and the Moon Map" in prompt
    assert '"title_text": "Mira and the Moon Map"' in prompt
    assert "render the exact Story Title" in prompt
    assert "black rectangle" in prompt


def test_compact_json_serializes_story_generation_context_without_pretty_whitespace():
    reduced = {
        "title": "Tiny Test",
        "summary": "Short.",
        "theme": "focus",
        "learning_goal": "focus",
        "moral_theme": "paying attention",
        "setting": "a playroom",
        "tone": "warm",
        "visual_bible": {},
        "pages": [{"page_number": 1, "story_role": "introduction"}],
    }

    assert _compact_json(reduced) == (
        '{"title":"Tiny Test","summary":"Short.","theme":"focus","learning_goal":"focus",'
        '"moral_theme":"paying attention","setting":"a playroom","tone":"warm","visual_bible":{},'
        '"pages":[{"page_number":1,"story_role":"introduction"}]}'
    )


def test_normalize_story_output_adds_deterministic_page_narration():
    raw_story_json = {
        "title": "Mira and the Moon Map",
        "summary": "Mira solves a moonlit puzzle.",
        "theme": "patience",
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Mira opened the moon map and watched silver paths appear.",
            }
        ],
        "moral": "Small patient steps can solve big puzzles.",
    }
    plan = {"title": "Mira and the Moon Map", "theme": "patience", "pages": [{"page_number": 1}]}
    story = SimpleNamespace(
        category="adventure",
        event_description=None,
        learning_goal=None,
        context=None,
        age_group="3-6",
    )

    normalized = _normalize_story_output(raw_story_json, plan, story)

    assert normalized["pages"] == [
        {
            "page_number": 1,
            "text": "Mira opened the moon map and watched silver paths appear.",
            "emotion": "wonder",
            "narration": {
                "tone": "curious",
                "pace": "slow",
                "voice_style": "warm animated storyteller",
            },
        }
    ]
    assert "speech_narration" not in normalized["pages"][0]
    assert normalized["moral"] == {
        "page_number": 2,
        "text": "Small patient steps can solve big puzzles.",
    }


def test_normalize_story_output_builds_selected_language_variants():
    raw_story_json = {
        "title": {"en": "Mira and the Moon Map", "hi": "मीरा और चाँद का नक्शा"},
        "summary": {"en": "Mira solves a moonlit puzzle.", "hi": "मीरा चाँदनी वाली पहेली सुलझाती है."},
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": {
                    "en": "Mira opened the moon map.",
                    "hi": "मीरा ने चाँद का नक्शा खोला.",
                },
            }
        ],
        "moral": {
            "en": "Small patient steps can solve big puzzles.",
            "hi": "धैर्य वाले छोटे कदम बड़ी पहेलियाँ सुलझा सकते हैं.",
        },
    }
    plan = {
        "title": "Mira and the Moon Map",
        "theme": "patience",
        "selected_languages": ["en", "hi"],
        "pages": [{"page_number": 1}],
    }
    story = SimpleNamespace(
        category="adventure",
        event_description=None,
        learning_goal=None,
        context=None,
        age_group="3-6",
        languages=["en", "hi"],
    )

    normalized = _normalize_story_output(raw_story_json, plan, story)

    assert normalized["title"] == "Mira and the Moon Map"
    assert normalized["languages"] == ["en", "hi"]
    assert normalized["language_variants"]["hi"]["title"] == "मीरा और चाँद का नक्शा"
    assert normalized["language_variants"]["hi"]["pages"][0]["text"] == "मीरा ने चाँद का नक्शा खोला."
    assert normalized["language_variants"]["hi"]["moral"] == {
        "page_number": 2,
        "text": "धैर्य वाले छोटे कदम बड़ी पहेलियाँ सुलझा सकते हैं.",
    }


def test_story_content_response_accepts_legacy_string_moral():
    response = StoryJsonContentResponse.model_validate(
        {
            "title": "Mira and the Moon Map",
            "pages": [{"page_number": 1, "text": "Mira listened."}],
            "moral": "Small patient steps can solve big puzzles.",
        }
    )

    assert response.moral is not None
    assert response.moral.page_number is None
    assert response.moral.text == "Small patient steps can solve big puzzles."


def test_normalize_story_output_rejects_page_count_mismatch():
    raw_story_json = {
        "title": "Mira and the Moon Map",
        "pages": [{"page_number": 1, "emotion": "wonder", "text": "One page only."}],
        "moral": "Keep trying.",
    }
    plan = {"title": "Mira and the Moon Map", "pages": [{"page_number": 1}, {"page_number": 2}]}
    story = SimpleNamespace(
        category="adventure",
        event_description=None,
        learning_goal=None,
        context=None,
        age_group="3-6",
    )

    with pytest.raises(AppException, match="expected 2"):
        _normalize_story_output(raw_story_json, plan, story)


@pytest.mark.asyncio
async def test_story_json_repair_rejects_page_count_mismatch():
    class _FakeProvider:
        async def generate_text(self, prompt, **kwargs):
            _ = prompt, kwargs
            return TextGenerationResult(
                text=json.dumps(
                    {
                        "title": "Mira and the Moon Map",
                        "summary": "A one-page bad repair.",
                        "pages": [{"page_number": 1, "emotion": "wonder", "text": "One page only."}],
                        "moral": "Keep trying.",
                    }
                ),
                prompt_used=prompt,
                model="fake",
                metadata={},
            )

    service = StoryService.__new__(StoryService)
    service._ai_provider = _FakeProvider()
    story = SimpleNamespace(id="story-1", age_group="3-6")
    parse_error = json.JSONDecodeError("bad json", "{", 0)

    repaired = await service._repair_story_generation_json_response(
        "{bad",
        parse_error,
        story,
        expected_page_count=2,
    )

    assert repaired is None


def test_build_page_narration_maps_emotion_and_age_group():
    assert build_page_narration("triumph", "6-9") == {
        "tone": "celebratory",
        "pace": "medium",
        "voice_style": "expressive adventure storyteller",
    }
    assert build_page_narration("unknown", "0-3") == {
        "tone": "curious",
        "pace": "slow",
        "voice_style": "gentle lullaby bedtime storyteller",
    }
    assert build_page_narration("unknown", "2-4")["voice_style"] == "gentle lullaby bedtime storyteller"


def test_build_image_plan_context_reduces_story_plan_and_story_json_to_visual_fields():
    story_plan = {
        "title": "Mira and the Moon Map",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira opens a glowing atlas.",
                "characters_present": ["Mira"],
                "child_action": "Mira points to the silver path.",
                "emotional_beat": "quiet wonder",
                "domain_detail": "The atlas shows one silver path across the page.",
                "continuity_requirements": ["Mira keeps the atlas open."],
            }
        ],
    }
    story_json = {
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Moonlight slipped across the atlas while Mira traced the first path.",
            }
        ],
    }

    compact_story_plan, compact_story_json = StoryService._build_image_plan_context(story_plan, story_json)

    assert compact_story_plan == {
        "title": "Mira and the Moon Map",
        "setting": "a moonlit library",
        "tone": "gentle mystery",
        "content_anchors": {
            "required_names": ["Moon map", "silver path"],
            "required_facts": ["Maps can show one step at a time."],
            "age_safe_explanations": ["A clue can help you choose the next step."],
        },
        "visual_bible": {
            "style": "premium 3D storybook",
            "hero": {
                "name": "Mira",
                "appearance": "A curious child with bright eyes.",
                "outfit": "Yellow raincoat and red scarf.",
                "signature_item": "Moon map",
            },
        },
        "pages": [
            {
                "page_number": 1,
                "story_role": "introduction",
                "scene_description": "Mira opens a glowing atlas.",
                "characters_present": ["Mira"],
                "child_action": "Mira points to the silver path.",
                "emotional_beat": "quiet wonder",
                "domain_detail": "The atlas shows one silver path across the page.",
                "continuity_requirements": ["Mira keeps the atlas open."],
            }
        ],
    }
    assert compact_story_json == {
        "expected_page_count": 1,
        "expected_page_numbers": [1],
        "pages": [
            {
                "page_number": 1,
                "emotion": "wonder",
                "text": "Moonlight slipped across the atlas while Mira traced the first path.",
            }
        ],
    }


def test_story_image_plan_prompt_has_visual_continuity_and_age_simplicity_rules():
    prompt = load_prompt("prompts/story/image_plan_prompt.txt")

    assert "## VISUAL SIMPLICITY BY AGE" in prompt
    assert "0-3: use large readable characters, simple backgrounds, few objects" in prompt
    assert "3-6: allow richer environments while maintaining a clear focal point" in prompt
    assert "6-9: allow richer visual context and more environmental detail" in prompt
    assert "Every illustration should communicate one dominant visual action" in prompt
    assert "The hero should be the first element a child notices within one second" in prompt
    assert "## VISUAL CONTINUITY" in prompt
    assert "camera orientation when appropriate" in prompt
    assert "environment continuity" in prompt
    assert "character positions when continuing the same scene" in prompt
    assert "prop locations" in prompt
    assert "lighting direction" in prompt
    assert "When consecutive pages occur in the same location, reuse the same environment" in prompt


def test_custom_safe_image_plan_prompt_enforces_story_page_contract():
    prompt = StoryService._custom_safe_image_plan_prompt(
        {
            "visual_bible": {"hero": {"character_id": "mira"}},
            "pages": [{"page_number": 1}, {"page_number": 2}],
        },
        {
            "expected_page_count": 2,
            "expected_page_numbers": [1, 2],
            "pages": [{"page_number": 1, "text": "One."}, {"page_number": 2, "text": "Two."}],
        },
        {
            "use_child_character": False,
            "cast_mode": StoryService.CAST_MODE_IMAGINED,
            "character_description": "Invented cast.",
        },
    )

    assert "Story JSON is the source of truth for page count" in prompt
    assert "Output pages.length must equal Story JSON expected_page_count exactly" in prompt
    assert "Output page_number values must match Story JSON expected_page_numbers exactly" in prompt
    assert "Do not skip, merge, collapse, add, or reorder pages" in prompt
    assert '"body_scale_lock":""' in prompt
    assert '"relative_size":""' in prompt
    assert '"signature_item":""' in prompt
    assert "For every recurring character, fill visual_bible locks" in prompt


def test_validate_image_plan_page_contract_rejects_missing_page():
    story_json = {
        "pages": [
            {"page_number": 1, "text": "One."},
            {"page_number": 2, "text": "Two."},
            {"page_number": 3, "text": "Three."},
        ]
    }
    image_plan = {
        "pages": [
            {"page_number": 1, "image_prompt": "One."},
            {"page_number": 3, "image_prompt": "Three."},
        ]
    }

    with pytest.raises(AppException) as exc_info:
        StoryService._validate_image_plan_page_contract(image_plan, story_json)

    assert exc_info.value.code == "IMAGE_PLAN_PAGE_COUNT_MISMATCH"
    assert exc_info.value.details["expected_page_numbers"] == [1, 2, 3]
    assert exc_info.value.details["received_page_numbers"] == [1, 3]
