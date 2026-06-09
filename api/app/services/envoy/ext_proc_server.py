from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import grpc
from envoy.config.core.v3 import base_pb2
from envoy.service.ext_proc.v3 import external_processor_pb2 as epb
from envoy.service.ext_proc.v3 import external_processor_pb2_grpc as epb_grpc
from envoy.type.v3 import http_status_pb2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_api_key
from app.db.session import AsyncSessionLocal
from app.models import Channel, KeyStatus, Model, RoutePolicy, RouteScope, UserStatus
from app.services import api_key_cache, providers, quota_cache
from app.services.protocols.chat import anthropic_to_openai_payload, route_exposes
from app.services.quota import rate_limit

log = logging.getLogger(__name__)

_TOKEN_METADATA_KEY = "x-llmxy-token"


def _headers_map(req: epb.ProcessingRequest) -> dict[str, str]:
    if not req.HasField("request_headers"):
        return {}
    out: dict[str, str] = {}
    for h in req.request_headers.headers.headers:
        value = h.value
        raw_value = getattr(h, "raw_value", b"") or b""
        if not value and raw_value:
            value = raw_value.decode("utf-8", errors="replace")
        out[h.key.lower()] = value
    return out


def _header(name: str, value: str) -> base_pb2.HeaderValueOption:
    return base_pb2.HeaderValueOption(
        header=base_pb2.HeaderValue(key=name, raw_value=value.encode("utf-8")),
        append_action=base_pb2.HeaderValueOption.OVERWRITE_IF_EXISTS_OR_ADD,
    )


def _continue_headers() -> epb.ProcessingResponse:
    return epb.ProcessingResponse(
        request_headers=epb.HeadersResponse(
            response=epb.CommonResponse(status=epb.CommonResponse.CONTINUE)
        )
    )


def _mutate_headers(headers: dict[str, str]) -> epb.ProcessingResponse:
    mutation = epb.HeaderMutation()
    for k, v in headers.items():
        mutation.set_headers.append(_header(k, v))
    return epb.ProcessingResponse(
        request_headers=epb.HeadersResponse(
            response=epb.CommonResponse(
                status=epb.CommonResponse.CONTINUE,
                header_mutation=mutation,
                clear_route_cache=True,
            )
        )
    )


def _continue_body(headers: dict[str, str]) -> epb.ProcessingResponse:
    mutation = epb.HeaderMutation()
    for k, v in headers.items():
        mutation.set_headers.append(_header(k, v))
    return epb.ProcessingResponse(
        request_body=epb.BodyResponse(
            response=epb.CommonResponse(
                status=epb.CommonResponse.CONTINUE,
                header_mutation=mutation,
                clear_route_cache=True,
            )
        )
    )


def _deny(code: int, message: str) -> epb.ProcessingResponse:
    body = json.dumps({"error": {"message": message}}, separators=(",", ":")).encode("utf-8")
    headers = epb.HeaderMutation()
    headers.set_headers.append(_header("content-type", "application/json"))
    return epb.ProcessingResponse(
        immediate_response=epb.ImmediateResponse(
            status=http_status_pb2.HttpStatus(code=code),
            headers=headers,
            body=body,
        )
    )


def _is_cors_preflight(headers: dict[str, str]) -> bool:
    return (
        (headers.get(":method") or "").upper() == "OPTIONS"
        and bool(headers.get("origin"))
        and bool(headers.get("access-control-request-method"))
    )


def _cors_preflight(headers: dict[str, str]) -> epb.ProcessingResponse:
    origin = headers.get("origin") or "*"
    req_headers = headers.get("access-control-request-headers") or "authorization,content-type,x-api-key,anthropic-version"
    response_headers = epb.HeaderMutation()
    for name, value in {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET,POST,OPTIONS",
        "access-control-allow-headers": req_headers,
        "access-control-allow-credentials": "true",
        "access-control-max-age": "600",
        "vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
    }.items():
        response_headers.set_headers.append(_header(name, value))
    if (headers.get("access-control-request-private-network") or "").lower() == "true":
        response_headers.set_headers.append(_header("access-control-allow-private-network", "true"))
    return epb.ProcessingResponse(
        immediate_response=epb.ImmediateResponse(
            status=http_status_pb2.HttpStatus(code=204),
            headers=response_headers,
        )
    )


