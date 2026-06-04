from __future__ import annotations

from typing import Any

from app.services.providers.anthropic import AnthropicAdapter
from app.services.providers.azure_openai import AzureOpenAIAdapter
from app.services.providers.base import ChatResult, ProviderAdapter
from app.services.providers.gemini import GeminiAdapter
from app.services.providers.openai import OpenAIAdapter

_CONNECTORS: dict[str, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "azure_openai": AzureOpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}

_CONNECTOR_PROTOCOLS: dict[str, str] = {
    "openai": "openai",
    "azure_openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
}

_CONNECTOR_KINDS: dict[str, set[str]] = {
    "openai": {"chat", "embedding", "image"},
    "azure_openai": {"chat", "embedding", "image"},
    "anthropic": {"chat"},
    "gemini": {"chat", "embedding", "image"},
}

_CONNECTOR_ALIASES: dict[str, str] = {
    "azure": "azure_openai",
    "azure-openai": "azure_openai",
    "openai-compatible": "openai",
}

_PROTOCOL_ALIASES: dict[str, str] = {
    "azure": "openai",
    "azure_openai": "openai",
    "azure-openai": "openai",
    "openai-compatible": "openai",
}

SUPPORTED_CHAT_PROTOCOLS = ["openai", "anthropic", "gemini"]
SUPPORTED_EMBEDDING_PROTOCOLS = ["openai", "gemini"]


def normalize_protocol(protocol: str | None) -> str:
    raw = (protocol or "openai").lower().strip()
    return _PROTOCOL_ALIASES.get(raw, raw)


def normalize_connector(connector_type: str | None) -> str:
    raw = (connector_type or "openai").lower().strip()
    return _CONNECTOR_ALIASES.get(raw, raw)


def get_connector_adapter(connector_type: str) -> ProviderAdapter | None:
    return _CONNECTORS.get(normalize_connector(connector_type))


def get_adapter(provider_type: str) -> ProviderAdapter | None:
    return get_connector_adapter(provider_type)


SUPPORTED_CONNECTORS = list(_CONNECTORS.keys())
SUPPORTED = SUPPORTED_CONNECTORS


def adapter_family(protocol: str | None) -> str:
    return normalize_protocol(protocol)


def channel_locks_adapter(provider_type: str | None) -> bool:
    return normalize_protocol(provider_type) != "openai"


def connector_protocol(connector_type: str | None) -> str:
    connector = normalize_connector(connector_type)
    return _CONNECTOR_PROTOCOLS.get(connector, normalize_protocol(connector))


def connector_supports_protocol(connector_type: str | None, protocol: str | None) -> bool:
    connector = normalize_connector(connector_type)
    return _CONNECTOR_PROTOCOLS.get(connector) == normalize_protocol(protocol)


def connector_supports_kind(connector_type: str | None, kind: str | None) -> bool:
    connector = normalize_connector(connector_type)
    return (kind or "chat") in _CONNECTOR_KINDS.get(connector, set())


def channel_connector(channel: Any) -> str:
    raw_provider = (getattr(channel, "provider_type", None) or "").lower().strip()
    raw_connector = getattr(channel, "connector_type", None)
    connector = normalize_connector(raw_connector)
    if raw_provider in {"azure", "azure_openai", "azure-openai"} and connector == "openai":
        return "azure_openai"
    return normalize_connector(raw_connector or raw_provider or "openai")


def channel_protocol(channel: Any) -> str:
    return normalize_protocol(getattr(channel, "provider_type", None) or connector_protocol(channel_connector(channel)))


def resolve_upstream_protocol(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    override = requested or getattr(model, "upstream_protocol", None) or None
    return normalize_protocol(override) if override else channel_protocol(channel)


def resolve_connector_type(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    if requested:
        return normalize_connector(requested)
    return channel_connector(channel)


def resolve_adapter_protocol(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    return resolve_connector_type(model, channel, requested)
