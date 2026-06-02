from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundException
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

    async def delete_story_directory(self, story_id):
        self.deleted_story_ids.append(story_id)

    async def delete_child_profile_directory(self, parent_id, child_id):
        self.deleted_child_dirs.append((parent_id, child_id))


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
