import httpx

from app.core.config import settings
from app.core.exceptions import AuthException
from app.core.logger import get_logger

logger = get_logger(__name__)


async def verify_google_id_token(id_token: str) -> dict[str, str | None]:
    """Validate a Google ID token using Google's tokeninfo endpoint."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token})
    if response.status_code != 200:
        google_error = _response_error(response)
        logger.warning(
            "google_tokeninfo_rejected",
            status_code=response.status_code,
            google_error=google_error,
        )
        raise AuthException("Invalid Google token", status_code=401, code="INVALID_GOOGLE_TOKEN")
    payload = response.json()
    audience = payload.get("aud")
    if audience not in settings.google_allowed_client_ids:
        logger.warning(
            "google_token_audience_mismatch",
            audience=audience,
            allowed_client_ids=sorted(settings.google_allowed_client_ids),
            email=payload.get("email"),
        )
        raise AuthException("Google token audience mismatch", status_code=401, code="INVALID_GOOGLE_AUDIENCE")
    if not payload.get("email"):
        logger.warning("google_token_missing_email", audience=audience, subject=payload.get("sub"))
        raise AuthException("Google account email is required", status_code=400, code="GOOGLE_EMAIL_REQUIRED")
    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
    }


def _response_error(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or None
    if isinstance(payload, dict):
        return payload.get("error_description") or payload.get("error")
    return None
