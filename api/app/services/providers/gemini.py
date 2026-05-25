from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.models import Channel
from app.services.providers.base import ChatResult


def _to_gemini(payload: dict) -> dict:
    """Convert OpenAI chat to Gemini generateContent body."""
    contents: list[dict] = []
    system_parts: list[str] = []
    for m in payload.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            # collapse content blocks to text for simplicity
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            system_parts.append(content); continue
        contents.append({
            "role": "user" if role == "user" else "model",
            "parts": [{"text": content}],
        })
    body: dict = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    gen_cfg: dict = {}
    if "temperature" in payload: gen_cfg["temperature"] = payload["temperature"]
    if "top_p" in payload: gen_cfg["topP"] = payload["top_p"]
    if "max_tokens" in payload: gen_cfg["maxOutputTokens"] = payload["max_tokens"]
    if "stop" in payload:
        s = payload["stop"]; gen_cfg["stopSequences"] = s if isinstance(s, list) else [s]
    if gen_cfg:
        body["generationConfig"] = gen_cfg
    return body


def _gemini_resp_to_openai(resp: dict, model: str) -> dict:
    text = ""
    finish = "stop"
    for cand in resp.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                text += part["text"]
        if cand.get("finishReason"):
            finish = cand["finishReason"].lower()
    usage = resp.get("usageMetadata") or {}
    pt = usage.get("promptTokenCount", 0)
    ct = usage.get("candidatesTokenCount", 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop" if finish == "stop" else finish,
        }],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }


class GeminiAdapter:
    """Google Gemini (Generative Language API).

    base_url default https://generativelanguage.googleapis.com
    api_key passed via ?key= query param.
    """
    name = "gemini"

    def _url(self, channel: Channel, upstream_model: str, action: str) -> str:
        base = channel.base_url.rstrip("/")
        if "/v1" not in base:
            base = base + "/v1beta"
        key = channel.api_key_enc or ""
        return f"{base}/models/{upstream_model}:{action}?key={key}"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = _to_gemini(payload)
        headers = {"Content-Type": "application/json"}

        if not stream:
            url = self._url(channel, upstream_model, "generateContent")
            async with httpx.AsyncClient(timeout=120) as cli:
                r = await cli.post(url, json=body, headers=headers)
                try:
                    data = r.json()
                except Exception:
                    return ChatResult(status=r.status_code, body={"error": {"message": r.text}})
                if r.status_code != 200:
                    return ChatResult(status=r.status_code, body=data)
                out = _gemini_resp_to_openai(data, upstream_model)
                u = out.get("usage", {})
                return ChatResult(status=200, body=out, prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0))

        url = self._url(channel, upstream_model, "streamGenerateContent") + "&alt=sse"

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
                                for cand in ev.get("candidates") or []:
                                    for part in (cand.get("content") or {}).get("parts") or []:
                                        if "text" in part:
                                            out = {
                                                "id": chat_id, "object": "chat.completion.chunk",
                                                "created": created, "model": upstream_model,
                                                "choices": [{"index": 0, "delta": {"content": part["text"]}, "finish_reason": None}],
                                            }
                                            yield f"data: {json.dumps(out)}\n\n".encode()
                                u = ev.get("usageMetadata")
                                if u:
                                    prompt_tokens = u.get("promptTokenCount", prompt_tokens)
                                    completion_tokens = u.get("candidatesTokenCount", completion_tokens)
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
        # Gemini embedContent — accept OpenAI 'input' as text(s).
        inputs = payload.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        if not inputs:
            return 400, {"error": {"message": "missing input"}}
        base = channel.base_url.rstrip("/")
        if "/v1" not in base:
            base = base + "/v1beta"
        key = channel.api_key_enc or ""
        url = f"{base}/models/{upstream_model}:batchEmbedContents?key={key}"
        body = {"requests": [{"model": f"models/{upstream_model}", "content": {"parts": [{"text": t}]}} for t in inputs]}
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(url, json=body, headers={"Content-Type": "application/json"})
            try:
                data = r.json()
            except Exception:
                return r.status_code, {"error": {"message": r.text}}
            if r.status_code != 200:
                return r.status_code, data
            embeds = data.get("embeddings") or []
            out = {
                "object": "list",
                "data": [{"object": "embedding", "index": i, "embedding": e.get("values", [])} for i, e in enumerate(embeds)],
                "model": upstream_model,
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }
            return 200, out
