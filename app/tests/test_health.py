import os

from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "mysql+asyncmy://root:root@127.0.0.1:3306/storybook_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")

from app.main import create_app


def test_health_check() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swagger_alias_and_openapi_tags() -> None:
    client = TestClient(create_app())

    swagger_response = client.get("/swagger", follow_redirects=False)
    assert swagger_response.status_code in {307, 308}
    assert swagger_response.headers["location"] == "/docs"

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200

    tag_names = [tag["name"] for tag in openapi_response.json()["tags"]]
    assert tag_names == [
        "Auth",
        "Children",
        "Child Library",
        "Stories",
        "Narration",
        "Custom Stories",
        "Generic Stories",
        "Generic Audios",
        "Workflows",
        "Notifications",
        "Health",
    ]
