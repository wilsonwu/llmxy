from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.relay.chat import _load_route
from app.core.deps import get_api_key
from app.db.session import get_db
from app.models import ApiKey, User
from app.services import providers
from app.services.billing import has_quota
from app.services.image_relay import ImageRelayError, execute_image_relay
from app.services.quota import rate_limit, user_rpm

router = APIRouter(prefix="/v1", tags=["relay"])


@router.post("/images/generations")
async def images_generations(
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

    payload = await request.json()
    user_facing_model = payload.get("model")
    if not user_facing_model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model")

    policy, models_by_id, channels_by_id = await _load_route(db, user_facing_model, expected_modality="image")

    prompt_text = providers.extract_prompt_text(payload)
    decision = await providers.select_route(
        policy, models_by_id, channels_by_id, prompt_text=prompt_text, db=db,
    )
    if not decision:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no upstream")

    request_id = f"req-{uuid.uuid4().hex[:16]}"
    candidates = [(decision.model, decision.channel)] + decision.fallback_chain
    try:
        code, body = await execute_image_relay(
            db,
            user=user,
            api_key=api_key,
            candidates=candidates,
            payload=payload,
            request_id=request_id,
            user_facing_model=user_facing_model,
            resolved_label=getattr(decision, "chosen_label", None),
        )
    except ImageRelayError as e:
        raise HTTPException(e.status_code, e.body["error"]["message"]) from e

    if code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY if code not in (502, 504) else code, str(body))
    return JSONResponse(body)
