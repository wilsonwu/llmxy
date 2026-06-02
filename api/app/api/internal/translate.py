"""Internal translator endpoints invoked by Envoy after ext_proc auth/routing.

Envoy applies a `prefix_rewrite` from `/v1/` to `/internal/translate/v1/`, and
ext_proc supplies `x-llmxy-*` headers describing the selected user, API key,
route, target model, channel, and upstream protocol. These endpoints perform
the upstream adapter call, protocol-shaped response conversion, and synchronous
billing. The translator cluster is only reachable from Envoy in production.
"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

import uuid

from app.models import ApiKey, Channel, Model, RoutePolicy, RouteScope, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user
from app.services.image_relay import ImageRelayError, execute_image_relay
from app.services.protocols.chat import OpenAIToAnthropicStream, anthropic_to_openai_payload, openai_to_anthropic_response, route_exposes

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/translate", tags=["internal"])


@router.get("/v1/models")
async def list_models():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(RoutePolicy).where(
                    RoutePolicy.enabled.is_(True),
                    RoutePolicy.scope == RouteScope.public,
                )
            )
        ).scalars().all()
        return {
            "object": "list",
            "data": [
                {"id": r.user_facing_model, "object": "model", "owned_by": "llmxy"}
                for r in rows
                if route_exposes(r, "openai")
            ],
        }


async def _load_channel(db: AsyncSession, channel_id: str | None) -> Channel:
    if not channel_id or not channel_id.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing or invalid x-llmxy-channel-id")
    ch = await db.get(Channel, int(channel_id))
    if not ch or not ch.enabled:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "channel unavailable")
    return ch


async def _billing_context(
    db: AsyncSession,
    *,
    user_id: str | None,
    api_key_id: str | None,
    model_id: str | None,
) -> tuple[User | None, ApiKey | None, Model | None]:
    user = await db.get(User, int(user_id)) if (user_id and user_id.isdigit()) else None
    api_key = await db.get(ApiKey, int(api_key_id)) if (api_key_id and api_key_id.isdigit()) else None
    model = await db.get(Model, int(model_id)) if (model_id and model_id.isdigit()) else None
    return user, api_key, model


async def _record_classifier_from_headers(
    db: AsyncSession,
    *,
    user: User,
    api_key: ApiKey | None,
    user_facing_model: str,
    request_id: str,
    resolved_label: str | None,
    cls_model_id: str | None,
    cls_upstream: str | None,
    cls_prompt_tokens: str | None,
    cls_latency_ms: str | None,
    cls_status: str | None,
) -> None:
    if not (cls_model_id and cls_model_id.isdigit() and cls_status):
        return
    model = await db.get(Model, int(cls_model_id))
    if not model:
        return
    prompt_tokens = int(cls_prompt_tokens) if (cls_prompt_tokens and cls_prompt_tokens.isdigit()) else 0
    latency_ms = int(cls_latency_ms) if (cls_latency_ms and cls_latency_ms.isdigit()) else 0
    cost = calc_cost_cents(model, prompt_tokens, 0) if cls_status == "ok" else 0
    if cost > 0:
        await charge_user(db, user, api_key, cost, ref_id=request_id, note=f"{user_facing_model} [classifier]")
    db.add(UsageLog(
        user_id=user.id, api_key_id=api_key.id if api_key else None, model_id=model.id,
        user_facing_model=user_facing_model, upstream_model=cls_upstream or None,
        prompt_tokens=prompt_tokens, completion_tokens=0,
        cost_cents=cost, latency_ms=latency_ms,
        status=cls_status, request_id=request_id,
        kind="classifier", resolved_label=resolved_label,
    ))


async def _bill_chat_relay(
    db: AsyncSession,
    *,
    user: User | None,
    api_key: ApiKey | None,
    model: Model | None,
    user_facing_model: str | None,
    upstream_model: str,
    prompt_tokens: int,
    completion_tokens: int,
    started: float,
    request_id: str,
    resolved_label: str | None,
    classifier_headers: dict[str, str | None],
) -> None:
    if not (user and model):
        return
    label = user_facing_model or model.code
    cost = calc_cost_cents(model, prompt_tokens, completion_tokens)
    if cost > 0:
        await charge_user(db, user, api_key, cost, ref_id=request_id, note=label)
    db.add(UsageLog(
        user_id=user.id, api_key_id=api_key.id if api_key else None, model_id=model.id,
        user_facing_model=label, upstream_model=upstream_model,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        cost_cents=cost, latency_ms=int((time.time() - started) * 1000),
        status="ok", request_id=request_id,
        kind="relay", resolved_label=resolved_label,
    ))
    await _record_classifier_from_headers(
        db,
        user=user,
        api_key=api_key,
        user_facing_model=label,
        request_id=request_id,
        resolved_label=resolved_label,
        cls_model_id=classifier_headers.get("model_id"),
        cls_upstream=classifier_headers.get("upstream_model"),
        cls_prompt_tokens=classifier_headers.get("prompt_tokens"),
        cls_latency_ms=classifier_headers.get("latency_ms"),
        cls_status=classifier_headers.get("status"),
    )
    db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
    await db.commit()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_llmxy_channel_id: str | None = Header(None),
    x_llmxy_model_id: str | None = Header(None),
    x_llmxy_user_id: str | None = Header(None),
    x_llmxy_api_key_id: str | None = Header(None),
    x_llmxy_user_facing_model: str | None = Header(None),
    x_llmxy_upstream_model: str | None = Header(None),
    x_llmxy_upstream_protocol: str | None = Header(None),
    x_llmxy_request_id: str | None = Header(None),
    x_llmxy_resolved_label: str | None = Header(None),
    x_llmxy_classifier_model_id: str | None = Header(None),
    x_llmxy_classifier_upstream_model: str | None = Header(None),
    x_llmxy_classifier_prompt_tokens: str | None = Header(None),
    x_llmxy_classifier_latency_ms: str | None = Header(None),
    x_llmxy_classifier_status: str | None = Header(None),
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
        started = time.time()
        request_id = x_llmxy_request_id or f"req-{uuid.uuid4().hex[:16]}"
        user, api_key, model = await _billing_context(
            db, user_id=x_llmxy_user_id, api_key_id=x_llmxy_api_key_id, model_id=x_llmxy_model_id
        )
        classifier_headers = {
            "model_id": x_llmxy_classifier_model_id,
            "upstream_model": x_llmxy_classifier_upstream_model,
            "prompt_tokens": x_llmxy_classifier_prompt_tokens,
            "latency_ms": x_llmxy_classifier_latency_ms,
            "status": x_llmxy_classifier_status,
        }

        try:
            result = await adapter.chat(channel, x_llmxy_upstream_model, payload, stream=stream)
        except Exception as e:
            log.warning("translator adapter error: %s", e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

        if stream:
            if result.status != 200 or result.stream is None:
                raise HTTPException(result.status or 502, str(result.body))
            async def stream_and_bill() -> AsyncIterator[bytes]:
                prompt_tokens = 0
                completion_tokens = 0
                async for chunk in result.stream:
                    usage = providers.parse_usage_from_chunk(chunk)
                    if usage:
                        prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens or 0)
                        completion_tokens = int(usage.get("completion_tokens") or completion_tokens or 0)
                    yield chunk
                await _bill_chat_relay(
                    db,
                    user=user,
                    api_key=api_key,
                    model=model,
                    user_facing_model=x_llmxy_user_facing_model,
                    upstream_model=x_llmxy_upstream_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    started=started,
                    request_id=request_id,
                    resolved_label=x_llmxy_resolved_label,
                    classifier_headers=classifier_headers,
                )
            return StreamingResponse(stream_and_bill(), media_type="text/event-stream")

        if result.status != 200 or not result.body:
            raise HTTPException(result.status or 502, str(result.body))
        await _bill_chat_relay(
            db,
            user=user,
            api_key=api_key,
            model=model,
            user_facing_model=x_llmxy_user_facing_model,
            upstream_model=x_llmxy_upstream_model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            started=started,
            request_id=request_id,
            resolved_label=x_llmxy_resolved_label,
            classifier_headers=classifier_headers,
        )
        return JSONResponse(result.body)


@router.post("/v1/messages")
async def messages(
    request: Request,
    x_llmxy_channel_id: str | None = Header(None),
    x_llmxy_model_id: str | None = Header(None),
    x_llmxy_user_id: str | None = Header(None),
    x_llmxy_api_key_id: str | None = Header(None),
    x_llmxy_user_facing_model: str | None = Header(None),
    x_llmxy_upstream_model: str | None = Header(None),
    x_llmxy_upstream_protocol: str | None = Header(None),
    x_llmxy_request_id: str | None = Header(None),
    x_llmxy_resolved_label: str | None = Header(None),
    x_llmxy_classifier_model_id: str | None = Header(None),
    x_llmxy_classifier_upstream_model: str | None = Header(None),
    x_llmxy_classifier_prompt_tokens: str | None = Header(None),
    x_llmxy_classifier_latency_ms: str | None = Header(None),
    x_llmxy_classifier_status: str | None = Header(None),
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
        openai_payload = anthropic_to_openai_payload(payload)
        stream = bool(payload.get("stream"))
        started = time.time()
        request_id = x_llmxy_request_id or f"req-{uuid.uuid4().hex[:16]}"
        user, api_key, model = await _billing_context(
            db, user_id=x_llmxy_user_id, api_key_id=x_llmxy_api_key_id, model_id=x_llmxy_model_id
        )
        classifier_headers = {
            "model_id": x_llmxy_classifier_model_id,
            "upstream_model": x_llmxy_classifier_upstream_model,
            "prompt_tokens": x_llmxy_classifier_prompt_tokens,
            "latency_ms": x_llmxy_classifier_latency_ms,
            "status": x_llmxy_classifier_status,
        }

        try:
            result = await adapter.chat(channel, x_llmxy_upstream_model, openai_payload, stream=stream)
        except Exception as e:
            log.warning("translator messages adapter error: %s", e)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e

        if stream:
            if result.status != 200 or result.stream is None:
                raise HTTPException(result.status or 502, str(result.body))

            async def stream_and_bill() -> AsyncIterator[bytes]:
                converter = OpenAIToAnthropicStream(model=x_llmxy_user_facing_model or payload.get("model") or x_llmxy_upstream_model)
                async for chunk in result.stream:
                    usage = providers.parse_usage_from_chunk(chunk)
                    if usage:
                        converter.prompt_tokens = int(usage.get("prompt_tokens") or converter.prompt_tokens or 0)
                        converter.completion_tokens = int(usage.get("completion_tokens") or converter.completion_tokens or 0)
                    for event in converter.feed(chunk):
                        yield event
                for event in converter.finish():
                    yield event
                await _bill_chat_relay(
                    db,
                    user=user,
                    api_key=api_key,
                    model=model,
                    user_facing_model=x_llmxy_user_facing_model,
                    upstream_model=x_llmxy_upstream_model,
                    prompt_tokens=converter.prompt_tokens,
                    completion_tokens=converter.completion_tokens,
                    started=started,
                    request_id=request_id,
                    resolved_label=x_llmxy_resolved_label,
                    classifier_headers=classifier_headers,
                )

            return StreamingResponse(stream_and_bill(), media_type="text/event-stream")

        if result.status != 200 or not result.body:
            raise HTTPException(result.status or 502, str(result.body))
        await _bill_chat_relay(
            db,
            user=user,
            api_key=api_key,
            model=model,
            user_facing_model=x_llmxy_user_facing_model,
            upstream_model=x_llmxy_upstream_model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            started=started,
            request_id=request_id,
            resolved_label=x_llmxy_resolved_label,
            classifier_headers=classifier_headers,
        )
        return JSONResponse(openai_to_anthropic_response(result.body, x_llmxy_user_facing_model or payload.get("model") or x_llmxy_upstream_model))


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

        # Build the failover chain ext_proc resolved. Fall back to the single
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
