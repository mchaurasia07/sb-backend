from types import SimpleNamespace

from app.service.story_service_batch_service import BatchImageItem, BatchImageReference, StoryServiceBatchService
from app.utils.google_tts_utils import GoogleTTSProvider


def test_image_batch_payload_includes_new_page_data_shape():
    page_data = {
        "page_number": 1,
        "story_role": "introduction",
        "visual_importance": "medium",
        "emotion": "wonder",
        "scene_action": "Mira opens the moon map.",
        "environment": "Moonlit library.",
        "characters_present": ["Mira"],
        "image_prompt": "Mira opens a glowing moon map in a moonlit library.",
    }
    item = BatchImageItem(
        key="page_1",
        page_type="page",
        page_number=1,
        page_data=page_data,
        source_image_prompt=page_data["image_prompt"],
        rendered_prompt='Visual Bible: {"hero":{}} Page Data: {"page_number":1}',
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_1.png",
        text="Mira opened the moon map.",
    )

    payload = StoryServiceBatchService._image_item_payload(item)

    assert payload["page_data"] == page_data
    assert payload["rendered_prompt"] == item.rendered_prompt
    assert "{visual_bible}" not in payload["rendered_prompt"]
    assert "{page_data}" not in payload["rendered_prompt"]


def test_audio_batch_items_use_page_narration_from_story_json():
    service = StoryServiceBatchService.__new__(StoryServiceBatchService)
    service.tts_provider = GoogleTTSProvider()
    story_json = {
        "pages": [
            {
                "page_number": 1,
                "text": "Mira opened the moon map.",
                "emotion": "triumph",
                "narration": {
                    "tone": "celebratory",
                    "pace": "medium",
                    "voice_style": "expressive cinematic storyteller",
                },
            }
        ]
    }

    items = service._build_audio_items(story_json)

    assert len(items) == 1
    assert items[0].tone == "celebratory"
    assert items[0].pace == "medium"
    assert items[0].voice_style == "expressive cinematic storyteller"
    assert items[0].emotion == "triumph"
    assert "expressive cinematic storyteller" in items[0].prompt


def test_audio_batch_items_use_story_age_group_when_page_narration_missing():
    service = StoryServiceBatchService.__new__(StoryServiceBatchService)
    service.tts_provider = GoogleTTSProvider()
    story_json = {
        "pages": [
            {
                "page_number": 1,
                "text": "Mira listened as the tiny bell chimed.",
                "emotion": "calm",
            }
        ]
    }

    items = service._build_audio_items(story_json, age_group="0-3")

    assert len(items) == 1
    assert items[0].tone == "soothing"
    assert items[0].pace == "slow"
    assert items[0].voice_style == "gentle lullaby bedtime storyteller"
    assert story_json["pages"][0]["narration"]["voice_style"] == "gentle lullaby bedtime storyteller"


def test_tts_prompt_marks_story_text_as_only_spoken_content():
    provider = GoogleTTSProvider()

    prompt = provider.build_prompt(
        "Mira opened the moon map.",
        pace="slow",
        language="en",
        voice_style="warm animated storyteller",
        tone="curious",
        emotion="wonder",
    )

    assert "Speak ONLY the story text contained within NARRATION_TEXT." in prompt
    assert "Do not add, remove, translate, summarize, explain, or rewrite any words." in prompt
    assert "Read exclamations, interjections, reactions, sound words, and expressive phrases" in prompt
    assert "<NARRATION_TEXT>" in prompt
    assert "Mira opened the moon map." in prompt


def test_google_tts_provider_reuses_single_process_instance():
    provider = GoogleTTSProvider()

    assert GoogleTTSProvider() is provider


