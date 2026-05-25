from __future__ import annotations

from typing import AsyncIterator

import httpx

from app.core.config import settings
from app.core.crypto import decrypt
from app.models import Channel
from app.services.providers.base import ChatResult


class AzureOpenAIAdapter:
    """Azure OpenAI Service.

    channel.base_url: https://{resource}.openai.azure.com  (host only, no path)
    channel.api_key_enc: Azure api-key
    model.upstream_model: deployment name (Azure picks the model via deployment; body.model is ignored)

    Request/response shape (incl. SSE) matches OpenAI exactly; only URL and auth header differ.
    api-version defaults to settings.AZURE_OPENAI_API_VERSION and can be overridden globally.
    """

    name = "azure"

    def _headers(self, channel: Channel) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = decrypt(channel.api_key_enc)
        if key:
            h["api-key"] = key
        return h

    def _url(self, channel: Channel, deployment: str, path: str) -> str:
        base = channel.base_url.rstrip("/")
        # tolerate base already ending in /openai
        if not base.endswith("/openai"):
            base = base + "/openai"
        return f"{base}/deployments/{deployment}{path}?api-version={settings.AZURE_OPENAI_API_VERSION}"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = dict(payload)
        body.pop("model", None)  # Azure ignores body.model; deployment in URL decides
        body["stream"] = stream
        if stream:
            opts = dict(body.get("stream_options") or {})
            opts["include_usage"] = True
            body["stream_options"] = opts
        url = self._url(channel, upstream_model, "/chat/completions")
        headers = self._headers(channel)

        if not stream:
            async with httpx.AsyncClient(timeout=settings.UPSTREAM_TIMEOUT) as cli:
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
        body = dict(payload); body.pop("model", None)
        url = self._url(channel, upstream_model, "/embeddings")
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(url, json=body, headers=self._headers(channel))
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"error": {"message": r.text}}
