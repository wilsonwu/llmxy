"""gRPC AccessLogService — receives access log streams from Envoy and
charges users + writes UsageLog asynchronously.

Envoy calls path /envoy.service.accesslog.v3.AccessLogService/StreamAccessLogs;
we serve under that exact path via a generic handler so we don't need to
vendor the upstream proto package — see `register_path` below.

Auth model: same as xds_server — shared static token via gRPC metadata
`x-llmxy-token` (settings.XDS_AUTH_TOKEN), plus identifier.node.id must
match an existing envoy_instances row. Local envoys (loopback) skip both
checks when the token is unset (dev mode).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import grpc
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import EnvoyInstance, Model, UsageLog
from app.services.billing import calc_cost_cents, charge_user
from app.services.envoy.protos import als_pb2  # noqa: F401  (ensures compile)

log = logging.getLogger(__name__)

ENVOY_SERVICE = "envoy.service.accesslog.v3.AccessLogService"
METHOD = "StreamAccessLogs"
FULL_PATH = f"/{ENVOY_SERVICE}/{METHOD}"

_TOKEN_METADATA_KEY = "x-llmxy-token"


def _hdr(headers: dict, name: str) -> str | None:
    """Case-insensitive map lookup."""
    if not headers:
        return None
    v = headers.get(name)
    if v is not None:
        return v
    lower = name.lower()
    for k, val in headers.items():
        if k.lower() == lower:
            return val
    return None


def _check_token(context: grpc.aio.ServicerContext) -> bool:
    raw = settings.XDS_AUTH_TOKEN or ""
    accepted = {t.strip() for t in raw.split(",") if t.strip()}
    if not accepted:
        return True
    md = dict(context.invocation_metadata() or [])
    return md.get(_TOKEN_METADATA_KEY) in accepted


async def _node_exists(node_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        inst = (
            await db.execute(select(EnvoyInstance).where(EnvoyInstance.node_id == node_id))
        ).scalar_one_or_none()
        return inst is not None


def _extract_usage(entry) -> tuple[int, int]:
    """Read prompt/completion tokens from dynamic_metadata['llmxy.usage']."""
    try:
        fm = entry.common_properties.metadata.filter_metadata
        meta = fm.get("llmxy.usage")
        if not meta:
            return 0, 0
        fields = meta.fields
        pt = int(fields["prompt_tokens"].number_value) if "prompt_tokens" in fields else 0
        ct = int(fields["completion_tokens"].number_value) if "completion_tokens" in fields else 0
        return pt, ct
    except Exception as e:
        log.debug("usage extract failed: %s", e)
        return 0, 0


async def _write_usage_log_only(
    *, user_id: int, api_key_id: int | None, model_id: int | None,
    user_facing_model: str | None, upstream_model: str | None,
    prompt_tokens: int, completion_tokens: int, cost_cents: int,
    latency_ms: int, status: str, request_id: str,
    resolved_label: str | None = None,
) -> None:
    """Fallback path: write a UsageLog row in its own transaction. Used when
    the charge+log transaction rolled back so we still have an audit trail."""
    async with AsyncSessionLocal() as db:
        db.add(UsageLog(
            user_id=user_id, api_key_id=api_key_id, model_id=model_id,
            user_facing_model=user_facing_model, upstream_model=upstream_model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_cents=cost_cents, latency_ms=latency_ms,
            status=status, request_id=request_id,
            kind="relay", resolved_label=resolved_label,
        ))
        await db.commit()


async def _ingest_entry(entry) -> None:
    headers = dict(entry.request.request_headers or {})
    request_id = _hdr(headers, "x-llmxy-request-id") or "-"
    user_id = _hdr(headers, "x-llmxy-user-id")
    api_key_id = _hdr(headers, "x-llmxy-api-key-id")
    model_id = _hdr(headers, "x-llmxy-model-id")
    user_facing_model = _hdr(headers, "x-llmxy-user-facing-model")
    upstream_model = _hdr(headers, "x-llmxy-upstream-model")
    resolved_label = _hdr(headers, "x-llmxy-resolved-label")

    # Classifier overhead — ext_authz forwards these as headers so we can
    # write the classifier UsageLog + bill it in the SAME PG transaction as
    # the relay row. Without this, the two could split (ext_authz committed,
    # ALS never fired → orphan "classifier-only" rows).
    cls_model_id = _hdr(headers, "x-llmxy-classifier-model-id")
    cls_upstream = _hdr(headers, "x-llmxy-classifier-upstream-model")
    cls_prompt_tokens_raw = _hdr(headers, "x-llmxy-classifier-prompt-tokens")
    cls_latency_raw = _hdr(headers, "x-llmxy-classifier-latency-ms")
    cls_status = _hdr(headers, "x-llmxy-classifier-status")

    if not user_id or not model_id:
        # Likely a /v1/models listing or other non-billable call.
        return

    response_code = int(entry.response.response_code.value) if entry.response.HasField("response_code") else 0
    status_str = "ok" if 200 <= response_code < 300 else "error"
    duration_ms = 0
    if entry.common_properties.HasField("duration"):
        d = entry.common_properties.duration
        duration_ms = int(d.seconds * 1000 + d.nanos / 1_000_000)

    prompt_tokens, completion_tokens = _extract_usage(entry)
    if status_str == "ok" and prompt_tokens == 0 and completion_tokens == 0:
        # Successful response with no usage metadata = Lua extract miss. Either
        # the provider response body didn't carry `"usage": {...}` in OpenAI
        # shape (translator regression), or the SSE final chunk was emitted on
        # an unexpected content-type. Log so this is monitorable.
        log.warning(
            "ALS zero usage on 2xx rid=%s model=%s upstream=%s — check translator/Lua",
            request_id, user_facing_model, upstream_model,
        )

    uid = int(user_id)
    akid = int(api_key_id) if (api_key_id and api_key_id.isdigit()) else None
    mid = int(model_id) if model_id.isdigit() else None
    cls_mid = int(cls_model_id) if (cls_model_id and cls_model_id.isdigit()) else None
    cls_prompt_tokens = int(cls_prompt_tokens_raw) if (cls_prompt_tokens_raw and cls_prompt_tokens_raw.isdigit()) else 0
    cls_latency_ms = int(cls_latency_raw) if (cls_latency_raw and cls_latency_raw.isdigit()) else 0

    # Single transaction: relay charge + relay log + (optional) classifier
    # charge + classifier log, all together. On rollback we still want an
    # audit row, so we write a degraded UsageLog (status=error, cost=0) in a
    # fresh session as a best-effort tail.
    structured_breakdown: list[tuple[str, int, int]] = []
    window_start_epoch = 0
    total_cost = 0
    try:
        async with AsyncSessionLocal() as db:
            m = await db.get(Model, mid) if mid is not None else None
            relay_cost = calc_cost_cents(m, prompt_tokens, completion_tokens) if m else 0
            cls_m = await db.get(Model, cls_mid) if cls_mid is not None else None
            cls_cost = (
                calc_cost_cents(cls_m, cls_prompt_tokens, 0)
                if (cls_m and cls_status == "ok") else 0
            )
            # Only the relay's success status gates billing; classifier
            # always already ran (we paid the embedding provider) so we
            # bill it whenever cls_status == "ok", regardless of whether
            # the relay itself succeeded.
            billable_relay = relay_cost if status_str == "ok" else 0
            total_cost = billable_relay + cls_cost
            if total_cost > 0:
                from app.models import ApiKey, QuotaMode, User
                user = await db.get(User, uid)
                api_key = await db.get(ApiKey, akid) if akid is not None else None
                if user:
                    note = user_facing_model or ""
                    if cls_cost > 0 and billable_relay > 0:
                        note = f"{note} [relay+classifier]"
                    elif cls_cost > 0:
                        note = f"{note} [classifier]"
                    structured_breakdown = await charge_user(
                        db, user, api_key, total_cost, ref_id=request_id, note=note,
                    )
                    if api_key is not None:
                        if (
                            api_key.quota_mode == QuotaMode.periodic
                            and api_key.quota_period_start is not None
                        ):
                            window_start_epoch = int(api_key.quota_period_start.timestamp())
            db.add(UsageLog(
                user_id=uid, api_key_id=akid, model_id=mid,
                user_facing_model=user_facing_model, upstream_model=upstream_model,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cost_cents=billable_relay, latency_ms=duration_ms,
                status=status_str, request_id=request_id,
                kind="relay", resolved_label=resolved_label,
            ))
            if cls_mid is not None and cls_status:
                db.add(UsageLog(
                    user_id=uid, api_key_id=akid, model_id=cls_mid,
                    user_facing_model=user_facing_model, upstream_model=cls_upstream or None,
                    prompt_tokens=cls_prompt_tokens, completion_tokens=0,
                    cost_cents=cls_cost, latency_ms=cls_latency_ms,
                    status=cls_status, request_id=request_id,
                    kind="classifier", resolved_label=resolved_label,
                ))
            await db.commit()
        # After PG commit succeeds, mirror counters to Redis so the next
        # ext_authz read sees fresh numbers. Failure here is non-fatal —
        # the cache will self-heal on the next hydrate.
        if structured_breakdown:
            try:
                from app.services import quota_cache
                await quota_cache.apply_charge(
                    user_id=uid, key_id=akid, cost_cents=total_cost,
                    window_start_epoch=window_start_epoch,
                    breakdown=structured_breakdown,
                )
            except Exception as e:
                log.warning("quota_cache mirror failed rid=%s: %s", request_id, e)
    except Exception as e:
        log.warning(
            "ALS billing+log tx rolled back rid=%s user=%s err=%s; writing degraded log",
            request_id, uid, e,
        )
        try:
            await _write_usage_log_only(
                user_id=uid, api_key_id=akid, model_id=mid,
                user_facing_model=user_facing_model, upstream_model=upstream_model,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cost_cents=0, latency_ms=duration_ms,
                status="error", request_id=request_id,
                resolved_label=resolved_label,
            )
        except Exception as e2:
            log.error("ALS degraded log write also failed rid=%s: %s", request_id, e2)


async def _stream_handler(request_iterator, context):
    """Bidi-streaming impl. Envoy sends many StreamAccessLogsMessage frames
    on one stream. Auth happens on the first frame: token in metadata and
    identifier.node.id must match a registered EnvoyInstance.
    """
    if not _check_token(context):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid x-llmxy-token")
        return

    node_id_from_msg: str | None = None
    authed = False
    async for msg in request_iterator:
        if not authed:
            if msg.HasField("identifier") and msg.identifier.node and msg.identifier.node.id:
                node_id_from_msg = msg.identifier.node.id
                if not await _node_exists(node_id_from_msg):
                    await context.abort(
                        grpc.StatusCode.PERMISSION_DENIED,
                        f"unknown node {node_id_from_msg}",
                    )
                    return
                authed = True
        if not msg.HasField("http_logs"):
            continue
        for entry in msg.http_logs.log_entry:
            try:
                await _ingest_entry(entry)
            except Exception as e:
                log.warning("als ingest entry failed: %s", e)

    if node_id_from_msg:
        try:
            async with AsyncSessionLocal() as db:
                inst = (
                    await db.execute(
                        select(EnvoyInstance).where(EnvoyInstance.node_id == node_id_from_msg)
                    )
                ).scalar_one_or_none()
                if inst is not None:
                    inst.last_seen_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e:
            log.debug("touch last_seen failed for node %s: %s", node_id_from_msg, e)
    return als_pb2.StreamAccessLogsResponse()


def _build_generic_handler() -> grpc.GenericRpcHandler:
    """Register our stream handler at envoy's expected service path."""
    method_handler = grpc.stream_unary_rpc_method_handler(
        _stream_handler,
        request_deserializer=als_pb2.StreamAccessLogsMessage.FromString,
        response_serializer=als_pb2.StreamAccessLogsResponse.SerializeToString,
    )
    return grpc.method_handlers_generic_handler(
        ENVOY_SERVICE,
        {METHOD: method_handler},
    )


_server: grpc.aio.Server | None = None


async def start() -> None:
    """Single plaintext gRPC listener on 0.0.0.0:ALS_GRPC_PORT — accepts
    both local envoys (loopback) and remote envoys (token-protected)."""
    global _server
    if _server is not None:
        return
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((_build_generic_handler(),))
    bind = f"0.0.0.0:{settings.ALS_GRPC_PORT}"
    server.add_insecure_port(bind)
    await server.start()
    _server = server
    auth = "token-protected" if settings.XDS_AUTH_TOKEN else "OPEN (XDS_AUTH_TOKEN unset)"
    log.info("ALS gRPC server listening on %s (%s)", bind, auth)


async def stop() -> None:
    global _server
    if _server is not None:
        await _server.stop(grace=2.0)
        _server = None
