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

_ADAPTER_FAMILY: dict[str, str] = {
    "openai": "openai",
    "azure": "azure",
    "anthropic": "anthropic",
    "gemini": "gemini",
}


def get_adapter(provider_type: str) -> ProviderAdapter | None:
    return _REGISTRY.get((provider_type or "openai").lower())


SUPPORTED = list(_REGISTRY.keys())
SUPPORTED_CHAT_PROTOCOLS = list(_REGISTRY.keys())
SUPPORTED_EMBEDDING_PROTOCOLS = ["openai", "azure", "gemini"]

_LOCKED_CHANNEL_FAMILIES = {"anthropic", "azure", "gemini"}


def adapter_family(protocol: str | None) -> str:
    return _ADAPTER_FAMILY.get((protocol or "openai").lower(), (protocol or "openai").lower())


def channel_locks_adapter(provider_type: str | None) -> bool:
    return adapter_family(provider_type) in _LOCKED_CHANNEL_FAMILIES


def resolve_adapter_protocol(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    channel_protocol = (getattr(channel, "provider_type", None) or "openai").lower()
    override = (requested or getattr(model, "upstream_protocol", None) or "").lower()
    if channel_locks_adapter(channel_protocol):
        if override and adapter_family(override) == adapter_family(channel_protocol):
            return override
        return channel_protocol
    return override or channel_protocol
