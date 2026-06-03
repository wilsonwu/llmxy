from app.services.providers.openai_compat import normalize_chat_payload_for_model
from app.services.providers.anthropic import _to_anthropic
from app.services.providers.gemini import _to_gemini
from app.services.protocols.chat import anthropic_to_openai_payload


def test_gpt5_uses_max_completion_tokens():
    payload = {"model": "gpt-5.5", "messages": [], "max_tokens": 128}

    out = normalize_chat_payload_for_model(payload, "gpt-5.5")

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 128
    assert payload["max_tokens"] == 128


def test_legacy_models_keep_max_tokens():
    payload = {"model": "gpt-4o-mini", "messages": [], "max_tokens": 128}

    out = normalize_chat_payload_for_model(payload, "gpt-4o-mini")

    assert out["max_tokens"] == 128
    assert "max_completion_tokens" not in out


def test_openai_in_to_anthropic_upstream_uses_max_completion_tokens():
    payload = {
        "model": "claude-route",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 256,
    }

    body, _ = _to_anthropic(payload)

    assert body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_in_to_openai_gpt5_upstream_uses_max_completion_tokens():
    anthropic = {
        "model": "gpt-5.5",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hi"}],
    }

    openai_payload = anthropic_to_openai_payload(anthropic)
    out = normalize_chat_payload_for_model(openai_payload, "gpt-5.5")

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 128


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
