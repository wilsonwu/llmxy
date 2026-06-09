from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.services.protocols.ids import normalize_protocol


def route_exposes(route: Any, protocol: str) -> bool:
    kind = getattr(route, "modality", None) or "chat"
    protocols = route_protocols(route)
    return normalize_protocol(protocol, kind=kind) in protocols


def route_protocols(route: Any) -> list[str]:
    raw = getattr(route, "exposed_protocols", None) or ["openai"]
    kind = getattr(route, "modality", None) or "chat"
    protocols: list[str] = []
    for item in raw:
        value = normalize_protocol(str(item), kind=kind)
        if value and value not in protocols:
            protocols.append(value)
    return protocols or [normalize_protocol("openai", kind=kind)]


def openai_chat_token_limit(payload: dict[str, Any] | None, default: Any = None) -> Any:
    if not isinstance(payload, dict):
        return default
    if payload.get("max_completion_tokens") is not None:
        return payload.get("max_completion_tokens")
    if payload.get("max_tokens") is not None:
        return payload.get("max_tokens")
    return default


def anthropic_messages_request_error(payload: dict[str, Any]) -> str | None:
    if "max_completion_tokens" in payload:
        return "max_completion_tokens is not valid for Anthropic Messages; use max_tokens"
    if "max_tokens" not in payload:
        return "missing max_tokens"
    value = payload.get("max_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return "max_tokens must be a positive integer"
    return None


def anthropic_to_openai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list) and system:
        messages.append({"role": "system", "content": _anthropic_content_to_openai(system)})

    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        messages.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": _anthropic_content_to_openai(msg.get("content")),
        })

    out: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages,
    }
    if "max_tokens" in payload:
        out["max_tokens"] = payload.get("max_tokens")
    if "temperature" in payload:
        out["temperature"] = payload.get("temperature")
    if "top_p" in payload:
        out["top_p"] = payload.get("top_p")
    if "stop_sequences" in payload:
        out["stop"] = payload.get("stop_sequences")
    if "stream" in payload:
        out["stream"] = bool(payload.get("stream"))
    if out.get("stream"):
        out["stream_options"] = {"include_usage": True}
    return out


def openai_to_anthropic_response(body: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) else {}
    message = choice.get("message") or {}
    content = message.get("content") or ""
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": _openai_content_to_anthropic(content),
        "stop_reason": _openai_finish_reason_to_anthropic(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


async def openai_sse_to_anthropic_sse(
    stream: AsyncIterator[bytes],
    *,
    model: str,
) -> AsyncIterator[bytes]:
    converter = OpenAIToAnthropicStream(model=model)
    async for chunk in stream:
        for event in converter.feed(chunk):
            yield event
    for event in converter.finish():
        yield event


class OpenAIToAnthropicStream:
    def __init__(self, *, model: str) -> None:
        self.model = model
        self.message_id = f"msg_{uuid.uuid4().hex}"
        self.created = int(time.time())
        self.buffer = ""
        self.started = False
        self.content_started = False
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.stop_reason = "end_turn"

    def feed(self, chunk: bytes) -> list[bytes]:
        text = chunk.decode("utf-8", errors="ignore")
        self.buffer += text
        parts = self.buffer.split("\n\n")
        self.buffer = parts.pop() if parts else ""
        events: list[bytes] = []
        for part in parts:
            for line in part.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                usage = obj.get("usage") or {}
                if usage:
                    self.prompt_tokens = int(usage.get("prompt_tokens") or self.prompt_tokens or 0)
                    self.completion_tokens = int(usage.get("completion_tokens") or self.completion_tokens or 0)
                choice = (obj.get("choices") or [{}])[0]
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    self.stop_reason = _openai_finish_reason_to_anthropic(finish_reason)
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    events.extend(self._ensure_started())
                    events.append(_sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": content},
                    }))
        return events

    def finish(self) -> list[bytes]:
        events = self._ensure_started()
        events.append(_sse("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        }))
        events.append(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self.completion_tokens},
        }))
        events.append(_sse("message_stop", {"type": "message_stop"}))
        return events

    def _ensure_started(self) -> list[bytes]:
        events: list[bytes] = []
        if not self.started:
            self.started = True
            events.append(_sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": self.message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self.prompt_tokens, "output_tokens": 0},
                },
            }))
        if not self.content_started:
            self.content_started = True
            events.append(_sse("content_block_start", {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }))
        return events


def _anthropic_content_to_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            typ = block.get("type")
            if typ == "text":
                blocks.append({"type": "text", "text": block.get("text") or ""})
            elif typ == "image":
                source = block.get("source") or {}
                if source.get("type") == "base64" and source.get("media_type") and source.get("data"):
                    blocks.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{source['media_type']};base64,{source['data']}"
                        },
                    })
            else:
                blocks.append(block)
        return blocks
    return ""


def _openai_content_to_anthropic(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append({"type": "text", "text": block.get("text") or ""})
        return out or [{"type": "text", "text": ""}]
    return [{"type": "text", "text": ""}]


def _openai_finish_reason_to_anthropic(reason: Any) -> str:
    if reason == "length":
        return "max_tokens"
    if reason == "tool_calls":
        return "tool_use"
    if reason == "content_filter":
        return "stop_sequence"
    return "end_turn"


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")