from app.services.providers.openai_compat import (
    MAX_COMPLETION_TOKENS_FIELD,
    MAX_TOKENS_FIELD,
    normalize_chat_payload_for_protocol,
    should_retry_with_max_tokens,
    should_retry_with_max_completion_tokens,
)
from app.services.providers.registry import (
    SUPPORTED_CHAT_PROTOCOLS,
    SUPPORTED_CONNECTORS,
    channel_connector,
    channel_protocol,
    connector_supports_protocol,
    get_connector_adapter,
    resolve_connector_type,
    resolve_upstream_protocol,
)
from app.services.providers.anthropic import _to_anthropic
from app.services.providers.gemini import _to_gemini
from app.services.protocols.chat import anthropic_messages_request_error, anthropic_to_openai_payload
from types import SimpleNamespace


def test_openai_semantic_protocol_has_multiple_connectors():
    assert SUPPORTED_CHAT_PROTOCOLS == ["openai", "anthropic", "gemini"]
    assert "azure" not in SUPPORTED_CHAT_PROTOCOLS
    assert {"openai", "azure_openai"}.issubset(set(SUPPORTED_CONNECTORS))
    assert connector_supports_protocol("openai", "openai") is True
    assert connector_supports_protocol("azure_openai", "openai") is True
    assert get_connector_adapter("openai") is not None
    assert get_connector_adapter("azure_openai") is not None


def test_channel_protocol_and_connector_are_resolved_separately():
    channel = SimpleNamespace(provider_type="openai", connector_type="azure_openai")
    model = SimpleNamespace(upstream_protocol=None)

    assert channel_protocol(channel) == "openai"
    assert channel_connector(channel) == "azure_openai"
    assert resolve_upstream_protocol(model, channel) == "openai"
    assert resolve_connector_type(model, channel) == "azure_openai"


def test_legacy_azure_provider_type_maps_to_openai_protocol_and_azure_connector():
    channel = SimpleNamespace(provider_type="azure", connector_type="openai")

    assert channel_protocol(channel) == "openai"
    assert channel_connector(channel) == "azure_openai"


def test_force_max_completion_tokens_converts_max_tokens():
    payload = {"model": "gpt-5.5", "messages": [], "max_tokens": 128}

    out = normalize_chat_payload_for_protocol(payload, force_max_completion_tokens=True)

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 128
    assert payload["max_tokens"] == 128


def test_preferred_max_tokens_converts_max_completion_tokens():
    payload = {"model": "gpt-4o-mini", "messages": [], "max_completion_tokens": 128}

    out = normalize_chat_payload_for_protocol(payload, preferred_token_field=MAX_TOKENS_FIELD)

    assert "max_completion_tokens" not in out
    assert out["max_tokens"] == 128
    assert payload["max_completion_tokens"] == 128


def test_default_openai_protocol_keeps_max_tokens():
    payload = {"model": "gpt-4o-mini", "messages": [], "max_tokens": 128}

    out = normalize_chat_payload_for_protocol(payload)

    assert out["max_tokens"] == 128
    assert "max_completion_tokens" not in out


def test_openai_in_to_anthropic_upstream_maps_to_anthropic_max_tokens():
    payload = {
        "model": "claude-route",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 256,
    }

    body, _ = _to_anthropic(payload)

    assert body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_messages_standard_token_limit_is_max_tokens():
    assert anthropic_messages_request_error({"model": "m", "messages": [], "max_tokens": 128}) is None
    assert anthropic_messages_request_error({"model": "m", "messages": []}) == "missing max_tokens"
    assert (
        anthropic_messages_request_error({"model": "m", "messages": [], "max_tokens": 0})
        == "max_tokens must be a positive integer"
    )
    assert (
        anthropic_messages_request_error({"model": "m", "messages": [], "max_completion_tokens": 128})
        == "max_completion_tokens is not valid for Anthropic Messages; use max_tokens"
    )


def test_anthropic_in_to_openai_force_max_completion_tokens_converts_max_tokens():
    anthropic = {
        "model": "gpt-5.5",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }

    openai_payload = anthropic_to_openai_payload(anthropic)
    out = normalize_chat_payload_for_protocol(openai_payload, force_max_completion_tokens=True)

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 128


def test_anthropic_in_to_default_openai_protocol_keeps_max_tokens_regardless_of_model_name():
    anthropic = {
        "model": "smart",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }

    openai_payload = anthropic_to_openai_payload(anthropic)
    out = normalize_chat_payload_for_protocol(openai_payload)

    assert out["max_tokens"] == 128
    assert "max_completion_tokens" not in out


def test_openai_compat_retries_when_upstream_rejects_max_tokens():
    payload = {"model": "smart", "messages": [], "max_tokens": 128}
    body = {
        "error": {
            "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."
        }
    }

    assert should_retry_with_max_completion_tokens(payload, body) is True
    retry_body = normalize_chat_payload_for_protocol(
        payload,
        preferred_token_field=MAX_COMPLETION_TOKENS_FIELD,
    )
    assert "max_tokens" not in retry_body
    assert retry_body["max_completion_tokens"] == 128


def test_openai_compat_retries_when_upstream_rejects_max_completion_tokens():
    payload = {"model": "smart", "messages": [], "max_completion_tokens": 128}
    body = {
        "error": {
            "message": "Unsupported parameter: 'max_completion_tokens' is not supported with this model. Use 'max_tokens' instead."
        }
    }

    assert should_retry_with_max_tokens(payload, body) is True
    retry_body = normalize_chat_payload_for_protocol(
        payload,
        preferred_token_field=MAX_TOKENS_FIELD,
    )
    assert "max_completion_tokens" not in retry_body
    assert retry_body["max_tokens"] == 128


def test_anthropic_in_to_anthropic_upstream_round_trips_image_blocks():
    anthropic = {
        "model": "claude-route",
        "max_tokens": 64,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
            ],
        }],
    }

    openai_payload = anthropic_to_openai_payload(anthropic)
    body, _ = _to_anthropic(openai_payload)

    assert body["max_tokens"] == 64
    assert body["messages"][0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
    }


def test_openai_in_to_gemini_upstream_uses_max_completion_tokens():
    payload = {
        "model": "gemini-route",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 512,
    }

    body = _to_gemini(payload)

    assert body["generationConfig"]["maxOutputTokens"] == 512
