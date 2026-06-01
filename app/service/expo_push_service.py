import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


class ExpoPushService:
    """Small HTTP client for Expo Push Service."""

    PUSH_SEND_URL = "https://exp.host/--/api/v2/push/send"
    MAX_MESSAGES_PER_REQUEST = 100

    async def send_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return []

        tickets: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for chunk in self._chunks(messages, self.MAX_MESSAGES_PER_REQUEST):
                tickets.extend(await self._send_chunk(client, chunk))
        return tickets

    async def _send_chunk(self, client: httpx.AsyncClient, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
        }
        access_token = settings.EXPO_PUSH_ACCESS_TOKEN.strip()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.post(self.PUSH_SEND_URL, headers=headers, json=messages)
                if response.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2**attempt)
                    continue
                if response.status_code >= 400:
                    raise AppException(
                        f"Expo push request failed: {response.status_code} {response.text}",
                        code="EXPO_PUSH_REQUEST_FAILED",
                    )

                payload = response.json()
                if payload.get("errors"):
                    logger.warning("expo_push_response_errors: %s", payload.get("errors"))
                data = payload.get("data") or []
                return data if isinstance(data, list) else [data]
            except (httpx.HTTPError, AppException) as exc:
                last_error = exc
                await asyncio.sleep(2**attempt)

        raise AppException(f"Expo push request failed after retries: {last_error}", code="EXPO_PUSH_FAILED")

    @staticmethod
    def is_expo_push_token(value: str) -> bool:
        return value.startswith("ExpoPushToken[") or value.startswith("ExponentPushToken[")

    @staticmethod
    def _chunks(values: list[dict[str, Any]], size: int):
        for index in range(0, len(values), size):
            yield values[index : index + size]


expo_push_service = ExpoPushService()
