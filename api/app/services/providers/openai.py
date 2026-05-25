from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.core.crypto import decrypt
from app.models import Channel
from app.services.providers.base import ChatResult


class OpenAIAdapter:
    """OpenAI-compatible upstream (works for OpenAI, DeepSeek, Moonshot, Qwen, Together, etc.).

    base_url should be the API root (e.g. https://api.openai.com/v1).
    """
    name = "openai"

    def _headers(self, channel: Channel) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = decrypt(channel.api_key_enc)
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _url(self, channel: Channel, path: str) -> str:
        base = channel.base_url.rstrip("/")
        # accept base ending in /v1 or not
        if not base.endswith("/v1"):
            base = base + "/v1"
        return f"{base}{path}"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = dict(payload)
        body["model"] = upstream_model
        body["stream"] = stream
        if stream:
            # require upstream to include usage in the final chunk so we can bill
            opts = dict(body.get("stream_options") or {})
            opts["include_usage"] = True
            body["stream_options"] = opts
        url = self._url(channel, "/chat/completions")
        headers = self._headers(channel)

        if not stream:
            async with httpx.AsyncClient(timeout=120) as cli:
                r = await cli.post(url, json=body, headers=headers)
                try:
                    data = r.json()
                except Exception:
                    data = {"error": {"message": r.text}}
                usage = (data or {}).get("usage") or {}
                return ChatResult(
                    status=r.status_code,
                    body=data,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )

        async def gen() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as cli:
                async with cli.stream("POST", url, json=body, headers=headers) as r:
                    async for chunk in r.aiter_raw():
                        yield chunk

        return ChatResult(status=200, stream=gen())

    async def embeddings(self, channel: Channel, upstream_model: str, payload: dict) -> tuple[int, dict]:
        body = dict(payload); body["model"] = upstream_model
        url = self._url(channel, "/embeddings")
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(url, json=body, headers=self._headers(channel))
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"error": {"message": r.text}}
