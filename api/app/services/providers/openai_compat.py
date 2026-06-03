from __future__ import annotations

from typing import Any


_MAX_COMPLETION_PREFIXES = (
    "gpt-5",
    "gpt5",
    "o1",
    "o3",
    "o4",
)


def requires_max_completion_tokens(upstream_model: str | None) -> bool:
    model = (upstream_model or "").lower().strip()
    return any(model.startswith(prefix) for prefix in _MAX_COMPLETION_PREFIXES)


def normalize_chat_payload_for_model(payload: dict[str, Any], upstream_model: str) -> dict[str, Any]:
    body = dict(payload)
    if requires_max_completion_tokens(upstream_model):
        max_tokens = body.pop("max_tokens", None)
        if max_tokens is not None and "max_completion_tokens" not in body:
            body["max_completion_tokens"] = max_tokens
    return body
