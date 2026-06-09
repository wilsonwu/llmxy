from __future__ import annotations

from typing import Any

from app.services.providers.anthropic import AnthropicAdapter
from app.services.providers.azure_openai import AzureOpenAIAdapter
from app.services.providers.base import ChatResult, ProviderAdapter
from app.services.providers.gemini import GeminiAdapter
from app.services.providers.openai import OpenAIAdapter
from app.services.protocols.ids import (
    ANTHROPIC_MESSAGES,
    CHAT_PROTOCOLS,
    EMBEDDING_PROTOCOLS,
    GEMINI_EMBEDDINGS,
    GEMINI_GENERATE_CONTENT,
    GEMINI_IMAGES,
    OPENAI_CHAT,
    OPENAI_EMBEDDINGS,
    OPENAI_IMAGES,
    OPENAI_RESPONSES,
    normalize_protocol,
    protocol_for_kind,
)

_CONNECTORS: dict[str, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "azure_openai": AzureOpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}

_CONNECTOR_PROTOCOLS: dict[str, set[str]] = {
    "openai": {OPENAI_CHAT, OPENAI_RESPONSES, OPENAI_EMBEDDINGS, OPENAI_IMAGES},
    "azure_openai": {OPENAI_CHAT, OPENAI_RESPONSES, OPENAI_EMBEDDINGS, OPENAI_IMAGES},
    "anthropic": {ANTHROPIC_MESSAGES},
    "gemini": {GEMINI_GENERATE_CONTENT, GEMINI_EMBEDDINGS, GEMINI_IMAGES},
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
    "openai.chat": "openai",
    "openai.responses": "openai",
    "openai.embeddings": "openai",
    "openai.images": "openai",
    "anthropic.messages": "anthropic",
    "gemini.generate_content": "gemini",
    "gemini.embeddings": "gemini",
    "gemini.images": "gemini",
}

SUPPORTED_CHAT_PROTOCOLS = CHAT_PROTOCOLS
SUPPORTED_EMBEDDING_PROTOCOLS = EMBEDDING_PROTOCOLS


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
    return normalize_protocol(protocol).split(".", 1)[0]


def channel_locks_adapter(provider_type: str | None) -> bool:
    return adapter_family(provider_type) != "openai"


def connector_protocol(connector_type: str | None, kind: str | None = None) -> str:
    connector = normalize_connector(connector_type)
    if connector == "anthropic":
        return ANTHROPIC_MESSAGES
    if connector == "gemini":
        return protocol_for_kind(GEMINI_GENERATE_CONTENT, kind or "chat")
    return protocol_for_kind(OPENAI_CHAT, kind or "chat")


def connector_supports_protocol(connector_type: str | None, protocol: str | None) -> bool:
    connector = normalize_connector(connector_type)
    return normalize_protocol(protocol) in _CONNECTOR_PROTOCOLS.get(connector, set())


def connector_supports_kind(connector_type: str | None, kind: str | None) -> bool:
    connector = normalize_connector(connector_type)
    return (kind or "chat") in _CONNECTOR_KINDS.get(connector, set())


def channel_connector(channel: Any) -> str:
    raw_provider = (getattr(channel, "provider_type", None) or "").lower().strip()
    raw_connector = getattr(channel, "connector_type", None)
    connector = normalize_connector(raw_connector)
    if raw_provider in {"azure", "azure_openai", "azure-openai"} and connector == "openai":
        return "azure_openai"
    base_url = (getattr(channel, "base_url", None) or "").lower()
    if connector == "openai" and ".openai.azure.com" in base_url:
        return "azure_openai"
    return normalize_connector(raw_connector or raw_provider or "openai")


def channel_protocol(channel: Any, kind: str | None = None) -> str:
    raw = getattr(channel, "provider_type", None) or connector_protocol(channel_connector(channel), kind)
    return normalize_protocol(raw, kind=kind)


def resolve_upstream_protocol(
    model: Any | None,
    channel: Any,
    requested: str | None = None,
) -> str:
    kind = getattr(model, "kind", None) if model is not None else None
    override = requested or getattr(model, "upstream_protocol", None) or None
    return normalize_protocol(override, kind=kind) if override else channel_protocol(channel, kind)


async def run_chat(
    adapter: ProviderAdapter,
    protocol: str | None,
    channel: Any,
    upstream_model: str,
    payload: dict,
    stream: bool,
) -> ChatResult:
    if normalize_protocol(protocol) == OPENAI_RESPONSES and hasattr(adapter, "responses_from_chat"):
        return await adapter.responses_from_chat(channel, upstream_model, payload, stream)  # type: ignore[attr-defined]
    return await adapter.chat(channel, upstream_model, payload, stream)


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
