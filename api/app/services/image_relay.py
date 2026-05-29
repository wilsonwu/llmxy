"""Shared text-to-image relay logic: pre-deduct (hold) → call upstream →
reconcile (refund unused / charge shortfall) → write UsageLog.

Used by both the public FastAPI relay endpoint (`/v1/images/generations`,
direct clients) and the Envoy data-plane translator endpoint, so image
billing is identical regardless of how the request arrives. Image requests
never go through the token-based Envoy ALS billing path (see als_server: it
skips kind=="image" models).
"""
from __future__ import annotations

import logging
import time
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, Model, Channel, UsageLog, User
from app.services import providers
from app.services.billing import (
    available_cents,
    charge_user,
    quote_image_cost_cents,
    refund_to_sources,
)

log = logging.getLogger(__name__)


class ImageRelayError(Exception):
    """Carries an HTTP status + OpenAI-shape error body for the caller to
    translate into a response. Raised before any hold is placed."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"message": message}}


def _parse_params(payload: dict) -> tuple[str, str, int, str]:
    size = str(payload.get("size") or "1024x1024")
    quality = str(payload.get("quality") or "standard")
    try:
        n = int(payload.get("n") or 1)
    except (TypeError, ValueError):
        n = 1
    n = max(1, n)
    prompt_text = str(payload.get("prompt") or "")
    return size, quality, n, prompt_text


async def execute_image_relay(
    db: AsyncSession,
    *,
    user: User,
    api_key: ApiKey | None,
    candidates: list[tuple[Model, Channel]],
    payload: dict,
    request_id: str,
    user_facing_model: str,
    resolved_label: str | None = None,
) -> tuple[int, dict]:
    """Run the full hold→generate→reconcile flow across a routing chain.

    `candidates` is the ordered [(primary), *fallback_chain] produced by
    select_route. We try each upstream in turn (mirroring the chat relay's
    failover), placing a per-attempt hold so a failing upstream never leaves
    funds locked. The first upstream that returns images wins; if all fail a
    single failure UsageLog (cost 0) is written. Commits the transaction.
    Returns (status_code, body) to hand back to the client.
    """
    if not candidates:
        raise ImageRelayError(502, "no upstream")

    size, quality, n, prompt_text = _parse_params(payload)
    last_status, last_body = 502, {"error": {"message": "no upstream"}}

    for model, channel in candidates:
        # The image wire protocol is chosen per-model (upstream_protocol), NOT
        # by the channel's chat provider_type — image APIs vary too much to
        # share one implicit protocol. Fall back to provider_type when unset.
        protocol = model.upstream_protocol or channel.provider_type
        adapter = providers.get_image_adapter(protocol)
        if not adapter:
            last_status, last_body = 502, {"error": {"message": f"no image adapter for protocol {protocol}"}}
            continue

        estimate, meta = quote_image_cost_cents(
            model, size=size, quality=quality, n=n, prompt_text=prompt_text
        )
        meta["requested_n"] = n

        # Gate before holding so the pre-deduction can never drive a balance
        # negative (images are expensive relative to a single chat call).
        if estimate > 0:
            avail = await available_cents(db, user)
            if avail < estimate:
                raise ImageRelayError(402, "insufficient balance for image request")

        # 1) HOLD the worst-case estimate up front (per attempt).
        breakdown = await charge_user(
            db, user, api_key, estimate, ref_id=request_id,
            note=f"{user_facing_model} [image-hold]",
        )

        # 2) Call upstream.
        started = time.time()
        status_code, body = await adapter.images(channel, model.upstream_model, payload)
        latency_ms = int((time.time() - started) * 1000)

        images_returned = 0
        if status_code == 200 and isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                images_returned = len(data)

        if status_code == 200 and images_returned > 0:
            # 3) Success → compute actual cost and reconcile.
            if meta.get("mode") == "token" and isinstance(body.get("usage"), dict):
                u = body["usage"]
                in_tok = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
                out_tok = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
                micro = in_tok * (model.prompt_rate or 0) + out_tok * (model.completion_rate or 0)
                actual = ceil(micro / 10_000_000)
            else:
                actual, _ = quote_image_cost_cents(
                    model, size=size, quality=quality, n=images_returned, prompt_text=prompt_text
                )
            if actual < estimate:
                await refund_to_sources(
                    db, user, api_key, breakdown, estimate - actual,
                    ref_id=request_id, note=f"{user_facing_model} [image-refund]",
                )
            elif actual > estimate:
                await charge_user(
                    db, user, api_key, actual - estimate, ref_id=request_id,
                    note=f"{user_facing_model} [image-adjust]",
                )
            meta["images_returned"] = images_returned
            db.add(UsageLog(
                user_id=user.id,
                api_key_id=(api_key.id if api_key else None),
                model_id=model.id,
                user_facing_model=user_facing_model,
                upstream_model=model.upstream_model,
                prompt_tokens=0,
                completion_tokens=0,
                cost_cents=actual,
                image_count=images_returned,
                meta_jsonb=meta,
                latency_ms=latency_ms,
                status="ok",
                request_id=request_id,
                kind="relay",
                resolved_label=resolved_label,
            ))
            db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
            await db.commit()
            return status_code, body

        # 4) Failure/timeout/zero images → fully refund this attempt's hold
        #    and try the next candidate in the chain.
        if estimate > 0:
            await refund_to_sources(
                db, user, api_key, breakdown, estimate,
                ref_id=request_id, note=f"{user_facing_model} [image-refund]",
            )
        last_status, last_body = status_code, body

    # All candidates failed — record one failure log (cost 0) and surface
    # the last upstream error to the caller.
    db.add(UsageLog(
        user_id=user.id,
        api_key_id=(api_key.id if api_key else None),
        model_id=candidates[0][0].id,
        user_facing_model=user_facing_model,
        upstream_model=candidates[0][0].upstream_model,
        prompt_tokens=0,
        completion_tokens=0,
        cost_cents=0,
        image_count=0,
        meta_jsonb={"size": size, "quality": quality, "requested_n": n, "images_returned": 0},
        latency_ms=0,
        status="timeout" if last_status == 504 else "error",
        request_id=request_id,
        kind="relay",
        resolved_label=resolved_label,
    ))
    db.info.setdefault("_quota_invalidate_uids", set()).add(user.id)
    await db.commit()
    return last_status, last_body
