from types import SimpleNamespace
from datetime import date, datetime
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundException
from app.model.request.child import ChildProfileUpdateRequest
from app.service.child_service import ChildService


class _FakeChildren:
    def __init__(self, child):
        self.child = child
        self.deleted = None

    async def get_for_user(self, user_id, child_id):
        if self.child and self.child.user_id == user_id and self.child.id == child_id:
            return self.child
        return None

    async def delete(self, child):
        self.deleted = child

    async def update(self, child):
        return child


class _FakeStories:
    def __init__(self, stories):
        self.stories = stories

    async def list_by_user(self, user_id, child_id=None):
        return [
            story
            for story in self.stories
            if story.user_id == user_id and (child_id is None or story.child_id == child_id)
        ]


class _FakeImageStorage:
    def __init__(self):
        self.deleted_story_ids = []
        self.deleted_child_dirs = []
        self.saved_profile_photos = []

    async def delete_story_directory(self, story_id):
        self.deleted_story_ids.append(story_id)

    async def delete_child_profile_directory(self, parent_id, child_id):
        self.deleted_child_dirs.append((parent_id, child_id))

    async def save_child_profile_photo(self, parent_id, child_id, photo, public_base_url):
        self.saved_profile_photos.append((parent_id, child_id, photo, public_base_url))
        return f"{public_base_url}/media/{parent_id}/{child_id}/profile.png"


class _FakeAudioStorage:
    def __init__(self):
        self.deleted_story_ids = []

    async def delete_story_directory(self, story_id):
        self.deleted_story_ids.append(story_id)


@pytest.mark.asyncio
async def test_delete_child_profile_deletes_story_media_child_media_and_db(monkeypatch):
    user_id = uuid4()
    child_id = uuid4()
    story_ids = [uuid4(), uuid4()]
    user = SimpleNamespace(id=user_id, active_child_profile_id=child_id)
    child = SimpleNamespace(id=child_id, user_id=user_id)
    stories = [
        SimpleNamespace(id=story_ids[0], user_id=user_id, child_id=child_id),
        SimpleNamespace(id=story_ids[1], user_id=user_id, child_id=child_id),
        SimpleNamespace(id=uuid4(), user_id=user_id, child_id=uuid4()),
    ]
    image_storage = _FakeImageStorage()
    audio_storage = _FakeAudioStorage()
    service = ChildService.__new__(ChildService)
    service.children = _FakeChildren(child)
    service.stories = _FakeStories(stories)

    monkeypatch.setattr("app.service.child_service.get_image_storage_service", lambda: image_storage)
    monkeypatch.setattr("app.service.child_service.get_story_audio_storage_service", lambda: audio_storage)

    await service.delete(user, child_id)

    assert image_storage.deleted_story_ids == story_ids
    assert audio_storage.deleted_story_ids == story_ids
    assert image_storage.deleted_child_dirs == [(user_id, child_id)]
    assert service.children.deleted is child
    assert user.active_child_profile_id is None


@pytest.mark.asyncio
async def test_delete_child_profile_requires_owned_child():
    service = ChildService.__new__(ChildService)
    service.children = _FakeChildren(None)
    service.stories = _FakeStories([])

    with pytest.raises(NotFoundException):
        await service.delete(SimpleNamespace(id=uuid4(), active_child_profile_id=None), uuid4())


@pytest.mark.asyncio
async def test_update_child_profile_updates_form_fields_without_photo():
    user_id = uuid4()
    child_id = uuid4()
    child = SimpleNamespace(
        id=child_id,
        user_id=user_id,
        first_name="Mira",
        last_name="Old",
        dob=date(2018, 1, 1),
        age=6,
        gender="girl",
        avatar_image_url="old-profile.png",
        character_image_url="old-character.png",
        character_metadata={"identity_summary": "old"},
        child_user_id="mira_01",
        child_password="01012018",
        active=True,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 2),
    )
    service = ChildService.__new__(ChildService)
    service.children = _FakeChildren(child)

    result = await service.update(
        SimpleNamespace(id=user_id),
        child_id,
        ChildProfileUpdateRequest(first_name="Mira", last_name="New", age=7),
    )

    assert result.last_name == "New"
    assert result.age == 7
    assert child.avatar_image_url == "old-profile.png"
    assert child.character_image_url == "old-character.png"
    assert child.character_metadata == {"identity_summary": "old"}


@pytest.mark.asyncio
async def test_update_child_profile_photo_saves_upload_and_preserves_character(monkeypatch):
    user_id = uuid4()
    child_id = uuid4()
    child = SimpleNamespace(
        id=child_id,
        user_id=user_id,
        first_name="Mira",
        last_name="Patel",
        dob=date(2018, 1, 1),
        age=6,
        gender="girl",
        avatar_image_url="old-profile.png",
        character_image_url="old-character.png",
        character_metadata={"identity_summary": "old"},
        child_user_id="mira_01",
        child_password="01012018",
        active=True,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 2),
    )
    image_storage = _FakeImageStorage()
    service = ChildService.__new__(ChildService)
    service.children = _FakeChildren(child)

    monkeypatch.setattr("app.service.child_service.get_image_storage_service", lambda: image_storage)

    photo = SimpleNamespace(filename="profile.png", content_type="image/png")
    result = await service.update(
        SimpleNamespace(id=user_id),
        child_id,
        ChildProfileUpdateRequest(first_name="Mira"),
        photo=photo,
        public_base_url="https://api.example.test",
    )

    assert image_storage.saved_profile_photos == [(user_id, child_id, photo, "https://api.example.test")]
    assert result.avatar_image_url.endswith(f"/media/{user_id}/{child_id}/profile.png")
    assert child.character_image_url == "old-character.png"
    assert child.character_metadata == {"identity_summary": "old"}
