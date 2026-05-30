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
