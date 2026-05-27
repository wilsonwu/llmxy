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
from app.core.request_ctx import client_ip
from app.core.security import hash_api_key
from app.db.session import AsyncSessionLocal
from app.models import Channel, KeyStatus, Model, RoutePolicy, RouteScope, UserStatus
from app.services import api_key_cache, providers, quota_cache
from app.services.envoy.config import _channel_cluster_name, _is_direct
from app.services.quota import rate_limit

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

    # Snapshot lookup — zero PG hits on cache hit. enforce_key_state_cached
    # opens a session only when status actually flips (expire/window-roll).
    snap = await api_key_cache.get_apikey_snapshot(key_hash)
    if snap is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
    from app.services.api_key import enforce_key_state_cached
    snap = await enforce_key_state_cached(snap)
    if snap.status != KeyStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"api key {snap.status.value}")

    user = await api_key_cache.get_user_snapshot(snap.user_id)
    if user is None or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")

    # ----------------------------------------------------- quota / rate
    window_start_epoch = quota_cache.window_start_epoch_for(snap)
    ok, msg = await quota_cache.has_quota_fast(
        snap.user_id, snap.id, snap.quota_cents, window_start_epoch,
    )
    if not ok:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, msg)
    if not await rate_limit(snap.user_id, per_min=user.plan_rpm):
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
                    "x-llmxy-user-id": str(snap.user_id),
                    "x-llmxy-api-key-id": str(snap.id),
                },
            )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing model in body")

    # Route resolution still hits PG (RoutePolicy/Model/Channel) — out of
    # scope for this phase. Wrap in its own session so the snapshot path
    # above isn't entangled with a transaction.
    async with AsyncSessionLocal() as db:
        policy, models_by_id, channels_by_id = await _load_route(db, model_name)
        try:
            parsed_payload = json.loads(body) if body else None
        except Exception:
            parsed_payload = None
        prompt_text = providers.extract_prompt_text(parsed_payload) if parsed_payload else ""
        decision = await providers.select_route(
            policy, models_by_id, channels_by_id,
            prompt_text=prompt_text, client_ip=client_ip(request), db=db,
        )
        if not decision:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "no available upstream")

        m, c = decision.model, decision.channel
        rid = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
        cluster = _channel_cluster_name(c.id) if _is_direct(c) else "translator"

        headers: dict[str, str] = {
            "x-llmxy-cluster": cluster,
            "x-llmxy-request-id": rid,
            "x-llmxy-user-id": str(snap.user_id),
            "x-llmxy-api-key-id": str(snap.id),
            "x-llmxy-model-id": str(m.id),
            "x-llmxy-user-facing-model": model_name,
            "x-llmxy-upstream-model": m.upstream_model,
            "x-llmxy-provider-type": (c.provider_type or "").lower(),
            "x-llmxy-channel-id": str(c.id),
        }
        if decision.chosen_label:
            headers["x-llmxy-resolved-label"] = decision.chosen_label

        # Smart-mode embedding-classifier overhead: forwarded as headers so
        # ALS can write the classifier UsageLog + charge in the SAME PG
        # transaction as the relay row. Writing it here would split billing
        # across two transactions and one could persist while the other
        # rolls back, leaving "classifier-only" orphan rows when envoy
        # never proxies the relay (cluster miss, upstream timeout, etc.).
        eu = getattr(decision, "embedding_usage", None)
        if eu is not None:
            headers["x-llmxy-classifier-model-id"] = str(eu.model.id)
            headers["x-llmxy-classifier-upstream-model"] = eu.upstream_model or ""
            headers["x-llmxy-classifier-prompt-tokens"] = str(int(eu.prompt_tokens or 0))
            headers["x-llmxy-classifier-latency-ms"] = str(int(eu.latency_ms or 0))
            headers["x-llmxy-classifier-status"] = eu.status or "ok"

        # Inject upstream credentials only for direct (OpenAI-compat) clusters.
        # Translator cluster reaches our own FastAPI which holds the channel
        # already and will use channel.api_key_enc itself.
        if _is_direct(c):
            upstream_key = decrypt(c.api_key_enc) or ""
            if upstream_key:
                headers["authorization"] = f"Bearer {upstream_key}"

        return Response(status_code=200, headers=headers)
