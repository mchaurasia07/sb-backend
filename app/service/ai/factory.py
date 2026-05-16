from app.core.config import settings
from app.service.ai.base import AIProvider
from app.service.ai.openai_provider import OpenAIProvider
from app.service.ai.google_provider import GoogleProvider

_provider_instance: AIProvider | None = None


def get_ai_provider(provider_name: str | None = None) -> AIProvider:
    """Get configured AI provider instance (singleton pattern).

    Args:
        provider_name: Optional provider name override ("openai" or "google")
                      If not specified, uses AI_PROVIDER setting

    Returns:
        AIProvider: Configured provider instance

    Raises:
        RuntimeError: If no AI provider is configured
        ValueError: If invalid provider specified
    """
    global _provider_instance

    # Determine which provider to use
    selected_provider = provider_name or settings.AI_PROVIDER

    # If requesting same provider as current singleton, return it
    if _provider_instance is not None:
        current_provider = type(_provider_instance).__name__.lower().replace("provider", "")
        if current_provider == selected_provider.lower():
            return _provider_instance

    # Reset singleton when switching providers
    _provider_instance = None

    if selected_provider.lower() == "google":
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError(
                "Google provider selected but GOOGLE_API_KEY not configured. "
                "Please set GOOGLE_API_KEY in environment."
            )
        if not settings.CHARACTER_GENERATION_ENABLED:
            raise RuntimeError("CHARACTER_GENERATION_ENABLED must be True to use AI providers")

        _provider_instance = GoogleProvider(
            api_key=settings.GOOGLE_API_KEY,
            image_model=settings.GOOGLE_IMAGE_MODEL,
            text_model=settings.GOOGLE_TEXT_MODEL,
        )
        return _provider_instance

    elif selected_provider.lower() == "openai":
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OpenAI provider selected but OPENAI_API_KEY not configured. "
                "Please set OPENAI_API_KEY in environment."
            )
        if not settings.CHARACTER_GENERATION_ENABLED:
            raise RuntimeError("CHARACTER_GENERATION_ENABLED must be True to use AI providers")

        _provider_instance = OpenAIProvider(
            api_key=settings.OPENAI_API_KEY,
            image_model=settings.OPENAI_IMAGE_MODEL,
            text_model=settings.OPENAI_TEXT_MODEL,
        )
        return _provider_instance

    else:
        raise ValueError(
            f"Unknown AI provider: {selected_provider}. "
            "Supported providers: 'openai', 'google'"
        )
