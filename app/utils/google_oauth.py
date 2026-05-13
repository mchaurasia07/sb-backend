import httpx

from app.core.config import settings
from app.core.exceptions import AuthException


async def verify_google_id_token(id_token: str) -> dict[str, str | None]:
    """Validate a Google ID token using Google's tokeninfo endpoint."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
    if response.status_code != 200:
        raise AuthException("Invalid Google token", status_code=401, code="INVALID_GOOGLE_TOKEN")
    payload = response.json()
    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise AuthException("Google token audience mismatch", status_code=401, code="INVALID_GOOGLE_AUDIENCE")
    if not payload.get("email"):
        raise AuthException("Google account email is required", status_code=400, code="GOOGLE_EMAIL_REQUIRED")
    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
    }
