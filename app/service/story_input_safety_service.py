from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import status
from google.genai import types

from app.core.config import settings
from app.core.exceptions import AppException
from app.service.ai.factory import get_ai_provider
from app.utils.prompt_loader import load_and_render_prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoryInputSafetyResult:
    safe: bool
    risk_level: str
    blocked_categories: list[str]
    reason: str
    safe_rewrite: str | None = None


@dataclass(frozen=True)
class StoryInputSafetyInspection:
    request_json: dict[str, Any]
    prompt: str
    provider: str
    model: str | None
    result: StoryInputSafetyResult | None
    response_text: str | None = None
    response_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class StoryInputSafetyService:
    """Validates parent-provided story ideas before any story generation begins."""

    PROMPT_PATH = "prompts/story/input_safety_validation_prompt.txt"

    LOCAL_BLOCK_PATTERNS: tuple[tuple[str, str], ...] = (
        ("PROMPT_INJECTION", r"\b(ignore|disregard|forget|override|bypass)\s+(all\s+)?(previous\s+)?(instruction|prompt|rule|guideline|safety|filter)s?\b"),
        ("PROMPT_INJECTION", r"\b(reveal|show|display|print|output|leak|expose)\s+(the\s+)?(system|hidden|original|developer|internal)\s+(prompt|message|instruction|rule)s?\b"),
        ("PROMPT_INJECTION", r"\b(act\s+as|pretend\s+to\s+be|roleplay\s+as|you\s+are\s+now)\s+(an?\s+)?(DAN|evil|uncensored|unfiltered|unrestricted)\b"),
        ("PROMPT_INJECTION", r"\b(jailbreak|jailbroken|bypass\s+safety|disable\s+filter|turn\s+off\s+safety|no\s+restrictions|developer\s+mode)\b"),
        ("PROMPT_INJECTION", r"\bapi[_\s-]?key|access[_\s-]?token|secret[_\s-]?key|password[_\s-]?(hash|leak)\b"),
        ("SEXUAL_CONTENT", r"\b(sex|sexual|porn|pornography|xxx|hentai|masturbat|orgasm)\b"),
        ("SEXUAL_CONTENT", r"\b(nude|naked|nudity|strip|undress|expose[ds]?\s+bod(y|ies))\b"),
        ("SEXUAL_CONTENT", r"\b(erotic|fetish|bdsm|kinky|foreplay|intercourse)\b"),
        ("SEXUAL_CONTENT", r"\b(molest|groom|seduc|rape|sexual\s+assault|sexual\s+abuse|pedophil)\w*\b"),
        ("SEXUAL_CONTENT", r"\b(penis|vagina|breast|nipple|genital|privates)(?!\s+(exam|doctor|hospital))\b"),
        ("SEXUAL_CONTENT", r"\b(child\s+)?(sexy|hot\s+bod|attracted\s+to|crush\s+on).{0,30}(adult|grown[- ]?up|teacher|coach)\b"),
        ("GRAPHIC_VIOLENCE", r"\b(gore|gory|gorey|viscera|entrail|disembowel)\w*\b"),
        ("GRAPHIC_VIOLENCE", r"\b(decapitat|behead|dismember|mutilat|eviscer)\w*\b"),
        ("GRAPHIC_VIOLENCE", r"\b(torture|torment|inflict\s+pain|cruel\s+punishment)\b"),
        ("GRAPHIC_VIOLENCE", r"\b(massacre|slaughter|bloodbath|carnage|genocide)\b"),
        ("GRAPHIC_VIOLENCE", r"\b(stab|stabbing|knife|slash).{0,20}(repeatedly|blood|wound|kill)\b"),
        ("GRAPHIC_VIOLENCE", r"\b(shot|shoot|gun|bullet).{0,20}(head|brain|skull|execution)\b"),
        ("SELF_HARM", r"\b(suicid|kill\s+(my)?self|end\s+(my\s+)?(own\s+)?life|take\s+my\s+life)\w*\b"),
        ("SELF_HARM", r"\b(self[- ]?harm|self[- ]?injur|cut\s+(my)?self|hurt\s+(my)?self)\b"),
        ("SELF_HARM", r"\b(hang\s+(my)?self|slit\s+(my\s+)?(wrist|throat)|overdose|jump\s+off)\b"),
        ("SELF_HARM", r"\b(want\s+to\s+die|wish\s+I\s+was\s+dead|better\s+off\s+dead)\b"),
        ("DANGEROUS_CONTENT", r"\b(make|build|create|construct|assemble).{0,30}(bomb|explosive|device|ied)\b"),
        ("DANGEROUS_CONTENT", r"\b(poison|toxin|venom).{0,30}(recipe|how\s+to|instructions|make)\b"),
        ("DANGEROUS_CONTENT", r"\b(drug|meth|cocaine|heroin).{0,30}(cook|recipe|synthesize|manufacture)\b"),
        ("DANGEROUS_CONTENT", r"\b(weapon|firearm|gun).{0,30}(build|instructions|blueprint|diy)\b"),
        ("DANGEROUS_CONTENT", r"\b(arson|fire\s+starting|molotov|burn\s+down)\b"),
        ("HATE_OR_HARASSMENT", r"\b(nazi|hitler|white\s+supremac|kkk|swastika)\w*\b"),
        ("HATE_OR_HARASSMENT", r"\b(n[i1]gg[e3]r|f[a@]gg[o0]t|k[i1]k[e3]|sp[i1]c|ch[i1]nk|r[e3]t[a@]rd)\w*\b"),
        ("HATE_OR_HARASSMENT", r"\b(kill\s+all|death\s+to|eliminate\s+all).{0,20}(jews|muslims|blacks|gays|women)\b"),
        ("HATE_OR_HARASSMENT", r"\b(hate|despise|inferior).{0,30}(race|ethnicity|religion|gender|orientation)\b"),
        ("HATE_OR_HARASSMENT", r"\b(holocaust\s+denial|holocaust\s+hoax|slavery\s+was\s+good)\b"),
        ("ADULT_THEME", r"\b(drugs?|cocaine|heroin|meth|marijuana).{0,30}(use|abuse|addict|high|dealer)\b"),
        ("ADULT_THEME", r"\b(alcohol|beer|wine|vodka|drunk).{0,30}(abuse|addict|binge|intoxicat)\w*\b"),
        ("ADULT_THEME", r"\b(gambling|casino|bet|poker).{0,30}(addict|lose\s+money|debt)\b"),
        ("ADULT_THEME", r"\b(strip\s+club|brothel|prostitut|escort\s+service)\w*\b"),
    )

    BLOCKED_CATEGORIES = {
        "SEXUAL_CONTENT",
        "GRAPHIC_VIOLENCE",
        "SELF_HARM",
        "DANGEROUS_CONTENT",
        "HATE_OR_HARASSMENT",
        "PROMPT_INJECTION",
        "PRIVACY_OR_PERSONAL_DATA",
        "ADULT_THEME",
    }

    async def validate(self, payload: Any) -> StoryInputSafetyResult:
        inspection = await self.inspect(payload)
        if inspection.error_message:
            raise AppException(
                inspection.error_message,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                inspection.error_code or "STORY_INPUT_SAFETY_UNAVAILABLE",
            )
        if inspection.result is None:
            raise AppException(
                "Story safety validation returned an unexpected response. Please try again.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "STORY_INPUT_SAFETY_UNAVAILABLE",
            )
        if not inspection.result.safe:
            self._raise_unsafe(inspection.result)
        return inspection.result

    async def inspect(self, payload: Any) -> StoryInputSafetyInspection:
        request_json = self._request_snapshot(payload)
        idea_json = self._story_idea_json(payload)
        prompt = self._classification_prompt(idea_json)

        local_categories = self._local_block_categories(self._story_idea_text(payload))
        if local_categories:
            result = StoryInputSafetyResult(
                safe=False,
                risk_level="HIGH",
                blocked_categories=local_categories,
                reason="The story idea includes content that is not appropriate for children.",
            )
            response_json = self._result_to_response_json(result)
            return StoryInputSafetyInspection(
                request_json=request_json,
                prompt=prompt,
                provider="local",
                model="local-regex",
                result=result,
                response_text=json.dumps(response_json, ensure_ascii=False),
                response_json=response_json,
            )

        provider = get_ai_provider("google")
        provider_name = "google"
        model = getattr(provider, "text_model", None)

        try:
            response = await provider.generate_text(
                prompt,
                max_tokens=1000,
                temperature=0,
                step_name="INPUT_SAFETY_VALIDATION",
                response_format={"type": "json_object"},
                empty_response_retries=1,
                safety_settings=self._strict_safety_settings(),
            )
        except AppException as exc:
            logger.exception("Story input safety validation failed")
            return StoryInputSafetyInspection(
                request_json=request_json,
                prompt=prompt,
                provider=provider_name,
                model=str(model) if model else None,
                result=None,
                error_code=exc.code or "STORY_INPUT_SAFETY_UNAVAILABLE",
                error_message=exc.message,
            )
        except Exception:
            logger.exception("Story input safety validation failed")
            return StoryInputSafetyInspection(
                request_json=request_json,
                prompt=prompt,
                provider=provider_name,
                model=str(model) if model else None,
                result=None,
                error_code="STORY_INPUT_SAFETY_UNAVAILABLE",
                error_message="Story safety validation is temporarily unavailable. Please try again.",
            )

        try:
            response_json = json.loads(response.text)
        except json.JSONDecodeError:
            logger.warning("Safety classifier returned invalid JSON: %s", response.text[:1000])
            return StoryInputSafetyInspection(
                request_json=request_json,
                prompt=prompt,
                provider=provider_name,
                model=str(model) if model else None,
                result=None,
                response_text=response.text,
                error_code="STORY_INPUT_SAFETY_UNAVAILABLE",
                error_message="Story safety validation returned an invalid response. Please try again.",
            )

        result = self._parse_result(response_json)
        if result is None:
            return StoryInputSafetyInspection(
                request_json=request_json,
                prompt=prompt,
                provider=provider_name,
                model=str(model) if model else None,
                result=None,
                response_text=response.text,
                response_json=response_json,
                error_code="STORY_INPUT_SAFETY_UNAVAILABLE",
                error_message="Story safety validation returned an invalid response. Please try again.",
            )

        return StoryInputSafetyInspection(
            request_json=request_json,
            prompt=prompt,
            provider=provider_name,
            model=str(model) if model else None,
            result=result,
            response_text=response.text,
            response_json=response_json,
        )

    @classmethod
    def _local_block_categories(cls, text: str) -> list[str]:
        categories: list[str] = []
        for category, pattern in cls.LOCAL_BLOCK_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                categories.append(category)
        return sorted(set(categories))

    @staticmethod
    def _request_snapshot(payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json")
        if isinstance(payload, dict):
            return dict(payload)
        return {
            key: value
            for key, value in vars(payload).items()
            if not key.startswith("_")
        }

    @staticmethod
    def _story_idea_json(payload: Any) -> dict[str, str]:
        return {
            "category": getattr(payload, "category", None) or "",
            "learning_goal": getattr(payload, "learning_goal", None) or "",
            "context": getattr(payload, "context", None) or "",
        }

    @staticmethod
    def _story_idea_text(payload: Any) -> str:
        return json.dumps(StoryInputSafetyService._story_idea_json(payload), ensure_ascii=False)

    @staticmethod
    def _strict_safety_settings() -> list[types.SafetySetting]:
        return [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
            ),
        ]

    @staticmethod
    def _classification_prompt(idea_json: dict[str, str]) -> str:
        return load_and_render_prompt(
            StoryInputSafetyService.PROMPT_PATH,
            {"story_idea_json": idea_json},
        ).strip()

    @staticmethod
    def _parse_result(payload: dict[str, Any]) -> StoryInputSafetyResult | None:
        if not isinstance(payload, dict):
            return None

        safe = bool(payload.get("safe"))
        risk_level = str(payload.get("risk_level") or "UNKNOWN").upper()
        blocked_categories = [
            str(item).upper()
            for item in (payload.get("blocked_categories") or [])
            if str(item).strip()
        ]
        if any(category in StoryInputSafetyService.BLOCKED_CATEGORIES for category in blocked_categories):
            safe = False

        reason = str(payload.get("reason") or "").strip()
        safe_rewrite = payload.get("safe_rewrite")
        return StoryInputSafetyResult(
            safe=safe,
            risk_level=risk_level,
            blocked_categories=blocked_categories,
            reason=reason or "The story idea was reviewed for child safety.",
            safe_rewrite=str(safe_rewrite).strip() if safe_rewrite else None,
        )

    @staticmethod
    def _result_to_response_json(result: StoryInputSafetyResult) -> dict[str, Any]:
        return {
            "safe": result.safe,
            "risk_level": result.risk_level,
            "blocked_categories": result.blocked_categories,
            "reason": result.reason,
            "safe_rewrite": result.safe_rewrite,
        }

    @staticmethod
    def _raise_unsafe(result: StoryInputSafetyResult) -> None:
        raise AppException(
            "Story idea is not safe for children. Please try a different idea.",
            status.HTTP_400_BAD_REQUEST,
            "STORY_INPUT_UNSAFE",
            details={
                "risk_level": result.risk_level,
                "blocked_categories": result.blocked_categories,
                "reason": result.reason,
                "safe_rewrite": result.safe_rewrite,
            },
        )
