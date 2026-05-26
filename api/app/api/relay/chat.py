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
from app.models import ApiKey, Channel, Model, RoutePolicy, RouteScope, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, has_quota
from app.services.quota import rate_limit, user_rpm
from app.core.request_ctx import request_id_var

router = APIRouter(prefix="/v1", tags=["relay"])


async def _load_route(db: AsyncSession, user_facing_model: str) -> tuple[RoutePolicy, dict[int, Model], dict[int, Channel]]:
    policy = (
        await db.execute(select(RoutePolicy).where(RoutePolicy.user_facing_model == user_facing_model))
    ).scalar_one_or_none()
    if not policy or not policy.enabled or policy.scope == RouteScope.private:
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


async def _record_smart_usage(
    db: AsyncSession,
    user: User,
    api_key: ApiKey,
    decision,
    user_facing_model: str,
    request_id: str,
) -> None:
    """If smart routing called the embedding classifier, charge for it and log a row.
    Tied to the relay row by request_id; kind='classifier'. Cache hits cost nothing
    but still leave a zero-cost row for traceability.
    """
    eu = getattr(decision, "embedding_usage", None)
    if not eu:
        return
    cost = calc_cost_cents(eu.model, eu.prompt_tokens, 0) if eu.status == "ok" else 0
    if cost > 0:
        await charge_user(db, user, api_key, cost, ref_id=request_id, note=f"{user_facing_model} [classifier]")
    db.add(UsageLog(
        user_id=user.id, api_key_id=api_key.id, model_id=eu.model.id,
        user_facing_model=user_facing_model, upstream_model=eu.upstream_model,
        prompt_tokens=eu.prompt_tokens, completion_tokens=0,
        cost_cents=cost, latency_ms=eu.latency_ms,
        status=eu.status, request_id=request_id,
        kind="classifier", resolved_label=getattr(decision, "chosen_label", None),
    ))


@router.post("/chat/completions")
async def chat_completions(
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

    stream = bool(payload.get("stream"))
    policy, models_by_id, channels_by_id = await _load_route(db, user_facing_model)
    prompt_text = providers.extract_prompt_text(payload)
    decision = await providers.select_route(
        policy, models_by_id, channels_by_id, prompt_text=prompt_text, db=db,
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
                kind="relay", resolved_label=resolved_label,
            ))
            await _record_smart_usage(db, user, api_key, decision, user_facing_model, request_id)
            db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
            await db.commit()
            return JSONResponse(result.body)
        last_err = result.body
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"all upstreams failed: {last_err}")
