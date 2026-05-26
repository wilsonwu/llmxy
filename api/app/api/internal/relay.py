"""Envoy ext_authz HTTP callback.

Envoy forwards the original request (path, headers in `allowed_headers`,
body if `with_request_body` configured) to this endpoint at:

    POST {server_uri}{path_prefix}{original_path}

We:
  1. Auth the api key (Authorization: Bearer sk-...).
  2. Look up the user; check status.
  3. Parse the JSON body to extract `model`; load route policy → pick a
     concrete (Model, Channel).
  4. Check quota + rate limit.
  5. On allow: 200 with headers used by Envoy to (a) route to the right
     cluster (`x-llmxy-cluster`), (b) inject upstream auth, (c) feed the
     ALS / Lua filters via `x-llmxy-*` headers — these must be listed in
     LDS `authorization_response.allowed_upstream_headers`.

On deny: any non-2xx status. We use 402 for quota, 429 for rate limit,
401 for auth — Envoy reflects the status back to the client.
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.core.security import hash_api_key
from app.db.session import AsyncSessionLocal
from app.models import ApiKey, Channel, KeyStatus, Model, RoutePolicy, RouteScope, UsageLog, User, UserStatus
from app.services import providers
from app.services.billing import calc_cost_cents, charge_user, has_quota
from app.services.envoy.config import _channel_cluster_name, _is_direct
from app.services.quota import rate_limit, user_rpm

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/relay", tags=["internal"])


async def _load_route(db: AsyncSession, user_facing_model: str):
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


def _extract_model_from_body(raw: bytes, path: str) -> str | None:
    """Try to read the model from the request body. Embeddings / chat both
    have a top-level `model` field; for /v1/models the body is irrelevant."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if isinstance(obj, dict):
        m = obj.get("model")
        if isinstance(m, str):
            return m
    return None


@router.api_route("/authz/{full_path:path}", methods=["POST", "GET", "PUT", "DELETE"])
async def authz(full_path: str, request: Request, authorization: str | None = Header(None)):
    # Envoy uses path_prefix=/internal/relay/authz, so {full_path} is the
    # original request path without leading slash (e.g. "v1/chat/completions").
    original_path = "/" + full_path

    # ------------------------------------------------------------------ auth
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing api key")
    plain = authorization.split(" ", 1)[1].strip()
    if not plain.startswith("sk-"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key format")
    key_hash = hash_api_key(plain)

    async with AsyncSessionLocal() as db:
        api_key = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        ).scalar_one_or_none()
        if not api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
        from app.services.api_key import enforce_key_state
        await enforce_key_state(db, api_key)
        if api_key.status != KeyStatus.active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"api key {api_key.status.value}")
        user = await db.get(User, api_key.user_id)
        if not user or user.status != UserStatus.active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")

        # ----------------------------------------------------- quota / rate
        ok, msg = await has_quota(db, user, api_key)
        if not ok:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, msg)
        rpm = await user_rpm(db, user.id)
        if not await rate_limit(user.id, per_min=rpm):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

        # ----------------------------------------------------- route lookup
        # /v1/models is a passthrough listing — pick any direct cluster or
        # fall back to translator. For now just deny — Envoy shouldn't route
        # listing requests through here in practice (no usage anyway).
        body = await request.body()
        model_name = _extract_model_from_body(body, original_path)
        if not model_name:
            # Allow listing endpoints without a model — but we need *some*
            # cluster. Pick translator (FastAPI handles /v1/models itself).
            if original_path.endswith("/v1/models") or original_path.rstrip("/").endswith("/models"):
                rid = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
                return Response(
                    status_code=200,
                    headers={
                        "x-llmxy-cluster": "translator",
                        "x-llmxy-request-id": rid,
                        "x-llmxy-user-id": str(user.id),
                        "x-llmxy-api-key-id": str(api_key.id),
                    },
                )
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model in body")

        policy, models_by_id, channels_by_id = await _load_route(db, model_name)
        try:
            parsed_payload = json.loads(body) if body else None
        except Exception:
            parsed_payload = None
        prompt_text = providers.extract_prompt_text(parsed_payload) if parsed_payload else ""
        decision = await providers.select_route(
            policy, models_by_id, channels_by_id, prompt_text=prompt_text, db=db,
        )
        if not decision:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no available upstream")

        m, c = decision.model, decision.channel
        rid = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
        cluster = _channel_cluster_name(c.id) if _is_direct(c) else "translator"

        # Smart-mode classifier overhead is invisible to Envoy/ALS — record it
        # here so it shows up in usage/billing tied to the same request_id.
        cu = getattr(decision, "classifier_usage", None)
        if cu is not None:
            try:
                cls_cost = calc_cost_cents(cu.model, cu.prompt_tokens, cu.completion_tokens) if cu.status == "ok" else 0
                if cls_cost > 0:
                    await charge_user(db, user, api_key, cls_cost, ref_id=rid, note=f"{model_name} [classifier]")
                db.add(UsageLog(
                    user_id=user.id, api_key_id=api_key.id, model_id=cu.model.id,
                    user_facing_model=model_name, upstream_model=cu.upstream_model,
                    prompt_tokens=cu.prompt_tokens, completion_tokens=cu.completion_tokens,
                    cost_cents=cls_cost, latency_ms=cu.latency_ms,
                    status=cu.status, request_id=rid,
                    kind="classifier", resolved_label=decision.chosen_label,
                ))
                await db.commit()
            except Exception as e:
                log.warning("classifier usage log failed rid=%s: %s", rid, e)

        headers: dict[str, str] = {
            "x-llmxy-cluster": cluster,
            "x-llmxy-request-id": rid,
            "x-llmxy-user-id": str(user.id),
            "x-llmxy-api-key-id": str(api_key.id),
            "x-llmxy-model-id": str(m.id),
            "x-llmxy-user-facing-model": model_name,
            "x-llmxy-upstream-model": m.upstream_model,
            "x-llmxy-provider-type": (c.provider_type or "").lower(),
            "x-llmxy-channel-id": str(c.id),
        }
        if decision.chosen_label:
            headers["x-llmxy-resolved-label"] = decision.chosen_label

        # Inject upstream credentials only for direct (OpenAI-compat) clusters.
        # Translator cluster reaches our own FastAPI which holds the channel
        # already and will use channel.api_key_enc itself.
        if _is_direct(c):
            upstream_key = decrypt(c.api_key_enc) or ""
            if upstream_key:
                headers["authorization"] = f"Bearer {upstream_key}"

        return Response(status_code=200, headers=headers)
