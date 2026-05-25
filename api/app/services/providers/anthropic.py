from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.models import Channel
from app.services.providers.base import ChatResult


def _to_anthropic(payload: dict) -> tuple[dict, str | None]:
    """Convert OpenAI chat payload to Anthropic /v1/messages payload.
    Returns (body, system_prompt_or_None).
    """
    msgs = payload.get("messages") or []
    system_parts: list[str] = []
    out_msgs: list[dict] = []
    for m in msgs:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            continue
        if role not in ("user", "assistant"):
            continue
        # Anthropic content may be str or list of blocks; pass through if list
        out_msgs.append({"role": role, "content": content})
    body: dict = {
        "messages": out_msgs,
        "max_tokens": payload.get("max_tokens", 1024),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    for k in ("temperature", "top_p", "top_k", "stop_sequences"):
        if k in payload:
            body[k] = payload[k]
    if "stop" in payload and "stop_sequences" not in body:
        s = payload["stop"]
        body["stop_sequences"] = s if isinstance(s, list) else [s]
    return body, body.get("system")


def _anthropic_resp_to_openai(resp: dict, model: str) -> dict:
    text = ""
    for block in resp.get("content", []) or []:
        if block.get("type") == "text":
            text += block.get("text", "")
    usage = resp.get("usage") or {}
    pt = usage.get("input_tokens", 0)
    ct = usage.get("output_tokens", 0)
    return {
        "id": resp.get("id") or f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": resp.get("stop_reason") or "stop",
        }],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }


class AnthropicAdapter:
    """Anthropic Messages API (https://docs.anthropic.com/en/api/messages)."""
    name = "anthropic"

    def _headers(self, channel: Channel) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": channel.api_key_enc or "",
            "anthropic-version": "2023-06-01",
        }

    def _url(self, channel: Channel) -> str:
        base = channel.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        return f"{base}/messages"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body, _ = _to_anthropic(payload)
        body["model"] = upstream_model
        body["stream"] = stream
        url = self._url(channel)
        headers = self._headers(channel)

        if not stream:
            async with httpx.AsyncClient(timeout=120) as cli:
                r = await cli.post(url, json=body, headers=headers)
                try:
                    data = r.json()
                except Exception:
                    return ChatResult(status=r.status_code, body={"error": {"message": r.text}})
                if r.status_code != 200:
                    return ChatResult(status=r.status_code, body=data)
                out = _anthropic_resp_to_openai(data, upstream_model)
                u = out.get("usage", {})
                return ChatResult(status=200, body=out, prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0))

        async def gen() -> AsyncIterator[bytes]:
            chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
            created = int(time.time())
            prompt_tokens = 0
            completion_tokens = 0
            async with httpx.AsyncClient(timeout=None) as cli:
                async with cli.stream("POST", url, json=body, headers=headers) as r:
                    buf = ""
                    async for chunk in r.aiter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            event, buf = buf.split("\n\n", 1)
                            for line in event.splitlines():
                                if not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if not data_str:
                                    continue
                                try:
                                    ev = json.loads(data_str)
                                except Exception:
                                    continue
                                t = ev.get("type")
                                if t == "message_start":
                                    u = (ev.get("message") or {}).get("usage") or {}
                                    prompt_tokens = u.get("input_tokens", 0)
                                elif t == "content_block_delta":
                                    delta = ev.get("delta") or {}
                                    if delta.get("type") == "text_delta":
                                        out = {
                                            "id": chat_id, "object": "chat.completion.chunk",
                                            "created": created, "model": upstream_model,
                                            "choices": [{"index": 0, "delta": {"content": delta.get("text", "")}, "finish_reason": None}],
                                        }
                                        yield f"data: {json.dumps(out)}\n\n".encode()
                                elif t == "message_delta":
                                    u = (ev.get("usage") or {})
                                    completion_tokens = u.get("output_tokens", completion_tokens)
                                elif t == "message_stop":
                                    final = {
                                        "id": chat_id, "object": "chat.completion.chunk",
                                        "created": created, "model": upstream_model,
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
                                    }
                                    yield f"data: {json.dumps(final)}\n\n".encode()
                                    yield b"data: [DONE]\n\n"

        return ChatResult(status=200, stream=gen())

    async def embeddings(self, channel: Channel, upstream_model: str, payload: dict) -> tuple[int, dict]:
        return 501, {"error": {"message": "Anthropic does not provide embeddings"}}