def _auth_plain(headers: dict[str, str]) -> str | None:
    x_api_key = (headers.get("x-api-key") or headers.get("api-key") or "").strip()
    if x_api_key:
        return x_api_key
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def _client_protocol_and_modality(path: str) -> tuple[str, str | None]:
    clean = path.split("?", 1)[0]
    if clean.endswith("/v1/messages") or clean == "/v1/messages":
        return "anthropic.messages", "chat"
    if clean.endswith("/v1/chat/completions") or clean == "/v1/chat/completions":
        return "openai.chat", "chat"
    if clean.endswith("/v1/responses") or clean == "/v1/responses":
        return "openai.responses", "chat"
    if clean.endswith("/v1/embeddings") or clean == "/v1/embeddings":
        return "openai.embeddings", "embedding"
    if clean.endswith("/v1/images/generations") or clean == "/v1/images/generations":
        return "openai.images", "image"
    return "openai.chat", None


def _extract_model(body: bytes) -> str | None:
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("model"), str):
        return data["model"]
    return None


def _client_ip(headers: dict[str, str]) -> str | None:
    xff = headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",", 1)[0].strip() or None
    return (headers.get("x-real-ip") or "").strip() or None


async def _load_route(db: AsyncSession, user_facing_model: str) -> tuple[RoutePolicy, dict[int, Model], dict[int, Channel]]:
    policy = (
        await db.execute(select(RoutePolicy).where(RoutePolicy.user_facing_model == user_facing_model))
    ).scalar_one_or_none()
    if not policy or not policy.enabled or policy.scope == RouteScope.private:
        raise ValueError(f"model {user_facing_model} not available")
    target_ids = [int(t["model_id"]) for t in (policy.targets_jsonb or [])]
    if not target_ids:
        raise RuntimeError("route has no targets")
    models = (await db.execute(select(Model).where(Model.id.in_(target_ids)))).scalars().all()
    models_by_id = {m.id: m for m in models}
    channel_ids = {m.channel_id for m in models}
    channels = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars().all()
    return policy, models_by_id, {c.id: c for c in channels}


