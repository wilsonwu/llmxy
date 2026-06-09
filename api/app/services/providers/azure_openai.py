from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.core.config import settings
from app.core.crypto import decrypt
from app.models import Channel
from app.services.providers.base import ChatResult
from app.services.providers.openai_compat import (
    MAX_COMPLETION_TOKENS_FIELD,
    MAX_TOKENS_FIELD,
    normalize_chat_payload_for_protocol,
    should_retry_with_max_tokens,
    should_retry_with_max_completion_tokens,
)
from app.services.protocols.openai_responses import (
    openai_chat_to_responses_payload,
    responses_response_to_openai_chat,
    responses_sse_to_openai_chat_sse,
)


class AzureOpenAIAdapter:
    """Azure OpenAI Service.

    channel.base_url: https://{resource}.openai.azure.com  (host only, no path)
    channel.api_key_enc: Azure api-key
    model.upstream_model: deployment name (Azure picks the model via deployment; body.model is ignored)

    Request/response shape (incl. SSE) matches OpenAI exactly; only URL and auth header differ.
    api-version defaults to settings.AZURE_OPENAI_API_VERSION and can be overridden globally.
    """

    name = "azure_openai"

    def __init__(self) -> None:
        self._token_limit_fields: dict[tuple[int | None, str], str] = {}

    def _target_key(self, channel: Channel, upstream_model: str) -> tuple[int | None, str]:
        return (getattr(channel, "id", None), upstream_model)

    def _preferred_token_field(self, channel: Channel, upstream_model: str) -> str | None:
        return self._token_limit_fields.get(self._target_key(channel, upstream_model))

    def _remember_token_field(self, channel: Channel, upstream_model: str, field: str) -> None:
        self._token_limit_fields[self._target_key(channel, upstream_model)] = field

    def _headers(self, channel: Channel) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = decrypt(channel.api_key_enc)
        if key:
            h["api-key"] = key
        return h

    def _url(self, channel: Channel, deployment: str, path: str, api_version: str | None = None) -> str:
        base = channel.base_url.rstrip("/")
        # tolerate base already ending in /openai
        if not base.endswith("/openai"):
            base = base + "/openai"
        ver = api_version or settings.AZURE_OPENAI_API_VERSION
        return f"{base}/deployments/{deployment}{path}?api-version={ver}"

    def _responses_url(self, channel: Channel, api_version: str | None = None) -> str:
        base = channel.base_url.rstrip("/")
        if base.endswith("/openai/v1"):
            root = base
        elif base.endswith("/openai"):
            root = f"{base}/v1"
        else:
            root = f"{base}/openai/v1"
        ver = api_version or settings.AZURE_OPENAI_RESPONSES_API_VERSION
        return f"{root}/responses?api-version={ver}"

    async def chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = normalize_chat_payload_for_protocol(
            payload,
            preferred_token_field=self._preferred_token_field(channel, upstream_model),
        )
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
                if r.status_code == 400 and should_retry_with_max_completion_tokens(payload, data):
                    self._remember_token_field(channel, upstream_model, MAX_COMPLETION_TOKENS_FIELD)
                    body = normalize_chat_payload_for_protocol(
                        payload,
                        preferred_token_field=MAX_COMPLETION_TOKENS_FIELD,
                    )
                    body.pop("model", None)
                    body["stream"] = stream
                    r = await cli.post(url, json=body, headers=headers)
                    try:
                        data = r.json()
                    except Exception:
                        data = {"error": {"message": r.text}}
                elif r.status_code == 400 and should_retry_with_max_tokens(payload, data):
                    self._remember_token_field(channel, upstream_model, MAX_TOKENS_FIELD)
                    body = normalize_chat_payload_for_protocol(
                        payload,
                        preferred_token_field=MAX_TOKENS_FIELD,
                    )
                    body.pop("model", None)
                    body["stream"] = stream
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
                    if r.status_code == 400:
                        raw = await r.aread()
                        try:
                            data = json.loads(raw.decode("utf-8", errors="ignore"))
                        except Exception:
                            data = {"error": {"message": raw.decode("utf-8", errors="ignore")}}
                        if should_retry_with_max_completion_tokens(payload, data):
                            self._remember_token_field(channel, upstream_model, MAX_COMPLETION_TOKENS_FIELD)
                            retry_body = normalize_chat_payload_for_protocol(
                                payload,
                                preferred_token_field=MAX_COMPLETION_TOKENS_FIELD,
                            )
                            retry_body.pop("model", None)
                            retry_body["stream"] = stream
                            opts = dict(retry_body.get("stream_options") or {})
                            opts["include_usage"] = True
                            retry_body["stream_options"] = opts
                            async with cli.stream("POST", url, json=retry_body, headers=headers) as retry:
                                async for chunk in retry.aiter_raw():
                                    yield chunk
                            return
                        if should_retry_with_max_tokens(payload, data):
                            self._remember_token_field(channel, upstream_model, MAX_TOKENS_FIELD)
                            retry_body = normalize_chat_payload_for_protocol(
                                payload,
                                preferred_token_field=MAX_TOKENS_FIELD,
                            )
                            retry_body.pop("model", None)
                            retry_body["stream"] = stream
                            opts = dict(retry_body.get("stream_options") or {})
                            opts["include_usage"] = True
                            retry_body["stream_options"] = opts
                            async with cli.stream("POST", url, json=retry_body, headers=headers) as retry:
                                async for chunk in retry.aiter_raw():
                                    yield chunk
                            return
                        yield raw
                        return
                    async for chunk in r.aiter_raw():
                        yield chunk

        return ChatResult(status=200, stream=gen())

    async def responses_from_chat(self, channel: Channel, upstream_model: str, payload: dict, stream: bool) -> ChatResult:
        body = openai_chat_to_responses_payload(payload)
        body["model"] = upstream_model
        body["stream"] = stream
        url = self._responses_url(channel)
        headers = self._headers(channel)

        if not stream:
            async with httpx.AsyncClient(timeout=settings.UPSTREAM_TIMEOUT) as cli:
                r = await cli.post(url, json=body, headers=headers)
                try:
                    data = r.json()
                except Exception:
                    data = {"error": {"message": r.text}}
                if r.status_code != 200:
                    return ChatResult(status=r.status_code, body=data)
                out = responses_response_to_openai_chat(data, upstream_model)
                usage = out.get("usage") or {}
                return ChatResult(
                    status=200,
                    body=out,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )

        async def gen() -> AsyncIterator[bytes]:
            async with httpx.AsyncClient(timeout=None) as cli:
                async with cli.stream("POST", url, json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        raw = await r.aread()
                        yield raw
                        return
                    async for chunk in responses_sse_to_openai_chat_sse(r.aiter_raw(), model=upstream_model):
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

    async def images(self, channel: Channel, upstream_model: str, payload: dict) -> tuple[int, dict]:
        body = dict(payload); body.pop("model", None)  # deployment in URL decides
        # gpt-image-1 / dall-e generations are only available on the image
        # preview api-version; the chat GA version 404s here.
        url = self._url(channel, upstream_model, "/images/generations", settings.AZURE_OPENAI_IMAGE_API_VERSION)
        try:
            async with httpx.AsyncClient(timeout=settings.IMAGE_RELAY_TIMEOUT) as cli:
                r = await cli.post(url, json=body, headers=self._headers(channel))
                try:
                    return r.status_code, r.json()
                except Exception:
                    return r.status_code, {"error": {"message": r.text}}
        except httpx.TimeoutException:
            return 504, {"error": {"message": "upstream image generation timed out", "type": "timeout"}}
        except Exception as e:
            return 502, {"error": {"message": str(e), "type": "upstream_error"}}
