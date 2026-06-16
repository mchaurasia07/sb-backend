from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError

from app.entity.notification import NotificationAudience, NotificationDeliveryStatus
from app.model.request.notification import NotificationAsyncSendRequest
from app.routes.v1 import notifications as notification_routes
from app.service import notification_service as notification_service_module
from app.service.notification_service import NotificationService


class _Session:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


class _Notifications:
    def __init__(self, notification=None):
        self.created = None
        self.notification = notification
        self.update_count = 0

    async def create(self, **kwargs):
        self.created = SimpleNamespace(
            id=uuid4(),
            status=NotificationDeliveryStatus.PENDING,
            target_count=0,
            sent_count=0,
            failed_count=0,
            tickets=None,
            error_message=None,
            **kwargs,
        )
        return self.created

    async def get_by_id(self, notification_id):
        if self.notification and self.notification.id == notification_id:
            return self.notification
        return None

    async def update(self, notification):
        self.update_count += 1
        return notification


class _Tokens:
    def __init__(self, *, parent_tokens=None, child_tokens=None):
        self.parent_tokens = parent_tokens or []
        self.child_tokens = child_tokens or []
        self.deactivated = []

    async def active_for_parent_user(self, user_id):
        return self.parent_tokens

    async def active_for_child(self, child_id):
        return self.child_tokens

    async def active_for_parent_users(self, user_ids):
        return self.parent_tokens

    async def active_for_children(self, child_ids):
        return self.child_tokens

    async def active_for_audience(self, audience):
        return [*self.parent_tokens, *self.child_tokens]

    async def deactivate_token(self, expo_push_token, error=None):
        self.deactivated.append((expo_push_token, error))


class _ExpoPush:
    def __init__(self):
        self.messages = []

    async def send_messages(self, messages):
        self.messages.extend(messages)
        return [{"status": "ok"} for _ in messages]


def _token(value):
    return SimpleNamespace(expo_push_token=value, last_error=None)


def _async_payload(*, user_ids=None, child_ids=None):
    return NotificationAsyncSendRequest.model_validate(
        {
            "target": {
                "type": "custom",
                "user_ids": user_ids or [],
                "child_ids": child_ids or [],
            },
            "notification": {
                "title": "  New   story is ready  ",
                "body": " Tap   to read it now. ",
                "event_type": " manual_story_update ",
                "route": "story_detail",
                "fallback_route": "parent_dashboard",
                "params": {"story_id": str(uuid4())},
                "data": {"source": "admin_ui"},
            },
            "delivery": {
                "channel_id": "story-updates",
                "priority": "high",
                "sound": "default",
            },
        }
    )


def test_async_notification_target_validation_requires_required_ids():
    with pytest.raises(ValidationError):
        NotificationAsyncSendRequest.model_validate(
            {
                "target": {"type": "parent_user"},
                "notification": {"title": "Hi", "body": "Body"},
            }
        )

    with pytest.raises(ValidationError):
        NotificationAsyncSendRequest.model_validate(
            {
                "target": {"type": "child"},
                "notification": {"title": "Hi", "body": "Body"},
            }
        )

    with pytest.raises(ValidationError):
        NotificationAsyncSendRequest.model_validate(
            {
                "target": {"type": "custom"},
                "notification": {"title": "Hi", "body": "Body"},
            }
        )


