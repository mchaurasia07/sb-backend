"""Gemini Text-to-Speech integration for story narration."""

import asyncio
import base64
import io
import json
import logging
import wave
from typing import Tuple

import httpx

from app.core.config import settings
from app.core.exceptions import AppException
from app.utils.prompt_loader import load_and_render_prompt

logger = logging.getLogger(__name__)


class GoogleTTSProvider:
    """Wrapper for Gemini native TTS using the Gemini API key."""

    SAMPLE_RATE_HZ = 24000
    CHANNELS = 1
    SAMPLE_WIDTH_BYTES = 2
    API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    LANGUAGE_NAME_MAP = {
        "en": "Indian English",
        "hi": "Hindi",
        "mr": "Marathi",
    }
    LANGUAGE_CODE_MAP = {
        "en": "en-IN",
        "hi": "hi-IN",
        "mr": "mr-IN",
    }

    def __init__(self):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is required for Gemini TTS")

        self.api_key = settings.GOOGLE_API_KEY
        self.model = settings.GOOGLE_TTS_MODEL
        self.voice = settings.GOOGLE_TTS_VOICE
        logger.info("GoogleTTSProvider initialized with Gemini TTS model=%s voice=%s", self.model, self.voice)

    async def generate_narration_audio(
        self,
        text: str,
        pace: str = "medium",
        language: str = "en",
        voice_style: str = "storybook narrator",
        tone: str = "warm, magical, gentle",
        emotion: str = "wonder",
    ) -> Tuple[bytes, float]:
        """
        Generate narration audio for text using Gemini TTS.

        Gemini TTS returns raw PCM audio, so this method wraps the output in a
        WAV container before returning it.
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        audio_bytes, duration = await asyncio.to_thread(
            self._synthesize_speech,
            text,
            pace,
            language,
            voice_style,
            tone,
            emotion,
        )
        logger.info("Generated narration: duration=%.2fs, size=%s bytes", duration, len(audio_bytes))
        return audio_bytes, duration

    def _synthesize_speech(
        self,
        text: str,
        pace: str,
        language: str,
        voice_style: str,
        tone: str,
        emotion: str,
    ) -> Tuple[bytes, float]:
        prompt = self.build_prompt(
            text,
            pace=pace,
            language=language,
            voice_style=voice_style,
            tone=tone,
            emotion=emotion,
        )

        pcm_bytes = self._request_tts_audio(prompt)
        wav_bytes = self._pcm_to_wav(pcm_bytes)
        duration = self._estimate_duration(text, pace)
        logger.info("Gemini TTS synthesis complete: pcm=%s bytes, estimated %.2fs", len(pcm_bytes), duration)
        return wav_bytes, duration

    def build_prompt(
        self,
        text: str,
        *,
        pace: str = "medium",
        language: str = "en",
        voice_style: str = "storybook narrator",
        tone: str = "warm, magical, gentle",
        emotion: str = "wonder",
    ) -> str:
        normalized_language = language.lower()
        return load_and_render_prompt(
            "prompts/tts_narration_template.txt",
            {
                "language": self.LANGUAGE_NAME_MAP.get(normalized_language, language),
                "language_code": self.LANGUAGE_CODE_MAP.get(normalized_language, language),
                "voice": self.voice,
                "voice_style": voice_style,
                "tone": tone,
                "pace": pace,
                "emotion": emotion,
                "narration_text": text,
            },
        )

    def _request_tts_audio(self, prompt: str) -> bytes:
        model = self.model.removeprefix("models/")
        url = f"{self.API_BASE_URL}/models/{model}:generateContent"
        payload = {
            "model": model,
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": self.voice,
                        }
                    }
                },
            },
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        response = httpx.post(url, headers=headers, json=payload, timeout=120.0)
        if response.status_code >= 400:
            raise AppException(
                f"Gemini TTS request failed: {response.status_code} {response.text}",
                code="GEMINI_TTS_ERROR",
            )

        response_json = response.json()
        candidates = response_json.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data") or {}
            audio_data = inline_data.get("data")
            if audio_data:
                return base64.b64decode(audio_data)

        finish_reason = (candidates[0] or {}).get("finishReason") if candidates else None
        response_summary = self._summarize_response(response_json)
        logger.error("Gemini TTS returned no audio data: %s", response_summary)
        raise AppException(
            f"Gemini TTS returned no audio data. Finish reason: {finish_reason}. Response summary: {response_summary}",
            code="EMPTY_TTS_RESPONSE",
        )

    @staticmethod
    def _summarize_response(response_json: dict) -> str:
        candidates = response_json.get("candidates") or []
        summary = {
            "promptFeedback": response_json.get("promptFeedback"),
            "candidates": [],
        }

        for candidate in candidates[:2]:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            summary["candidates"].append(
                {
                    "finishReason": candidate.get("finishReason"),
                    "safetyRatings": candidate.get("safetyRatings"),
                    "parts": [
                        {
                            "keys": list(part.keys()),
                            "mimeType": (part.get("inlineData") or part.get("inline_data") or {}).get("mimeType"),
                            "hasData": bool((part.get("inlineData") or part.get("inline_data") or {}).get("data")),
                            "text": (part.get("text") or "")[:300],
                        }
                        for part in parts
                    ],
                }
            )

        return json.dumps(summary, ensure_ascii=False, default=str)[:2000]

    @classmethod
    def _pcm_to_wav(cls, pcm_bytes: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(cls.CHANNELS)
            wav_file.setsampwidth(cls.SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(cls.SAMPLE_RATE_HZ)
            wav_file.writeframes(pcm_bytes)
        return buffer.getvalue()

    @staticmethod
    def _estimate_duration(text: str, pace: str) -> float:
        pace_rate_map = {
            "slow": 0.85,
            "medium-slow": 0.95,
            "medium": 1.0,
            "fast": 1.1,
        }
        speaking_rate = pace_rate_map.get(pace, 1.0)
        word_count = len(text.split())
        return (word_count / 2.5) / speaking_rate
