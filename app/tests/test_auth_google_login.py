from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.exceptions import AuthException
from app.model.request.auth import GoogleLoginRequest, LoginRequest
from app.routes.v1.auth import AuthRouter
from app.service.auth_service import AuthService
from app.utils import google_oauth


class _FakeUsers:
    def __init__(self, *, google_user=None, email_user=None):
        self.google_user = google_user
        self.email_user = email_user
        self.created_user = None

    async def get_by_google_sub(self, google_sub):
        if self.google_user is not None and self.google_user.google_sub == google_sub:
            return self.google_user
        return None

    async def get_by_email(self, email):
        if self.email_user is not None and self.email_user.email == email.lower():
            return self.email_user
        return None

    async def create_google(self, email, google_sub, first_name, last_name):
        self.created_user = _user(
            email=email,
            google_sub=google_sub,
            first_name=first_name,
            last_name=last_name,
            phone=None,
        )
        return self.created_user


class _FakeChildren:
    def __init__(self, exists):
        self.exists = exists

    async def exists_for_user(self, user_id):
        return self.exists


class _FakeRefreshTokens:
    async def create(self, user_id, token_hash, expires_at):
        return None


class _FakeSession:
    def __init__(self):
        self.flush_called = False

    async def flush(self):
        self.flush_called = True


def _user(
    *,
    email="parent@example.com",
    google_sub="google-sub",
    first_name="Parent",
    last_name="User",
    phone="+919876543210",
):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        email=email,
        phone=phone,
        first_name=first_name,
        last_name=last_name,
        google_sub=google_sub,
        is_email_verified=True,
        is_phone_verified=False,
        created_at=now,
        updated_at=now,
    )


def _service(*, google_user=None, email_user=None, child_exists=False):
    service = AuthService.__new__(AuthService)
    service.session = _FakeSession()
    service.users = _FakeUsers(google_user=google_user, email_user=email_user)
    service.children = _FakeChildren(child_exists)
    service.refresh_tokens = _FakeRefreshTokens()
    return service


async def _google_payload(_id_token):
    return {
        "sub": "google-sub",
        "email": "parent@example.com",
        "name": "Parent User",
    }


@pytest.mark.asyncio
async def test_google_login_new_user_returns_first_time_and_add_phone(monkeypatch):
    monkeypatch.setattr("app.service.auth_service.verify_google_id_token", _google_payload)
    service = _service()

    response = await service.google_login(GoogleLoginRequest(id_token="valid-google-id-token"))

    assert response.first_time_login is True
    assert response.phone_required is True
    assert response.redirect_to == "add_phone"
    assert response.user.email == "parent@example.com"
    assert response.user.phone is None


@pytest.mark.asyncio
async def test_google_login_existing_google_user_without_child_goes_to_create_child(monkeypatch):
    monkeypatch.setattr("app.service.auth_service.verify_google_id_token", _google_payload)
    service = _service(google_user=_user(), child_exists=False)

    response = await service.google_login(GoogleLoginRequest(id_token="valid-google-id-token"))

    assert response.first_time_login is False
    assert response.phone_required is False
    assert response.redirect_to == "create_child_profile"


@pytest.mark.asyncio
async def test_google_login_existing_google_user_with_child_goes_to_dashboard(monkeypatch):
    monkeypatch.setattr("app.service.auth_service.verify_google_id_token", _google_payload)
    service = _service(google_user=_user(), child_exists=True)

    response = await service.google_login(GoogleLoginRequest(id_token="valid-google-id-token"))

    assert response.first_time_login is False
    assert response.phone_required is False
    assert response.redirect_to == "dashboard"


@pytest.mark.asyncio
async def test_google_login_links_existing_email_user_without_first_time(monkeypatch):
    monkeypatch.setattr("app.service.auth_service.verify_google_id_token", _google_payload)
    existing_user = _user(google_sub=None, phone=None)
    service = _service(email_user=existing_user)

    response = await service.google_login(GoogleLoginRequest(id_token="valid-google-id-token"))

    assert response.first_time_login is False
    assert response.phone_required is True
    assert response.redirect_to == "add_phone"
    assert existing_user.google_sub == "google-sub"
    assert existing_user.is_email_verified is True
    assert service.session.flush_called is True


class _FakeGoogleResponse:
    status_code = 200

    @staticmethod
    def json():
        return {
            "aud": "android-client-id",
            "sub": "google-sub",
            "email": "parent@example.com",
            "name": "Parent User",
        }


class _FakeGoogleClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, params):
        return _FakeGoogleResponse()


@pytest.mark.asyncio
async def test_verify_google_id_token_accepts_allowed_mobile_audience(monkeypatch):
    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _FakeGoogleClient)
    monkeypatch.setattr(google_oauth.settings, "google_allowed_client_ids", {"web-client-id", "android-client-id"})

    payload = await google_oauth.verify_google_id_token("valid-google-id-token")

    assert payload["email"] == "parent@example.com"


@pytest.mark.asyncio
async def test_verify_google_id_token_rejects_unconfigured_audience(monkeypatch):
    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _FakeGoogleClient)
    monkeypatch.setattr(google_oauth.settings, "google_allowed_client_ids", {"web-client-id"})

    with pytest.raises(AuthException, match="audience mismatch"):
        await google_oauth.verify_google_id_token("valid-google-id-token")


@pytest.mark.asyncio
async def test_rate_limited_auth_route_accepts_bound_request_kwargs():
    router = AuthRouter()
    endpoint = next(route.endpoint for route in router.router.routes if getattr(route, "path", "") == "/login")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    payload = LoginRequest(identifier="parent@example.com", password="secret")
    calls = {}

    class _Auth:
        async def login(self, payload_arg):
            calls["payload"] = payload_arg
            return SimpleNamespace(token="ok")

    response = await endpoint(request=request, payload=payload, container=SimpleNamespace(auth=_Auth()))

    assert calls["payload"] is payload
    assert response.message == "Login successful"