@pytest.mark.asyncio
async def test_queue_manual_async_creates_pending_custom_notification():
    session = _Session()
    notifications = _Notifications()
    service = NotificationService(session)
    service.notifications = notifications

    user_id = uuid4()
    child_id = uuid4()
    response = await service.queue_manual_async(_async_payload(user_ids=[user_id], child_ids=[child_id]))

    created = notifications.created
    assert response.status == "pending"
    assert response.audience == "custom"
    assert response.target_count == 0
    assert created.audience == NotificationAudience.CUSTOM
    assert created.title == "New story is ready"
    assert created.body == "Tap to read it now."
    assert created.data["route"] == "story_detail"
    assert created.data["fallback_route"] == "parent_dashboard"
    assert created.data["screen"] == "story_detail"
    assert created.data["source"] == "admin_ui"
    assert created.data["params"]["story_id"] == created.data["story_id"]
    assert created.data["_target"]["user_ids"] == [str(user_id)]
    assert created.data["_target"]["child_ids"] == [str(child_id)]
    assert created.data["_delivery"]["channelId"] == "story-updates"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_deliver_queued_custom_notification_dedupes_and_sends_rich_expo_payload(monkeypatch):
    notification_id = uuid4()
    story_id = str(uuid4())
    notification = SimpleNamespace(
        id=notification_id,
        event_type="manual_story_update",
        audience=NotificationAudience.CUSTOM,
        title="Story ready",
        body="Tap to read.",
        data={
            "event_type": "manual_story_update",
            "route": "story_detail",
            "fallback_route": "parent_dashboard",
            "params": {"story_id": story_id},
            "story_id": story_id,
            "screen": "story_detail",
            "_target": {
                "type": "custom",
                "user_ids": [str(uuid4())],
                "child_ids": [str(uuid4())],
            },
            "_delivery": {
                "channelId": "story-updates",
                "priority": "high",
                "sound": "default",
            },
        },
        status=NotificationDeliveryStatus.PENDING,
        target_count=0,
        sent_count=0,
        failed_count=0,
        tickets=None,
        error_message=None,
    )
    expo = _ExpoPush()
    monkeypatch.setattr(notification_service_module, "expo_push_service", expo)

    session = _Session()
    service = NotificationService(session)
    service.notifications = _Notifications(notification)
    service.tokens = _Tokens(
        parent_tokens=[_token("ExpoPushToken[same]"), _token("ExpoPushToken[parent]")],
        child_tokens=[_token("ExpoPushToken[same]"), _token("ExpoPushToken[child]")],
    )

    response = await service.deliver_queued(notification_id)

    assert response.status == "sent"
    assert response.target_count == 3
    assert notification.sent_count == 3
    assert len(expo.messages) == 3
    message = expo.messages[0]
    assert message["title"] == "Story ready"
    assert message["body"] == "Tap to read."
    assert message["priority"] == "high"
    assert message["channelId"] == "story-updates"
    assert message["sound"] == "default"
    assert message["data"]["route"] == "story_detail"
    assert message["data"]["fallback_route"] == "parent_dashboard"
    assert message["data"]["params"]["story_id"] == story_id
    assert "_target" not in message["data"]
    assert "_delivery" not in message["data"]


@pytest.mark.asyncio
async def test_deliver_queued_without_tokens_is_skipped(monkeypatch):
    notification_id = uuid4()
    notification = SimpleNamespace(
        id=notification_id,
        event_type="manual",
        audience=NotificationAudience.PARENTS,
        title="Hi",
        body="Body",
        data={
            "event_type": "manual",
            "_target": {"type": "parents", "user_ids": [], "child_ids": []},
            "_delivery": {"channelId": "library-updates", "priority": "high", "sound": "default"},
        },
        status=NotificationDeliveryStatus.PENDING,
        target_count=0,
        sent_count=0,
        failed_count=0,
        tickets=None,
        error_message=None,
    )
    expo = _ExpoPush()
    monkeypatch.setattr(notification_service_module, "expo_push_service", expo)

    session = _Session()
    service = NotificationService(session)
    service.notifications = _Notifications(notification)
    service.tokens = _Tokens()

    response = await service.deliver_queued(notification_id)

    assert response.status == "skipped"
    assert response.target_count == 0
    assert expo.messages == []


@pytest.mark.asyncio
async def test_send_notification_async_route_queues_background_task(monkeypatch):
    notification_id = uuid4()
    response_data = SimpleNamespace(
        notification_id=notification_id,
        status="pending",
        audience="custom",
        target_count=0,
        sent_count=0,
        failed_count=0,
    )

    class _Service:
        def __init__(self, session):
            self.session = session

        async def queue_manual_async(self, payload):
            return response_data

    monkeypatch.setattr(notification_routes, "NotificationService", _Service)

    background_tasks = BackgroundTasks()
    response = await notification_routes.send_notification_async(
        _async_payload(user_ids=[uuid4()]),
        background_tasks,
        session=object(),
    )

    assert response.success is True
    assert response.data is response_data
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is notification_routes.send_queued_notification_background
    assert background_tasks.tasks[0].args == (notification_id,)