def _prompt_text(client_protocol: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if client_protocol == "anthropic.messages":
        return providers.extract_prompt_text(anthropic_to_openai_payload(payload))
    return providers.extract_prompt_text(payload)


async def _decide(headers: dict[str, str], body: bytes) -> tuple[int, str] | dict[str, str]:
    plain = _auth_plain(headers)
    if not plain:
        return 401, "missing api key; send Authorization: Bearer sk-... or x-api-key: sk-..."
    if not plain.startswith("sk-"):
        return 401, "invalid api key format"
    snap = await api_key_cache.get_apikey_snapshot(hash_api_key(plain))
    if snap is None:
        return 401, "invalid api key"
    from app.services.api_key import enforce_key_state_cached
    snap = await enforce_key_state_cached(snap)
    if snap.status != KeyStatus.active:
        return 401, f"api key {snap.status.value}"
    user = await api_key_cache.get_user_snapshot(snap.user_id)
    if user is None or user.status != UserStatus.active:
        return 401, "user disabled"
    window_start_epoch = quota_cache.window_start_epoch_for(snap)
    ok, msg = await quota_cache.has_quota_fast(snap.user_id, snap.id, snap.quota_cents, window_start_epoch)
    if not ok:
        return 402, msg
    if not await rate_limit(snap.user_id, per_min=user.plan_rpm):
        return 429, "rate limit exceeded"

    path = headers.get(":path") or ""
    client_protocol, expected_modality = _client_protocol_and_modality(path)
    request_id = headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
    if path.split("?", 1)[0].rstrip("/") == "/v1/models":
        return {
            "x-llmxy-cluster": "translator",
            "x-llmxy-request-id": request_id,
            "x-llmxy-user-id": str(snap.user_id),
            "x-llmxy-api-key-id": str(snap.id),
            "x-llmxy-client-protocol": client_protocol,
            "x-llmxy-billed-sync": "true",
        }

    model_name = _extract_model(body)
    if not model_name:
        return 400, "missing model in body"
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        return 400, "invalid json body"

    async with AsyncSessionLocal() as db:
        try:
            policy, models_by_id, channels_by_id = await _load_route(db, model_name)
        except ValueError as e:
            return 404, str(e)
        except RuntimeError as e:
            return 502, str(e)
        if expected_modality is not None and (policy.modality or "chat") != expected_modality:
            return 404, f"model {model_name} is not available on the {expected_modality} endpoint"
        if not route_exposes(policy, client_protocol):
            return 404, f"model {model_name} is not available on the {client_protocol} protocol"
        decision = await providers.select_route(
            policy,
            models_by_id,
            channels_by_id,
            prompt_text=_prompt_text(client_protocol, payload),
            client_ip=_client_ip(headers),
            db=db,
        )
        if not decision:
            return 502, "no available upstream"

        m, c = decision.model, decision.channel
        pairs = [(m, c)] + (decision.fallback_chain or [])
        eff_protocol = providers.resolve_upstream_protocol(m, c)
        connector = providers.resolve_connector_type(m, c)
        out = {
            "x-llmxy-cluster": "translator",
            "x-llmxy-request-id": request_id,
            "x-llmxy-user-id": str(snap.user_id),
            "x-llmxy-api-key-id": str(snap.id),
            "x-llmxy-model-id": str(m.id),
            "x-llmxy-user-facing-model": model_name,
            "x-llmxy-upstream-model": m.upstream_model,
            "x-llmxy-provider-type": (c.provider_type or "").lower(),
            "x-llmxy-connector-type": connector,
            "x-llmxy-upstream-protocol": eff_protocol,
            "x-llmxy-channel-id": str(c.id),
            "x-llmxy-client-protocol": client_protocol,
            "x-llmxy-billed-sync": "true",
        }
        if expected_modality == "chat":
            out["x-llmxy-chat-chain"] = ",".join(f"{mm.id}:{cc.id}" for mm, cc in pairs)
        if decision.chosen_label:
            out["x-llmxy-resolved-label"] = decision.chosen_label
        if m.kind == "image":
            out["x-llmxy-image-chain"] = ",".join(f"{mm.id}:{cc.id}" for mm, cc in pairs)
        eu = getattr(decision, "embedding_usage", None)
        if eu is not None:
            out["x-llmxy-classifier-model-id"] = str(eu.model.id)
            out["x-llmxy-classifier-upstream-model"] = eu.upstream_model or ""
            out["x-llmxy-classifier-prompt-tokens"] = str(int(eu.prompt_tokens or 0))
            out["x-llmxy-classifier-latency-ms"] = str(int(eu.latency_ms or 0))
            out["x-llmxy-classifier-status"] = eu.status or "ok"
        return out


def _check_token(context: grpc.aio.ServicerContext) -> bool:
    raw = settings.XDS_AUTH_TOKEN or ""
    accepted = {t.strip() for t in raw.split(",") if t.strip()}
    if not accepted:
        return True
    md = dict(context.invocation_metadata() or [])
    return md.get(_TOKEN_METADATA_KEY) in accepted


class _ExternalProcessor(epb_grpc.ExternalProcessorServicer):
    async def Process(self, request_iterator, context):  # type: ignore[override]
        if not _check_token(context):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid x-llmxy-token")
            return
        headers: dict[str, str] = {}
        body = b""
        async for req in request_iterator:
            if req.HasField("request_headers"):
                headers = _headers_map(req)
                if _is_cors_preflight(headers):
                    yield _cors_preflight(headers)
                    return
                if req.request_headers.end_of_stream:
                    decision = await _decide(headers, b"")
                    if isinstance(decision, tuple):
                        code, message = decision
                        yield _deny(code, message)
                        return
                    yield _mutate_headers(decision)
                    continue
                yield _continue_headers()
                continue
            if req.HasField("request_body"):
                body += bytes(req.request_body.body or b"")
                if not req.request_body.end_of_stream:
                    yield epb.ProcessingResponse(
                        request_body=epb.BodyResponse(response=epb.CommonResponse(status=epb.CommonResponse.CONTINUE))
                    )
                    continue
                decision = await _decide(headers, body)
                if isinstance(decision, tuple):
                    code, message = decision
                    yield _deny(code, message)
                    return
                yield _continue_body(decision)
                continue
            yield epb.ProcessingResponse()


_server: grpc.aio.Server | None = None


async def start() -> None:
    global _server
    if _server is not None:
        return
    server = grpc.aio.server()
    epb_grpc.add_ExternalProcessorServicer_to_server(_ExternalProcessor(), server)
    bind = f"0.0.0.0:{settings.EXT_PROC_GRPC_PORT}"
    server.add_insecure_port(bind)
    await server.start()
    _server = server
    auth = "token-protected" if settings.XDS_AUTH_TOKEN else "OPEN (XDS_AUTH_TOKEN unset)"
    log.info("Envoy ext_proc gRPC server listening on %s (%s)", bind, auth)


async def stop() -> None:
    global _server
    if _server is None:
        return
    await _server.stop(grace=2.0)
    _server = None