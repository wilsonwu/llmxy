from __future__ import annotations

from typing import AsyncIterator

import httpx

from app.core.config import settings
from app.models import Channel
from app.services.providers.base import ChatResult


class AzureOpenAIAdapter:
    """Azure OpenAI Service.

    channel.base_url: https://{resource}.openai.azure.com  (host only, no path)
    channel.api_key_enc: Azure api-key
    model.upstream_model: deployment name (Azure 用 deployment 决定模型，body.model 字段被忽略)

    Request/response shape (含 SSE) 与 OpenAI 完全一致，只是 URL 和鉴权头不同。
    api-version 默认走 settings.AZURE_OPENAI_API_VERSION，可整体覆盖。
    """

    name = "azure"

    def _headers(self, channel: Channel) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if channel.api_key_enc:
            h["api-key"] = channel.api_key_enc
        return h

    def _url(self, channel: Channel, deployment: str, path: str) -> str:
        base = channel.base_url.rstrip("/")
        # 若用户填了 /openai 后缀也兼容
        if not base.endswith("/openai"):
            base = base + "/openai"
        return f"{base}/deployments/{deployment}{path}?api-version={settings.AZURE_OPENAI_API_VERSION}"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = dict(payload)
        body.pop("model", None)  # Azure ignores body.model; deployment in URL decides
        body["stream"] = stream
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
