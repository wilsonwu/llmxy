from __future__ import annotations

from typing import Any

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

_LOCKED_CHANNEL_PROTOCOLS = {"anthropic", "azure", "gemini"}


def channel_locks_adapter(provider_type: str | None) -> bool:
    return (provider_type or "").lower() in _LOCKED_CHANNEL_PROTOCOLS


def resolve_adapter_protocol(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    channel_protocol = (getattr(channel, "provider_type", None) or "openai").lower()
    override = (requested or getattr(model, "upstream_protocol", None) or "").lower()
    if channel_protocol in _LOCKED_CHANNEL_PROTOCOLS:
        return channel_protocol
    return override or channel_protocol
