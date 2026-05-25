"""gRPC AccessLogService — receives access log streams from Envoy and
charges users + writes UsageLog asynchronously.

Envoy calls path /envoy.service.accesslog.v3.AccessLogService/StreamAccessLogs;
we serve under that exact path via a generic handler so we don't need to
vendor the upstream proto package — see `register_path` below.
"""
from __future__ import annotations

import asyncio
import logging
import time

import grpc

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import Model, UsageLog
from app.services.billing import calc_cost_cents, charge_user
from app.services.envoy.protos import als_pb2  # noqa: F401  (ensures compile)

log = logging.getLogger(__name__)

ENVOY_SERVICE = "envoy.service.accesslog.v3.AccessLogService"
METHOD = "StreamAccessLogs"
FULL_PATH = f"/{ENVOY_SERVICE}/{METHOD}"


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


def _extract_usage(entry) -> tuple[int, int]:
    """Read prompt/completion tokens from dynamic_metadata['llmxy.usage']."""
    try:
        meta = entry.common_properties.metadata.filter_metadata.get("llmxy.usage")
        if not meta:
            return 0, 0
        fields = meta.fields
        pt = int(fields["prompt_tokens"].number_value) if "prompt_tokens" in fields else 0
        ct = int(fields["completion_tokens"].number_value) if "completion_tokens" in fields else 0
        return pt, ct
    except Exception as e:
        log.debug("usage extract failed: %s", e)
        return 0, 0


async def _ingest_entry(entry) -> None:
    headers = dict(entry.request.request_headers or {})
    request_id = _hdr(headers, "x-llmxy-request-id") or "-"
    user_id = _hdr(headers, "x-llmxy-user-id")
    api_key_id = _hdr(headers, "x-llmxy-api-key-id")
    model_id = _hdr(headers, "x-llmxy-model-id")
    user_facing_model = _hdr(headers, "x-llmxy-user-facing-model")
    upstream_model = _hdr(headers, "x-llmxy-upstream-model")

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

    async with AsyncSessionLocal() as db:
        m = await db.get(Model, int(model_id)) if model_id.isdigit() else None
        cost = calc_cost_cents(m, prompt_tokens, completion_tokens) if m else 0
        if cost > 0 and status_str == "ok":
            # Need a User & ApiKey for charge_user — small extra fetch.
            from app.models import ApiKey, User
            user = await db.get(User, int(user_id))
            api_key = await db.get(ApiKey, int(api_key_id)) if (api_key_id and api_key_id.isdigit()) else None
            if user:
                try:
                    await charge_user(db, user, api_key, cost, ref_id=request_id, note=user_facing_model)
                except Exception as e:
                    log.warning("charge_user failed rid=%s: %s", request_id, e)
        db.add(UsageLog(
            user_id=int(user_id),
            api_key_id=int(api_key_id) if (api_key_id and api_key_id.isdigit()) else None,
            model_id=int(model_id) if model_id.isdigit() else None,
            user_facing_model=user_facing_model,
            upstream_model=upstream_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cents=cost,
            latency_ms=duration_ms,
            status=status_str,
            request_id=request_id,
        ))
        await db.commit()


async def _stream_handler(request_iterator, context):
    """Bidi-streaming impl. Envoy sends many StreamAccessLogsMessage frames
    on one stream; we don't need to send anything back until the stream ends."""
    async for msg in request_iterator:
        if not msg.HasField("http_logs"):
            continue
        for entry in msg.http_logs.log_entry:
            try:
                await _ingest_entry(entry)
            except Exception as e:
                log.warning("als ingest entry failed: %s", e)
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
    global _server
    if _server is not None:
        return
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((_build_generic_handler(),))
    bind = f"{settings.INTERNAL_API_HOST}:{settings.ALS_GRPC_PORT}"
    server.add_insecure_port(bind)
    await server.start()
    _server = server
    log.info("ALS gRPC server listening on %s", bind)


async def stop() -> None:
    global _server
    if _server is None:
        return
    await _server.stop(grace=2.0)
    _server = None