def test_story_reference_image_prompt_uses_rendered_prompt_identity_lock():
    prompt = StoryServiceBatchService._story_reference_image_prompt(
        "Scene prompt with Character Identity Lock.",
        reference_images=[
            BatchImageReference(
                character_id="ria_the_pattern_maker",
                name="Ria",
                role="hero",
                image_url="/media/ria.png",
                part=SimpleNamespace(),
                priority=0,
            )
        ],
    )

    assert "Character Identity Lock inside the rendered prompt" in prompt
    assert "character_id=ria_the_pattern_maker; name=Ria; role=hero" in prompt
    assert "Respect basic scene etiquette" in prompt
    assert "do not draw outdoor shoes on feet" in prompt
    assert "This does not change the locked footwear design" in prompt
    assert "Scene prompt with Character Identity Lock." in prompt
    assert "generated Master Character Reference Portrait" in prompt


def test_story_reference_image_prompt_names_multiple_character_references():
    prompt = StoryServiceBatchService._story_reference_image_prompt(
        "Scene prompt with Character Identity Lock.",
        reference_images=[
            BatchImageReference(
                character_id="hero_child",
                name="Mira",
                role="hero_child",
                image_url="/media/child.png",
                part=SimpleNamespace(),
                priority=0,
            ),
            BatchImageReference(
                character_id="uncle_raj",
                name="Uncle Raj",
                role="mentor",
                image_url="/media/raj.png",
                part=SimpleNamespace(),
                priority=1,
            ),
        ],
    )

    assert "Attached images after this prompt are named character identity references" in prompt
    assert "character_id=hero_child; name=Mira; role=hero_child" in prompt
    assert "character_id=uncle_raj; name=Uncle Raj; role=mentor" in prompt
    assert "Scene prompt with Character Identity Lock." in prompt


def test_image_reference_selection_prioritizes_hero_and_visible_side_character():
    item = BatchImageItem(
        key="page_1",
        page_type="page",
        page_number=1,
        page_data={
            "characters_present": ["Mira", "Uncle Raj"],
            "reference_character_ids": ["hero_child", "uncle_raj"],
        },
        source_image_prompt="Mira and Uncle Raj study the map.",
        rendered_prompt="Rendered prompt.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_1.png",
        text="",
    )
    references = [
        BatchImageReference("hero_child", "Mira", "hero_child", "/media/mira.png", SimpleNamespace(), 0),
        BatchImageReference("uncle_raj", "Uncle Raj", "mentor", "/media/raj.png", SimpleNamespace(), 1),
        BatchImageReference("aunt_anu", "Aunt Anu", "supporting", "/media/anu.png", SimpleNamespace(), 2),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
    )

    assert [reference.character_id for reference in selected] == ["hero_child", "uncle_raj"]


def test_image_reference_selection_attaches_visible_imagined_hero_and_side_character():
    item = BatchImageItem(
        key="page_2",
        page_type="page",
        page_number=2,
        page_data={
            "characters_present": ["Ria", "Leo"],
            "reference_character_ids": ["ria_the_pattern_maker", "leo_the_explorer"],
        },
        source_image_prompt="Ria and Leo arrange leaves.",
        rendered_prompt="Rendered prompt.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_2.png",
        text="",
    )
    references = [
        BatchImageReference("ria_the_pattern_maker", "Ria", "hero", "/media/ria.png", SimpleNamespace(), 0),
        BatchImageReference("leo_the_explorer", "Leo", "friend", "/media/leo.png", SimpleNamespace(), 1),
        BatchImageReference("maya_the_artist", "Maya", "friend", "/media/maya.png", SimpleNamespace(), 2),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
        strict_page_refs=True,
    )

    assert [reference.character_id for reference in selected] == ["ria_the_pattern_maker", "leo_the_explorer"]


def test_image_reference_selection_does_not_attach_unrelated_refs_for_object_only_page():
    item = BatchImageItem(
        key="page_4",
        page_type="page",
        page_number=4,
        page_data={
            "characters_present": [],
            "reference_character_ids": [],
            "important_objects": ["red pencil"],
            "object_states": {"red pencil": {"count": 1, "location": "on ground"}},
        },
        source_image_prompt="A red pencil lies on the classroom floor.",
        rendered_prompt="Visual Bible mentions Ria and Leo.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_4.png",
        text="",
    )
    references = [
        BatchImageReference("ria_the_pattern_maker", "Ria", "hero", "/media/ria.png", SimpleNamespace(), 0),
        BatchImageReference("leo_the_explorer", "Leo", "friend", "/media/leo.png", SimpleNamespace(), 1),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
        strict_page_refs=True,
    )

    assert selected == []


