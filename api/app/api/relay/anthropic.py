from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.relay.chat import _load_route, _record_smart_usage
from app.core.deps import get_api_key_any
from app.core.request_ctx import client_ip, request_id_var
from app.db.session import get_db
from app.models import ApiKey, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, has_quota
from app.services.protocols.chat import (
    OpenAIToAnthropicStream,
    anthropic_messages_request_error,
    anthropic_to_openai_payload,
    openai_to_anthropic_response,
)
from app.services.quota import rate_limit, user_rpm

router = APIRouter(prefix="/v1", tags=["relay"])


@router.post("/messages")
async def messages(
    request: Request,
    creds: tuple[ApiKey, User] = Depends(get_api_key_any),
    db: AsyncSession = Depends(get_db),
):
    api_key, user = creds
    ok, msg = await has_quota(db, user, api_key)
    if not ok:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, msg)
    rpm = await user_rpm(db, user.id)
    if not await rate_limit(user.id, per_min=rpm):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json body")

    user_facing_model = payload.get("model")
    if not user_facing_model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model")
    if not payload.get("messages"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing messages")
    if err := anthropic_messages_request_error(payload):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)

    openai_payload = anthropic_to_openai_payload(payload)
    stream = bool(payload.get("stream"))
    policy, models_by_id, channels_by_id = await _load_route(
        db,
        user_facing_model,
        expected_modality="chat",
        expected_protocol="anthropic",
    )
    prompt_text = providers.extract_prompt_text(openai_payload)
    decision = await providers.select_route(
        policy,
        models_by_id,
        channels_by_id,
        prompt_text=prompt_text,
        client_ip=client_ip(request),
        db=db,
    )
    if not decision:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no available upstream")

    request_id = request_id_var.get() or f"req-{uuid.uuid4().hex[:16]}"
    started = time.time()
    candidates = [(decision.model, decision.channel)] + decision.fallback_chain
    resolved_label = getattr(decision, "chosen_label", None)

    if stream:
        async def streamer() -> AsyncIterator[bytes]:
            last_err: str | None = None
            for m, c in candidates:
                protocol = providers.resolve_adapter_protocol(m, c)
                adapter = providers.get_adapter(protocol)
                if not adapter:
                    last_err = f"no adapter for {protocol}"
                    continue
                try:
                    result = await adapter.chat(c, m.upstream_model, openai_payload, stream=True)
                except Exception as e:
                    last_err = str(e)
                    continue
                if result.status != 200 or result.stream is None:
                    last_err = str(result.body)
                    continue

                converter = OpenAIToAnthropicStream(model=user_facing_model)
                async for chunk in result.stream:
                    usage = providers.parse_usage_from_chunk(chunk)
                    if usage:
                        converter.prompt_tokens = int(usage.get("prompt_tokens") or converter.prompt_tokens or 0)
                        converter.completion_tokens = int(usage.get("completion_tokens") or converter.completion_tokens or 0)
                    for event in converter.feed(chunk):
                        yield event
                for event in converter.finish():
                    yield event

                cost = calc_cost_cents(m, converter.prompt_tokens, converter.completion_tokens)
                await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
                db.add(UsageLog(
                    user_id=user.id, api_key_id=api_key.id, model_id=m.id,
                    user_facing_model=user_facing_model, upstream_model=m.upstream_model,
                    prompt_tokens=converter.prompt_tokens,
                    completion_tokens=converter.completion_tokens,
                    cost_cents=cost, latency_ms=int((time.time() - started) * 1000),
                    status="ok", request_id=request_id,
                    kind="relay", resolved_label=resolved_label,
                ))
                await _record_smart_usage(db, user, api_key, decision, user_facing_model, request_id)
                db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
                await db.commit()
                return
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")

        return StreamingResponse(streamer(), media_type="text/event-stream")

    last_err = None
    for m, c in candidates:
        protocol = providers.resolve_adapter_protocol(m, c)
        adapter = providers.get_adapter(protocol)
        if not adapter:
            last_err = f"no adapter for {protocol}"
            continue
        try:
            result = await adapter.chat(c, m.upstream_model, openai_payload, stream=False)
        except Exception as e:
            last_err = str(e)
            continue
        if result.status == 200 and result.body:
            cost = calc_cost_cents(m, result.prompt_tokens, result.completion_tokens)
            await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
            db.add(UsageLog(
                user_id=user.id, api_key_id=api_key.id, model_id=m.id,
                user_facing_model=user_facing_model, upstream_model=m.upstream_model,
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                cost_cents=cost, latency_ms=int((time.time() - started) * 1000),
                status="ok", request_id=request_id,
                kind="relay", resolved_label=resolved_label,
            ))
            await _record_smart_usage(db, user, api_key, decision, user_facing_model, request_id)
            db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
            await db.commit()
            return JSONResponse(openai_to_anthropic_response(result.body, user_facing_model))
        last_err = result.body
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")