import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.entity.support import SupportMessageSender, SupportQueryStatus
from app.model.request.support import AddSupportMessageRequest, CreateSupportQueryRequest
from app.repository.base_repository import BaseRepository
from app.repository.support_repository import SupportRepository
from app.routes.v1.support import (
    add_jugni_reply,
    create_support_query,
    list_jugni_queries,
    router,
)
from app.service.support_service import SupportService


class _Session:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, _value):
        return None


class _SupportRepository:
    def __init__(self):
        self.query = None
        self.messages = []
        self.list_items = []
        self.total = 0
        self.flush_count = 0

    async def create_query(
        self, *, user_id, subject, pending_at_user, pending_at_jugni
    ):
        now = datetime.now(UTC)
        self.query = SimpleNamespace(
            query_id="QRY_1000123",
            user_id=user_id,
            subject=subject,
            status=SupportQueryStatus.OPEN,
            pending_at_user=pending_at_user,
            pending_at_jugni=pending_at_jugni,
            created_at=now,
            updated_at=now,
            closed_at=None,
            messages=[],
        )
        return self.query

    async def add_message(self, *, query, sender, message):
        item = SimpleNamespace(
            message_id=f"MSG{len(self.messages) + 1:03d}",
            sender=sender,
            message=message,
            created_at=datetime.now(UTC),
        )
        self.messages.append(item)
        query.messages.append(item)
        return item

    async def list_for_user(self, *, user_id, page, size):
        return self.list_items, self.total

    async def list_for_jugni(
        self,
        *,
        page,
        size,
        pending_at_jugni,
        pending_at_user,
        query_status,
    ):
        items = [
            item
            for item in self.list_items
            if (pending_at_jugni is None or item.pending_at_jugni == pending_at_jugni)
            and (pending_at_user is None or item.pending_at_user == pending_at_user)
            and (query_status is None or item.status == query_status)
        ]
        return items, len(items)

    async def get_for_user(self, *, user_id, query_id, include_messages=False):
        if self.query and self.query.user_id == user_id and self.query.query_id == query_id:
            return self.query
        return None

    async def get_by_query_id(self, query_id):
        if self.query and self.query.query_id == query_id:
            return self.query
        return None

    async def update(self, _query):
        self.flush_count += 1


def _service():
    session = _Session()
    service = SupportService(session)
    repository = _SupportRepository()
    service.support = repository
    return service, session, repository


@pytest.mark.asyncio
async def test_create_query_trims_fields_and_creates_initial_user_message():
    service, session, repository = _service()
    user_id = uuid4()

    response = await service.create_query(
        user_id=user_id,
        payload=CreateSupportQueryRequest(
            subject="  Unable to generate my story ",
            query_details="  Processing for ten hours. ",
        ),
    )

    assert response.query_id == "QRY_1000123"
    assert response.status == "OPEN"
    assert response.pending_at_jugni is True
    assert response.pending_at_user is False
    assert repository.query.subject == "Unable to generate my story"
    assert repository.messages[0].sender == SupportMessageSender.USER
    assert repository.messages[0].message == "Processing for ten hours."
    assert session.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"subject": " ", "query_details": "Details"}, "Subject is required."),
        ({"subject": "Subject", "query_details": ""}, "Query details are required."),
    ],
)
async def test_create_query_required_validation(payload, message):
    service, _, repository = _service()
    with pytest.raises(ValueError, match=message):
        await service.create_query(
            user_id=uuid4(), payload=CreateSupportQueryRequest(**payload)
        )
    assert repository.query is None


