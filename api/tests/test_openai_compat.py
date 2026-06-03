from app.services.providers.openai_compat import normalize_chat_payload_for_model


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