def test_custom_image_reference_selection_ignores_names_from_rendered_visual_bible():
    item = BatchImageItem(
        key="page_1",
        page_type="page",
        page_number=1,
        page_data={
            "characters_present": ["Mira"],
            "reference_character_ids": ["hero_child"],
        },
        source_image_prompt="Mira studies the map.",
        rendered_prompt="Visual Bible mentions Uncle Raj and Aunt Anu, but they are not visible on this page.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_1.png",
        text="",
    )
    references = [
        BatchImageReference("hero_child", "Mira", "hero_child", "/media/mira.png", SimpleNamespace(), 0),
        BatchImageReference("uncle_raj", "Uncle Raj", "mentor", "/media/raj.png", SimpleNamespace(), 1),
        BatchImageReference("aunt_anu", "Aunt Anu", "supporting", "/media/anu.png", SimpleNamespace(), 2),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
        strict_page_refs=True,
    )

    assert [reference.character_id for reference in selected] == ["hero_child"]


def test_custom_image_reference_selection_includes_visible_side_character_only():
    item = BatchImageItem(
        key="page_4",
        page_type="page",
        page_number=4,
        page_data={
            "characters_present": ["Mira", "Uncle Raj"],
            "reference_character_ids": ["hero_child", "uncle_raj"],
        },
        source_image_prompt="Mira and Uncle Raj study the map.",
        rendered_prompt="Rendered prompt also mentions Aunt Anu in the Visual Bible.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_4.png",
        text="",
    )
    references = [
        BatchImageReference("hero_child", "Mira", "hero_child", "/media/mira.png", SimpleNamespace(), 0),
        BatchImageReference("uncle_raj", "Uncle Raj", "mentor", "/media/raj.png", SimpleNamespace(), 1),
        BatchImageReference("aunt_anu", "Aunt Anu", "supporting", "/media/anu.png", SimpleNamespace(), 2),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
        strict_page_refs=True,
    )

    assert [reference.character_id for reference in selected] == ["hero_child", "uncle_raj"]


def test_generic_image_reference_selection_still_uses_rendered_prompt_text_scan():
    item = BatchImageItem(
        key="page_1",
        page_type="page",
        page_number=1,
        page_data={
            "characters_present": ["Mira"],
            "reference_character_ids": ["hero_child"],
        },
        source_image_prompt="Mira studies the map.",
        rendered_prompt="The Visual Bible includes Uncle Raj as a recurring mentor.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_1.png",
        text="",
    )
    references = [
        BatchImageReference("hero_child", "Mira", "hero_child", "/media/mira.png", SimpleNamespace(), 0),
        BatchImageReference("uncle_raj", "Uncle Raj", "mentor", "/media/raj.png", SimpleNamespace(), 1),
    ]

    selected = StoryServiceBatchService._select_reference_images_for_item(
        item,
        references,
        model="gemini-3.1-flash-image",
    )

    assert [reference.character_id for reference in selected] == ["hero_child", "uncle_raj"]


def test_custom_visible_character_lock_repeats_hero_hair_outfit_and_body_scale():
    visual_bible = {
        "hero": {
            "character_id": "hero_child",
            "name": "Amayra",
            "appearance": "Dark brown hair pulled back into two pigtails with wispy strands around the forehead.",
            "outfit": "Lavender tunic, forest green covered play bottoms, brown ankle boots, leather pouch.",
            "footwear": "brown ankle boots",
            "signature_item": "smooth river stone",
        },
        "recurring_characters": [
            {
                "character_id": "grandma_elara",
                "name": "Grandma Elara",
                "appearance": "Silver-white hair in a neat bun.",
                "outfit": "deep indigo dress",
            }
        ],
    }
    page_data = {
        "characters_present": ["Amayra"],
        "reference_character_ids": ["hero_child"],
    }

    lock = StoryServiceBatchService._custom_visible_character_lock_block(visual_bible, page_data)

    assert "## Custom Visible Character Lock" in lock
    assert "two pigtails" in lock
    assert "wispy strands around the forehead" in lock
    assert "Lavender tunic" in lock
    assert "forest green covered play bottoms" in lock
    assert "brown ankle boots" in lock
    assert "Locked footwear: brown ankle boots" in lock
    assert "Footwear etiquette" in lock
    assert "no outdoor shoes on beds or sacred/no-shoe spaces" in lock
    assert "leather pouch" in lock
    assert "same reusable child body model" in lock
    assert "same child height" in lock
    assert "no loose hair" in lock
    assert "no open hair" in lock
    assert "no changed body build" in lock
    assert "grandma_elara" not in lock