@pytest.mark.asyncio
async def test_query_detail_orders_messages_oldest_first():
    service, _, repository = _service()
    user_id = uuid4()
    await service.create_query(
        user_id=user_id,
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    later = datetime.now(UTC)
    repository.query.messages = [
        SimpleNamespace(
            message_id="MSG002",
            sender=SupportMessageSender.SUPPORT,
            message="Second",
            created_at=later,
        ),
        SimpleNamespace(
            message_id="MSG001",
            sender=SupportMessageSender.USER,
            message="First",
            created_at=later - timedelta(minutes=1),
        ),
    ]

    detail = await service.get_query(user_id=user_id, query_id="QRY_1000123")
    assert [item.message_id for item in detail.messages] == ["MSG001", "MSG002"]


@pytest.mark.asyncio
async def test_add_message_rejects_closed_query():
    service, _, repository = _service()
    user_id = uuid4()
    await service.create_query(
        user_id=user_id,
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    repository.query.status = SupportQueryStatus.CLOSED

    with pytest.raises(Exception) as exc_info:
        await service.add_message(
            user_id=user_id,
            query_id="QRY_1000123",
            payload=AddSupportMessageRequest(message="Another message"),
        )
    assert getattr(exc_info.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_user_message_sets_query_pending_at_jugni():
    service, _, repository = _service()
    user_id = uuid4()
    await service.create_query(
        user_id=user_id,
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    repository.query.pending_at_jugni = False
    repository.query.pending_at_user = True

    await service.add_message(
        user_id=user_id,
        query_id="QRY_1000123",
        payload=AddSupportMessageRequest(message="More information"),
    )

    assert repository.query.pending_at_jugni is True
    assert repository.query.pending_at_user is False


@pytest.mark.asyncio
async def test_jugni_reply_needs_no_user_id_and_marks_query_responded():
    service, session, repository = _service()
    await service.create_query(
        user_id=uuid4(),
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )

    response = await service.add_jugni_reply(
        query_id="QRY_1000123",
        payload=AddSupportMessageRequest(message="  I have fixed this for you.  "),
    )

    assert response.sender == "JUGNI"
    assert response.message == "I have fixed this for you."
    assert repository.query.status == SupportQueryStatus.RESPONDED
    assert repository.query.pending_at_jugni is False
    assert repository.query.pending_at_user is True
    assert session.commit_count == 2


@pytest.mark.asyncio
async def test_jugni_reply_rejects_closed_query():
    service, _, repository = _service()
    await service.create_query(
        user_id=uuid4(),
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    repository.query.status = SupportQueryStatus.CLOSED

    with pytest.raises(Exception) as exc_info:
        await service.add_jugni_reply(
            query_id="QRY_1000123",
            payload=AddSupportMessageRequest(message="Reply"),
        )
    assert getattr(exc_info.value, "status_code", None) == 409


def test_support_repository_extends_base_repository():
    assert issubclass(SupportRepository, BaseRepository)


def test_jugni_and_user_message_routes_are_registered():
    method_paths = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert ("PUT", "/jugni/queries/{query_id}/message") in method_paths
    assert ("POST", "/queries/{query_id}/message") in method_paths


def test_jugni_and_user_list_routes_are_registered():
    get_paths = {
        route.path
        for route in router.routes
        if "GET" in getattr(route, "methods", set())
    }
    assert {"/jugni/queries", "/queries"} <= get_paths


@pytest.mark.asyncio
async def test_jugni_list_filters_queries_and_returns_all_messages():
    service, _, repository = _service()
    await service.create_query(
        user_id=uuid4(),
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    repository.list_items = [repository.query]

    result = await service.list_jugni_queries(
        page=1,
        size=20,
        pending_at_jugni=True,
        pending_at_user=False,
        query_status=SupportQueryStatus.OPEN,
    )

    assert result.total_records == 1
    assert result.items[0].pending_at_jugni is True
    assert result.items[0].pending_at_user is False
    assert [message.message for message in result.items[0].messages] == ["First"]


@pytest.mark.asyncio
async def test_close_query_is_idempotent():
    service, session, repository = _service()
    user_id = uuid4()
    await service.create_query(
        user_id=user_id,
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    first = await service.close_query(user_id=user_id, query_id="QRY_1000123")
    second = await service.close_query(user_id=user_id, query_id="QRY_1000123")

    assert first.status == second.status == "CLOSED"
    assert first.closed_at == second.closed_at
    assert session.commit_count == 2  # create + first close only


@pytest.mark.asyncio
async def test_create_route_returns_exact_validation_error_contract():
    user_id = uuid4()
    user = SimpleNamespace(id=user_id)
    service, _, _ = _service()
    container = SimpleNamespace(support=service)

    response = await create_support_query(
        payload=CreateSupportQueryRequest(subject="", query_details="Details"),
        current_user=user,
        container=container,
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "success": False,
        "message": "Subject is required.",
    }


@pytest.mark.asyncio
async def test_jugni_reply_route_has_no_user_id_parameter():
    service, _, _ = _service()
    await service.create_query(
        user_id=uuid4(),
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    container = SimpleNamespace(support=service)

    response = await add_jugni_reply(
        query_id="QRY_1000123",
        payload=AddSupportMessageRequest(message="Reply"),
        current_user=SimpleNamespace(id=uuid4()),
        container=container,
    )

    assert response.message == "Jugni reply added successfully."
    assert response.data.sender == "JUGNI"


@pytest.mark.asyncio
async def test_jugni_list_route_passes_pending_and_status_filters():
    service, _, repository = _service()
    await service.create_query(
        user_id=uuid4(),
        payload=CreateSupportQueryRequest(subject="Subject", query_details="First"),
    )
    repository.list_items = [repository.query]
    container = SimpleNamespace(support=service)

    response = await list_jugni_queries(
        page=1,
        size=20,
        pending_at_jugni=True,
        pending_at_user=False,
        query_status=SupportQueryStatus.OPEN,
        current_user=SimpleNamespace(id=uuid4()),
        container=container,
    )

    assert response.data.total_records == 1
    assert len(response.data.items[0].messages) == 1
