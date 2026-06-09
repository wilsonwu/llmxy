from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.relay.chat import _load_route, _record_smart_usage
from app.core.deps import get_api_key
from app.core.request_ctx import client_ip, request_id_var
from app.db.session import get_db
from app.models import ApiKey, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, has_quota
from app.services.protocols.openai_responses import (
    openai_chat_sse_to_responses_sse,
    openai_chat_to_responses_response,
    responses_to_openai_chat_payload,
)
from app.services.quota import rate_limit, user_rpm

router = APIRouter(prefix="/v1", tags=["relay"])


@router.post("/responses")
async def responses(
    request: Request,
    creds: tuple[ApiKey, User] = Depends(get_api_key),
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

    chat_payload = responses_to_openai_chat_payload(payload)
    stream = bool(payload.get("stream"))
    policy, models_by_id, channels_by_id = await _load_route(
        db, user_facing_model, expected_modality="chat", expected_protocol="openai.responses"
    )
    prompt_text = providers.extract_prompt_text(chat_payload)
    decision = await providers.select_route(
        policy, models_by_id, channels_by_id,
        prompt_text=prompt_text, client_ip=client_ip(request), db=db,
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
                connector = providers.resolve_connector_type(m, c)
                protocol = providers.resolve_upstream_protocol(m, c)
                adapter = providers.get_connector_adapter(connector)
                if not adapter:
                    last_err = f"no connector adapter for {connector}"
                    continue
                if not providers.connector_supports_protocol(connector, protocol):
                    last_err = f"connector {connector} does not support protocol {protocol}"
                    continue
                try:
                    result = await providers.run_chat(adapter, protocol, c, m.upstream_model, chat_payload, stream=True)
                except Exception as e:
                    last_err = str(e)
                    continue
                if result.status != 200 or result.stream is None:
                    last_err = str(result.body)
                    continue

                prompt_tokens = 0
                completion_tokens = 0

                async def source() -> AsyncIterator[bytes]:
                    nonlocal prompt_tokens, completion_tokens
                    async for chunk in result.stream:
                        usage = providers.parse_usage_from_chunk(chunk)
                        if usage:
                            prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens or 0)
                            completion_tokens = int(usage.get("completion_tokens") or completion_tokens or 0)
                        yield chunk

                async for event in openai_chat_sse_to_responses_sse(source(), model=user_facing_model):
                    yield event

                cost = calc_cost_cents(m, prompt_tokens, completion_tokens)
                await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
                db.add(UsageLog(
                    user_id=user.id, api_key_id=api_key.id, model_id=m.id,
                    user_facing_model=user_facing_model, upstream_model=m.upstream_model,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
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
        connector = providers.resolve_connector_type(m, c)
        protocol = providers.resolve_upstream_protocol(m, c)
        adapter = providers.get_connector_adapter(connector)
        if not adapter:
            last_err = f"no connector adapter for {connector}"
            continue
        if not providers.connector_supports_protocol(connector, protocol):
            last_err = f"connector {connector} does not support protocol {protocol}"
            continue
        try:
            result = await providers.run_chat(adapter, protocol, c, m.upstream_model, chat_payload, stream=False)
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
            return JSONResponse(openai_chat_to_responses_response(result.body, user_facing_model))
        last_err = result.body
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")
