from __future__ import annotations

import json
from typing import Any

MAX_TOKENS_FIELD = "max_tokens"
MAX_COMPLETION_TOKENS_FIELD = "max_completion_tokens"


def normalize_chat_payload_for_protocol(
    payload: dict[str, Any],
    *,
    force_max_completion_tokens: bool = False,
    preferred_token_field: str | None = None,
) -> dict[str, Any]:
    body = dict(payload)
    token_field = MAX_COMPLETION_TOKENS_FIELD if force_max_completion_tokens else preferred_token_field
    if token_field == MAX_COMPLETION_TOKENS_FIELD:
        max_tokens = body.pop(MAX_TOKENS_FIELD, None)
        if max_tokens is not None and MAX_COMPLETION_TOKENS_FIELD not in body:
            body[MAX_COMPLETION_TOKENS_FIELD] = max_tokens
    elif token_field == MAX_TOKENS_FIELD:
        max_completion_tokens = body.pop(MAX_COMPLETION_TOKENS_FIELD, None)
        if max_completion_tokens is not None and MAX_TOKENS_FIELD not in body:
            body[MAX_TOKENS_FIELD] = max_completion_tokens
    return body


def _mentions_unsupported_pair(response_body: Any, unsupported_field: str, suggested_field: str) -> bool:
    try:
        text = json.dumps(response_body, ensure_ascii=False).lower()
    except TypeError:
        text = str(response_body).lower()
    return (
        unsupported_field in text
        and suggested_field in text
        and ("unsupported" in text or "not supported" in text)
    )


def should_retry_with_max_completion_tokens(payload: dict[str, Any], response_body: Any) -> bool:
    if MAX_TOKENS_FIELD not in payload or MAX_COMPLETION_TOKENS_FIELD in payload:
        return False
    return _mentions_unsupported_pair(response_body, MAX_TOKENS_FIELD, MAX_COMPLETION_TOKENS_FIELD)


def should_retry_with_max_tokens(payload: dict[str, Any], response_body: Any) -> bool:
    if MAX_COMPLETION_TOKENS_FIELD not in payload or MAX_TOKENS_FIELD in payload:
        return False
    return _mentions_unsupported_pair(response_body, MAX_COMPLETION_TOKENS_FIELD, MAX_TOKENS_FIELD)
