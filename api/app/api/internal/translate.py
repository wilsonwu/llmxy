"""Internal translator endpoints — invoked by Envoy when a request targets
a non-OpenAI provider (anthropic / gemini / azure). Envoy applies a
`prefix_rewrite` from `/v1/` → `/internal/translate/v1/` on the translator
cluster, then forwards with ext_authz headers (x-llmxy-channel-id etc).

We pick the adapter, call into the existing provider code, and return an
OpenAI-shape response (with usage). The Lua filter on the response path
then extracts usage uniformly.

These endpoints are auth-less — they trust that ext_authz already
authenticated. The translator cluster is only reachable from Envoy
(bound to 127.0.0.1).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

import uuid

from app.db.session import get_db
from app.models import ApiKey, Channel, Model, User
from app.services import providers
from app.services.image_relay import ImageRelayError, execute_image_relay

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/translate", tags=["internal"])


async def _load_channel(db: AsyncSession, channel_id: str | None) -> Channel:
    if not channel_id or not channel_id.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing or invalid x-llmxy-channel-id")
    ch = await db.get(Channel, int(channel_id))
    if not ch or not ch.enabled:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "channel unavailable")
    return ch


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_llmxy_channel_id: str | None = Header(None),
    x_llmxy_upstream_model: str | None = Header(None),
    x_llmxy_upstream_protocol: str | None = Header(None),
):
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        channel = await _load_channel(db, x_llmxy_channel_id)
        protocol = x_llmxy_upstream_protocol or channel.provider_type
        adapter = providers.get_adapter(protocol)
        if not adapter:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"no adapter for {protocol}")
        if not x_llmxy_upstream_model:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing x-llmxy-upstream-model")

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json body")
        stream = bool(payload.get("stream"))

        try:
            result = await adapter.chat(channel, x_llmxy_upstream_model, payload, stream=stream)
        except Exception as e:
            log.warning("translator adapter error: %s", e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

        if stream:
            if result.status != 200 or result.stream is None:
                raise HTTPException(result.status or 502, str(result.body))
            return StreamingResponse(result.stream, media_type="text/event-stream")

        if result.status != 200 or not result.body:
            raise HTTPException(result.status or 502, str(result.body))
        return JSONResponse(result.body)


@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    x_llmxy_channel_id: str | None = Header(None),
    x_llmxy_upstream_model: str | None = Header(None),
    x_llmxy_upstream_protocol: str | None = Header(None),
):
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        channel = await _load_channel(db, x_llmxy_channel_id)
        protocol = x_llmxy_upstream_protocol or channel.provider_type
        adapter = providers.get_adapter(protocol)
        if not adapter:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"no adapter for {protocol}")
        if not x_llmxy_upstream_model:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing x-llmxy-upstream-model")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json body")
        try:
            status_code, body = await adapter.embeddings(channel, x_llmxy_upstream_model, payload)
        except Exception as e:
            log.warning("translator embeddings error: %s", e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        if status_code != 200:
            raise HTTPException(status_code, str(body))
        return JSONResponse(body)


@router.post("/v1/images/generations")
async def images_generations(
    request: Request,
    x_llmxy_channel_id: str | None = Header(None),
    x_llmxy_model_id: str | None = Header(None),
    x_llmxy_user_id: str | None = Header(None),
    x_llmxy_api_key_id: str | None = Header(None),
    x_llmxy_user_facing_model: str | None = Header(None),
    x_llmxy_image_chain: str | None = Header(None),
):
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        if not x_llmxy_user_id or not x_llmxy_user_id.isdigit():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing or invalid x-llmxy-user-id")
        user = await db.get(User, int(x_llmxy_user_id))
        if not user:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown user")
        api_key = None
        if x_llmxy_api_key_id and x_llmxy_api_key_id.isdigit():
            api_key = await db.get(ApiKey, int(x_llmxy_api_key_id))

        # Build the failover chain ext_authz resolved. Fall back to the single
        # model/channel headers if the chain header is absent (older callers).
        candidates: list[tuple[Model, Channel]] = []
        chain = x_llmxy_image_chain or ""
        if chain:
            for part in chain.split(","):
                mid, _, cid = part.partition(":")
                if not (mid.isdigit() and cid.isdigit()):
                    continue
                mm = await db.get(Model, int(mid))
                cc = await db.get(Channel, int(cid))
                if mm and mm.kind == "image" and cc and cc.enabled:
                    candidates.append((mm, cc))
        if not candidates:
            channel = await _load_channel(db, x_llmxy_channel_id)
            if not x_llmxy_model_id or not x_llmxy_model_id.isdigit():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing or invalid x-llmxy-model-id")
            model = await db.get(Model, int(x_llmxy_model_id))
            if not model or model.kind != "image":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "model is not an image model")
            candidates = [(model, channel)]

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json body")

        request_id = f"req-{uuid.uuid4().hex[:16]}"
        try:
            code, body = await execute_image_relay(
                db,
                user=user,
                api_key=api_key,
                candidates=candidates,
                payload=payload,
                request_id=request_id,
                user_facing_model=x_llmxy_user_facing_model or candidates[0][0].code,
            )
        except ImageRelayError as e:
            raise HTTPException(e.status_code, e.body["error"]["message"]) from e

        if code != 200:
            raise HTTPException(code if code in (502, 504, 402) else status.HTTP_502_BAD_GATEWAY, str(body))
        return JSONResponse(body)
