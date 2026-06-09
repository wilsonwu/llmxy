from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.services.protocols.chat import openai_chat_token_limit


def responses_to_openai_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    value = payload.get("input")
    if isinstance(value, str):
        messages.append({"role": "user", "content": value})
    elif isinstance(value, list):
        for item in value:
            msg = _responses_input_item_to_chat_message(item)
            if msg:
                messages.append(msg)

    out: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages or [{"role": "user", "content": ""}],
    }
    if "max_output_tokens" in payload:
        out["max_tokens"] = payload.get("max_output_tokens")
    elif "max_tokens" in payload:
        out["max_tokens"] = payload.get("max_tokens")
    for key in ("temperature", "top_p", "stream", "stop"):
        if key in payload:
            out[key] = payload[key]
    if out.get("stream"):
        out["stream_options"] = {"include_usage": True}
    return out


def openai_chat_to_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "user"
        content = msg.get("content")
        if role == "system":
            text = _content_to_text(content)
            if text:
                instructions.append(text)
            continue
        input_items.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": _chat_content_to_responses_content(content),
        })

    out: dict[str, Any] = {
        "model": payload.get("model"),
        "input": input_items or [{"role": "user", "content": [{"type": "input_text", "text": ""}]}],
    }
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    max_tokens = openai_chat_token_limit(payload)
    if max_tokens is not None:
        out["max_output_tokens"] = max_tokens
    for key in ("temperature", "top_p", "stream"):
        if key in payload:
            out[key] = payload[key]
    if "stop" in payload:
        out["stop"] = payload["stop"]
    return out


def responses_response_to_openai_chat(body: dict[str, Any], model: str) -> dict[str, Any]:
    text = _responses_output_text(body)
    usage = body.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    finish_reason = "stop" if (body.get("status") in (None, "completed")) else body.get("status")
    return {
        "id": body.get("id") or f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(body.get("created_at") or time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or prompt_tokens + completion_tokens),
        },
    }


def openai_chat_to_responses_response(body: dict[str, Any], model: str) -> dict[str, Any]:
    choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) else {}
    message = choice.get("message") or {}
    text = _content_to_text(message.get("content"))
    usage = body.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    response_id = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [{
            "id": item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "output_text": text,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": int(usage.get("total_tokens") or prompt_tokens + completion_tokens),
        },
    }


async def openai_chat_sse_to_responses_sse(stream: AsyncIterator[bytes], *, model: str) -> AsyncIterator[bytes]:
    response_id = f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    content_index = 0
    prompt_tokens = 0
    completion_tokens = 0
    started = False
    async for obj in _iter_sse_json(stream):
        usage = obj.get("usage") or {}
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens or 0)
            completion_tokens = int(usage.get("completion_tokens") or completion_tokens or 0)
        choice = (obj.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        text = delta.get("content")
        if text:
            if not started:
                started = True
                yield _sse("response.created", {"type": "response.created", "response": _response_stub(response_id, model, "in_progress")})
                yield _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
                yield _sse("response.content_part.added", {"type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": content_index, "part": {"type": "output_text", "text": "", "annotations": []}})
            yield _sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item_id, "output_index": 0, "content_index": content_index, "delta": text})

    if not started:
        yield _sse("response.created", {"type": "response.created", "response": _response_stub(response_id, model, "in_progress")})
        yield _sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
        yield _sse("response.content_part.added", {"type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": content_index, "part": {"type": "output_text", "text": "", "annotations": []}})
    yield _sse("response.output_text.done", {"type": "response.output_text.done", "item_id": item_id, "output_index": 0, "content_index": content_index, "text": ""})
    yield _sse("response.content_part.done", {"type": "response.content_part.done", "item_id": item_id, "output_index": 0, "content_index": content_index, "part": {"type": "output_text", "text": "", "annotations": []}})
    yield _sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": {"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": []}})
    completed = _response_stub(response_id, model, "completed")
    completed["usage"] = {"input_tokens": prompt_tokens, "output_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}
    yield _sse("response.completed", {"type": "response.completed", "response": completed})
    yield b"data: [DONE]\n\n"


async def responses_sse_to_openai_chat_sse(stream: AsyncIterator[bytes], *, model: str) -> AsyncIterator[bytes]:
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    prompt_tokens = 0
    completion_tokens = 0
    async for event, obj in _iter_sse_events(stream):
        typ = obj.get("type") or event
        if typ == "response.output_text.delta":
            out = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": obj.get("delta") or ""}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(out, separators=(',', ':'))}\n\n".encode("utf-8")
        elif typ == "response.completed":
            usage = (obj.get("response") or {}).get("usage") or {}
            prompt_tokens = int(usage.get("input_tokens") or prompt_tokens or 0)
            completion_tokens = int(usage.get("output_tokens") or completion_tokens or 0)

    final = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
    }
    yield f"data: {json.dumps(final, separators=(',', ':'))}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def _responses_input_item_to_chat_message(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {"role": "user", "content": item}
    if not isinstance(item, dict):
        return None
    role = item.get("role") or "user"
    if role not in {"system", "user", "assistant"}:
        role = "user"
    return {"role": role, "content": _content_to_text(item.get("content"))}


def _chat_content_to_responses_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            typ = block.get("type")
            if typ == "text":
                blocks.append({"type": "input_text", "text": block.get("text") or ""})
            elif typ == "image_url":
                image_url = block.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if isinstance(url, str):
                    blocks.append({"type": "input_image", "image_url": url})
        return blocks or [{"type": "input_text", "text": ""}]
    return [{"type": "input_text", "text": _content_to_text(content)}]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"text", "input_text", "output_text"}:
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def _responses_output_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    text = ""
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "output_text":
                text += block.get("text") or ""
    return text


async def _iter_sse_json(stream: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
    async for _, obj in _iter_sse_events(stream):
        yield obj


async def _iter_sse_events(stream: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
    buffer = ""
    async for chunk in stream:
        buffer += chunk.decode("utf-8", errors="ignore")
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            name: str | None = None
            data_lines: list[str] = []
            for line in event.splitlines():
                line = line.strip()
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        data_lines = []
                        break
                    data_lines.append(raw)
            if not data_lines:
                continue
            try:
                obj = json.loads("\n".join(data_lines))
            except Exception:
                continue
            if isinstance(obj, dict):
                yield name, obj


def _response_stub(response_id: str, model: str, status: str) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": [],
    }


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")
