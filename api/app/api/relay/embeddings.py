from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.relay.chat import _load_route, _record_smart_usage
from app.core.deps import get_api_key
from app.core.request_ctx import client_ip
from app.db.session import get_db
from app.models import ApiKey, UsageLog, User
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, has_quota
from app.services.embedding_relay import EmbeddingRelayError, execute_embedding_relay

router = APIRouter(prefix="/v1", tags=["relay"])


@router.post("/embeddings")
async def embeddings(
    request: Request,
    creds: tuple[ApiKey, User] = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    api_key, user = creds
    ok, msg = await has_quota(db, user, api_key)
    if not ok:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, msg)
    payload = await request.json()
    user_facing_model = payload.get("model")
    if not user_facing_model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model")
    policy, models_by_id, channels_by_id = await _load_route(
        db, user_facing_model, expected_modality="embedding", expected_protocol="openai"
    )
    prompt_text = providers.extract_prompt_text(payload)
    decision = await providers.select_route(
        policy,
        models_by_id,
        channels_by_id,
        prompt_text=prompt_text,
        client_ip=client_ip(request),
        db=db,
    )
    if not decision:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no upstream")

    request_id = f"req-{uuid.uuid4().hex[:16]}"
    started = time.time()
    candidates = [(decision.model, decision.channel)] + decision.fallback_chain
    try:
        body, selected_model, _ = await execute_embedding_relay(candidates, payload)
    except EmbeddingRelayError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    usage = (body or {}).get("usage") or {}
    pt = usage.get("prompt_tokens", 0)
    cost = calc_cost_cents(selected_model, pt, 0)
    await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
    db.add(UsageLog(
        user_id=user.id, api_key_id=api_key.id, model_id=selected_model.id,
        user_facing_model=user_facing_model, upstream_model=selected_model.upstream_model,
        prompt_tokens=pt, completion_tokens=0, cost_cents=cost,
        latency_ms=int((time.time() - started) * 1000),
        status="ok", request_id=request_id,
        kind="relay", resolved_label=getattr(decision, "chosen_label", None),
    ))
    await _record_smart_usage(db, user, api_key, decision, user_facing_model, request_id)
    db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
    await db.commit()
    return JSONResponse(body)
