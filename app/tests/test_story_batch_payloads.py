from app.service.story_service_batch_service import BatchImageItem, StoryServiceBatchService
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

    assert "Speak the story text only" in prompt
    assert "Do not add, remove, translate, summarize, or rewrite any words." in prompt
    assert "<<<NARRATION_TEXT" in prompt
    assert "Mira opened the moon map." in prompt


def test_story_reference_image_prompt_uses_rendered_prompt_identity_lock():
    prompt = StoryServiceBatchService._story_reference_image_prompt(
        "Scene prompt with Character Identity Lock.",
    )

    assert "Character Identity Lock inside the rendered prompt" in prompt
    assert "Scene prompt with Character Identity Lock." in prompt
    assert "only attached image" in prompt


def test_story_text_only_image_prompt_uses_visual_bible_as_model_sheet():
    prompt = StoryServiceBatchService._story_text_only_image_prompt(
        "Rendered prompt with Visual Bible character locks.",
    )

    assert "No character reference image is attached" in prompt
    assert "Visual Bible inside the rendered prompt as the complete model sheet" in prompt
    assert "Rendered prompt with Visual Bible character locks." in prompt