def test_custom_story_rendered_image_prompt_adds_lock_and_rewrites_clothing_conflict():
    template = (
        "## Visual Bible\n{visual_bible}\n"
        "## Current Page\n{page_data}\n"
        "## Character Identity Lock\n{character_identity_lock}\n"
        "Expressions, pose, camera angle, and scene clothing may vary only when the\n"
        "Current Page Data requests it. The underlying face/head identity must stay the\n"
        "same. Do not make a new variant that merely resembles the reference."
    )
    visual_bible = {
        "hero": {
            "character_id": "hero_child",
            "name": "Amayra",
            "appearance": "Dark brown hair pulled back into two pigtails with wispy strands around the forehead.",
            "outfit": "Lavender tunic, forest green covered play bottoms, brown ankle boots, leather pouch.",
            "footwear": "brown ankle boots",
        }
    }

    prompt = StoryServiceBatchService._render_custom_story_image_prompt(
        template,
        visual_bible,
        "Amayra stands near a tree.",
        {
            "child_name": "Amayra",
            "character_description": "Name: Amayra",
            "child_age_label": "7 years old",
            "child_age_visual_guidance": "early-reader child proportions",
        },
        page_type="story_page",
        target_aspect_ratio="1:1",
        page_data={
            "characters_present": ["Amayra"],
            "reference_character_ids": ["hero_child"],
            "image_prompt": "Amayra stands near a tree.",
        },
        story_title="Amayra and the Tree",
        is_custom_story=True,
    )

    assert "## Custom Visible Character Lock" in prompt
    assert "scene clothing may vary" not in prompt
    assert "The locked story outfit, hairstyle, body build, height, body proportions" in prompt
    assert "Locked footwear: brown ankle boots" in prompt
    assert "Footwear etiquette" in prompt
    assert "no changed outfit" in prompt
    assert "no missing footwear" in prompt
    assert "no changed hairstyle" in prompt


def test_image_item_payload_records_reference_ids_and_urls():
    item = BatchImageItem(
        key="page_1",
        page_type="page",
        page_number=1,
        page_data={"characters_present": ["Mira"]},
        source_image_prompt="Mira smiles.",
        rendered_prompt="Rendered prompt.",
        aspect_ratio="1:1",
        image_size="1024x1024",
        file_name="page_1.png",
        text="",
    )
    references = [
        BatchImageReference("hero_child", "Mira", "hero_child", "/media/mira.png", SimpleNamespace(), 0),
    ]

    payload = StoryServiceBatchService._image_item_payload(item, reference_images=references)

    assert payload["reference_character_ids_used"] == ["hero_child"]
    assert payload["reference_image_urls_used"] == ["/media/mira.png"]


def test_story_text_only_image_prompt_uses_visual_bible_as_model_sheet():
    prompt = StoryServiceBatchService._story_text_only_image_prompt(
        "Rendered prompt with Visual Bible character locks.",
    )

    assert "No character reference image is attached" in prompt
    assert "Visual Bible inside the rendered prompt as the complete model sheet" in prompt
    assert "Respect basic scene etiquette" in prompt
    assert "do not draw outdoor shoes on feet" in prompt
    assert "This does not change the locked footwear design" in prompt
    assert "Rendered prompt with Visual Bible character locks." in prompt
