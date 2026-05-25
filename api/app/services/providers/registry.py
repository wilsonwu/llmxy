from __future__ import annotations

from app.services.providers.anthropic import AnthropicAdapter
from app.services.providers.azure_openai import AzureOpenAIAdapter
from app.services.providers.base import ChatResult, ProviderAdapter
from app.services.providers.gemini import GeminiAdapter
from app.services.providers.openai import OpenAIAdapter

_REGISTRY: dict[str, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "azure": AzureOpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(provider_type: str) -> ProviderAdapter | None:
    return _REGISTRY.get((provider_type or "openai").lower())


SUPPORTED = list(_REGISTRY.keys())
