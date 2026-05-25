from __future__ import annotations

import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_api_key
from app.db.session import get_db
from app.models import ApiKey, Channel, Model, RoutePolicy, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, check_balance
from app.services.quota import rate_limit

router = APIRouter(prefix="/v1", tags=["relay"])


async def _load_route(db: AsyncSession, user_facing_model: str) -> tuple[RoutePolicy, dict[int, Model], dict[int, Channel]]:
    policy = (
        await db.execute(select(RoutePolicy).where(RoutePolicy.user_facing_model == user_facing_model))
    ).scalar_one_or_none()
    if not policy or not policy.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"model {user_facing_model} not available")
    target_ids = [int(t["model_id"]) for t in (policy.targets_jsonb or [])]
    if not target_ids:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "route has no targets")
    models = (await db.execute(select(Model).where(Model.id.in_(target_ids)))).scalars().all()
    models_by_id = {m.id: m for m in models}
    channel_ids = {m.channel_id for m in models}
    channels = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars().all()
    channels_by_id = {c.id: c for c in channels}
    return policy, models_by_id, channels_by_id


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    creds: tuple[ApiKey, User] = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    api_key, user = creds
    ok, msg = check_balance(user, api_key)
    if not ok:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, msg)
    if not await rate_limit(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json body")

    user_facing_model = payload.get("model")
    if not user_facing_model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model")

    stream = bool(payload.get("stream"))
    policy, models_by_id, channels_by_id = await _load_route(db, user_facing_model)
    decision = providers.select_route(policy, models_by_id, channels_by_id)
    if not decision:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no available upstream")

    request_id = f"req-{uuid.uuid4().hex[:16]}"
    started = time.time()
    candidates = [(decision.model, decision.channel)] + decision.fallback_chain

    if stream:
        async def streamer() -> AsyncIterator[bytes]:
            last_err: str | None = None
            for m, c in candidates:
                adapter = providers.get_adapter(c.provider_type)
                if not adapter:
                    last_err = f"no adapter for {c.provider_type}"
                    continue
                try:
                    result = await adapter.chat(c, m.upstream_model, payload, stream=True)
                except Exception as e:
                    last_err = str(e); continue
                if result.status != 200 or result.stream is None:
                    last_err = str(result.body); continue
                prompt_tokens = 0
                completion_tokens = 0
                async for chunk in result.stream:
                    u = providers.parse_usage_from_chunk(chunk)
                    if u:
                        prompt_tokens = u.get("prompt_tokens", prompt_tokens)
                        completion_tokens = u.get("completion_tokens", completion_tokens)
                    yield chunk
                cost = calc_cost_cents(m, prompt_tokens, completion_tokens)
                await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
                db.add(UsageLog(
                    user_id=user.id, api_key_id=api_key.id, model_id=m.id,
                    user_facing_model=user_facing_model, upstream_model=m.upstream_model,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    cost_cents=cost, latency_ms=int((time.time() - started) * 1000),
                    status="ok", request_id=request_id,
                ))
                await db.commit()
                return
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")

        return StreamingResponse(streamer(), media_type="text/event-stream")

    last_err = None
    for m, c in candidates:
        adapter = providers.get_adapter(c.provider_type)
        if not adapter:
            last_err = f"no adapter for {c.provider_type}"; continue
        try:
            result = await adapter.chat(c, m.upstream_model, payload, stream=False)
        except Exception as e:
            last_err = str(e); continue
        if result.status == 200 and result.body:
            cost = calc_cost_cents(m, result.prompt_tokens, result.completion_tokens)
            await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
            db.add(UsageLog(
                user_id=user.id, api_key_id=api_key.id, model_id=m.id,
                user_facing_model=user_facing_model, upstream_model=m.upstream_model,
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                cost_cents=cost, latency_ms=int((time.time() - started) * 1000),
                status="ok", request_id=request_id,
            ))
            await db.commit()
            return JSONResponse(result.body)
        last_err = result.body
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")
