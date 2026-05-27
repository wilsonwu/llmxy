"""ADS (Aggregated Discovery Service) gRPC server — serves CDS/RDS/LDS
to remote Envoy nodes over plaintext gRPC.

Auth: shared static token via gRPC metadata `x-llmxy-token` (settings.XDS_AUTH_TOKEN)
plus node_id match — the node.id in the first DiscoveryRequest must exist in
envoy_instances with mode=remote. Empty XDS_AUTH_TOKEN disables the token
check (dev mode).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import grpc
from google.protobuf import any_pb2
from google.protobuf import json_format
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import Channel, EnvoyInstance, EnvoyMode
from app.services.envoy import config as envoy_config

log = logging.getLogger(__name__)

from envoy.service.discovery.v3 import (  # type: ignore
    ads_pb2_grpc,
    discovery_pb2,
)
from envoy.config.cluster.v3 import cluster_pb2  # type: ignore
from envoy.config.listener.v3 import listener_pb2  # type: ignore
from envoy.config.route.v3 import route_pb2  # type: ignore
from envoy.extensions.transport_sockets.tls.v3 import tls_pb2  # noqa: F401 (register descriptor)
from envoy.extensions.filters.network.http_connection_manager.v3 import (  # noqa: F401
    http_connection_manager_pb2,
)
from envoy.extensions.filters.http.ext_authz.v3 import ext_authz_pb2  # noqa: F401
from envoy.extensions.filters.http.lua.v3 import lua_pb2  # noqa: F401
from envoy.extensions.filters.http.router.v3 import router_pb2  # noqa: F401
from envoy.extensions.access_loggers.grpc.v3 import als_pb2  # noqa: F401
from envoy.extensions.upstreams.http.v3 import http_protocol_options_pb2  # noqa: F401

TYPE_URL_CLUSTER = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
TYPE_URL_LISTENER = "type.googleapis.com/envoy.config.listener.v3.Listener"
TYPE_URL_ROUTE = "type.googleapis.com/envoy.config.route.v3.RouteConfiguration"

_TYPE_TO_PROTO: dict[str, type] = {
    TYPE_URL_CLUSTER: cluster_pb2.Cluster,
    TYPE_URL_LISTENER: listener_pb2.Listener,
    TYPE_URL_ROUTE: route_pb2.RouteConfiguration,
}

_TOKEN_METADATA_KEY = "x-llmxy-token"


# --- per-node push notification ---------------------------------------------
_node_events: dict[str, asyncio.Event] = {}
_node_loops: dict[str, asyncio.AbstractEventLoop] = {}

# Redis pub/sub channel: multi-replica deployments use this to broadcast
# notify_node() to every api process — only the one holding the live ADS
# stream for that node_id will wake up (others no-op via empty _node_events).
_NOTIFY_CHANNEL = "llmxy:xds:notify"
_pubsub_task: asyncio.Task | None = None


def _local_notify(node_id: str) -> None:
    evt = _node_events.get(node_id)
    loop = _node_loops.get(node_id)
    if evt is None or loop is None:
        return
    loop.call_soon_threadsafe(evt.set)


def notify_node(node_id: str) -> None:
    """Wake the ADS stream serving `node_id` so it re-pushes config.
    Locally signals this replica, and (if Redis is configured) publishes to
    every other api replica so whichever one owns the stream wakes up too.
    Safe to call from any thread / event loop.
    """
    _local_notify(node_id)
    try:
        from app.core.redis import get_redis
        r = get_redis()
        # Fire-and-forget. We don't await here because callers may be sync
        # context (e.g. admin HTTP handler post-commit hook); the schedule
        # happens on the current loop.
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(r.publish(_NOTIFY_CHANNEL, node_id))
        except RuntimeError:
            # No running loop (called from sync test) — skip broadcast.
            pass
    except Exception as e:
        log.debug("xds notify_node: redis publish skipped: %s", e)


async def _pubsub_listener() -> None:
    """Subscribe to the notify channel and forward each message to local
    streams. Runs forever; reconnects on transient redis errors."""
    from app.core.redis import get_redis
    backoff = 1.0
    while True:
        try:
            r = get_redis()
            ps = r.pubsub()
            await ps.subscribe(_NOTIFY_CHANNEL)
            log.info("xds pubsub subscribed to %s", _NOTIFY_CHANNEL)
            backoff = 1.0
            async for msg in ps.listen():
                if msg is None or msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                if isinstance(data, str) and data:
                    _local_notify(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("xds pubsub listener error (retry in %ss): %s", backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


# --- resource rendering -----------------------------------------------------

def _render_lds_remote(inst: EnvoyInstance) -> dict[str, Any]:
    public_host = settings.CONTROL_PLANE_PUBLIC_HOST or "127.0.0.1"
    public_port = settings.API_PORT
    lds = envoy_config.render_lds(inst)
    # Force remote listeners onto the FIXED bind port baked into the manifest
    # (containerPort). inst.listen_port for remote rows holds the externally
    # reachable port the operator typed in (NodePort for k8s, host port for
    # docker --network=host) — driving envoy's bind off of that would either
    # collide with the manifest's containerPort or fail to bind entirely.
    from app.services.envoy.remote_bootstrap import REMOTE_BIND_LISTEN_PORT
    for res in lds.get("resources", []):
        sock = res.get("address", {}).get("socket_address")
        if sock:
            sock["port_value"] = REMOTE_BIND_LISTEN_PORT
        for fc in res.get("filter_chains", []):
            for f in fc.get("filters", []):
                tc = f.get("typed_config", {})
                if "rds" in tc:
                    tc["rds"]["config_source"] = {
                        "ads": {},
                        "resource_api_version": "V3",
                    }
                for al in tc.get("access_log", []) or []:
                    al_tc = al.get("typed_config", {})
                    cc = al_tc.get("common_config", {})
                    gs = cc.get("grpc_service", {})
                    eg = gs.get("envoy_grpc", {})
                    if eg.get("cluster_name") == "als":
                        eg["cluster_name"] = "als_cluster"
                for hf in tc.get("http_filters", []) or []:
                    htc = hf.get("typed_config", {})
                    http_svc = htc.get("http_service")
                    if http_svc:
                        http_svc["server_uri"]["uri"] = f"http://{public_host}:{public_port}"
                        http_svc["server_uri"]["cluster"] = "ext_authz"
    return lds


def _render_cds_remote(channels: list[Channel]) -> dict[str, Any]:
    public_host = settings.CONTROL_PLANE_PUBLIC_HOST or "127.0.0.1"
    public_port = settings.API_PORT
    cds = envoy_config.render_cds(channels)
    kept: list[dict[str, Any]] = []
    for res in cds.get("resources", []):
        name = res.get("name")
        if name == "als":
            continue
        if name in ("translator", "ext_authz"):
            try:
                ep = res["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]
                ep["address"] = public_host
                ep["port_value"] = public_port
            except Exception:
                pass
        kept.append(res)
    return {"resources": kept}


async def _build_resources(node_id: str) -> dict[str, list[any_pb2.Any]]:
    async with AsyncSessionLocal() as db:
        inst = (
            await db.execute(select(EnvoyInstance).where(EnvoyInstance.node_id == node_id))
        ).scalar_one_or_none()
        if inst is None:
            return {TYPE_URL_CLUSTER: [], TYPE_URL_LISTENER: [], TYPE_URL_ROUTE: []}
        channels = (await db.execute(select(Channel).order_by(Channel.id))).scalars().all()
        cds_dict = _render_cds_remote(channels)
        rds_dict = envoy_config.render_rds()
        lds_dict = _render_lds_remote(inst)

    def _pack(dicts: list[dict[str, Any]], type_url: str) -> list[any_pb2.Any]:
        proto_cls = _TYPE_TO_PROTO[type_url]
        out: list[any_pb2.Any] = []
        for d in dicts:
            d = {k: v for k, v in d.items() if k != "@type"}
            msg = proto_cls()
            json_format.ParseDict(d, msg, ignore_unknown_fields=True)
            any_msg = any_pb2.Any()
            any_msg.Pack(msg, type_url_prefix="type.googleapis.com")
            out.append(any_msg)
        return out

    return {
        TYPE_URL_CLUSTER: _pack(cds_dict.get("resources", []), TYPE_URL_CLUSTER),
        TYPE_URL_LISTENER: _pack(lds_dict.get("resources", []), TYPE_URL_LISTENER),
        TYPE_URL_ROUTE: _pack(rds_dict.get("resources", []), TYPE_URL_ROUTE),
    }


# --- auth ------------------------------------------------------------------

def _accepted_tokens() -> set[str]:
    """Comma-separated XDS_AUTH_TOKEN supports rotation: deploy with both old
    and new tokens listed, roll envoys onto the new one, then drop the old.
    Empty string disables the check entirely (dev mode)."""
    raw = settings.XDS_AUTH_TOKEN or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def check_token(context: grpc.aio.ServicerContext) -> bool:
    """Return True if the gRPC metadata satisfies XDS_AUTH_TOKEN. An empty
    setting always returns True (dev-mode bypass)."""
    accepted = _accepted_tokens()
    if not accepted:
        return True
    md = dict(context.invocation_metadata() or [])
    return md.get(_TOKEN_METADATA_KEY) in accepted


async def _authn_node(node_id: str) -> EnvoyInstance | None:
    async with AsyncSessionLocal() as db:
        inst = (
            await db.execute(select(EnvoyInstance).where(EnvoyInstance.node_id == node_id))
        ).scalar_one_or_none()
        if inst is None or inst.mode != EnvoyMode.remote:
            return None
        return inst


async def _touch_seen(node_id: str, version: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        inst = (
            await db.execute(select(EnvoyInstance).where(EnvoyInstance.node_id == node_id))
        ).scalar_one_or_none()
        if inst is None:
            return
        inst.last_seen_at = datetime.now(timezone.utc)
        if version is not None:
            inst.last_xds_version = version
        await db.commit()


# --- ADS service -----------------------------------------------------------

class _ADSService(ads_pb2_grpc.AggregatedDiscoveryServiceServicer):
    async def StreamAggregatedResources(self, request_iterator, context):  # type: ignore[override]
        if not check_token(context):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid x-llmxy-token")
            return

        # Read the first request to learn node.id, then proceed.
        first = None
        async for req in request_iterator:
            first = req
            break
        if first is None:
            return
        node_id = first.node.id if first.node else ""
        if not node_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "node.id is required")
            return
        inst = await _authn_node(node_id)
        if inst is None:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, f"unknown remote node {node_id}")
            return

        loop = asyncio.get_running_loop()
        evt = asyncio.Event()
        _node_events[node_id] = evt
        _node_loops[node_id] = loop
        log.info("ads stream opened: node=%s peer=%s", node_id, context.peer())

        # Per-type state. version is a content hash so identical resources
        # ALWAYS produce identical version_info — envoy then ACKs without the
        # "skipped N unmodified" churn. nonce is fresh per push to satisfy
        # ADS protocol's request/response correlation.
        sent_version: dict[str, str] = {}
        sent_nonce: dict[str, str] = {}
        subscribed: set[str] = {first.type_url} if first.type_url else set()
        # Types that need a fresh push (newly subscribed, or external notify).
        # `None` sentinel = push all subscribed types (external notify / startup).
        pending: set[str] = set(subscribed)

        def _process_request(req) -> None:
            """Classify a DiscoveryRequest. Only NEW subscriptions or NACKs
            mark a type as pending — pure ACKs are no-ops (without this, every
            ACK triggers a re-push which envoy re-ACKs forever)."""
            type_url = req.type_url
            if not type_url:
                return
            is_new_sub = type_url not in subscribed
            subscribed.add(type_url)
            has_error = bool(req.error_detail and req.error_detail.message)
            if has_error:
                log.warning(
                    "ads NACK from %s for %s: %s",
                    node_id, type_url, req.error_detail.message,
                )
                # NACK: envoy rejected last push. Drop sent_version so the
                # next push (after operator fixes config) re-sends even if
                # content unchanged.
                sent_version.pop(type_url, None)
                pending.add(type_url)
                evt.set()
                return
            response_nonce = req.response_nonce or ""
            last_nonce = sent_nonce.get(type_url, "")
            if response_nonce and response_nonce == last_nonce:
                # Pure ACK of our last push. No work.
                return
            if is_new_sub or not response_nonce:
                pending.add(type_url)
                evt.set()

        async def _reader():
            try:
                async for req in request_iterator:
                    _process_request(req)
            except Exception as e:
                log.info("ads reader for %s ended: %s", node_id, e)

        reader_task = asyncio.create_task(_reader())

        def _hash(any_list) -> str:
            h = hashlib.sha256()
            for a in any_list:
                h.update(a.SerializeToString(deterministic=True))
            return h.hexdigest()[:16]

        try:
            # Initial push: send everything subscribed in the first request.
            pending = set(subscribed)
            evt.set()
            while True:
                await evt.wait()
                evt.clear()
                if not subscribed:
                    continue
                # External notify (notify_node) wakes us without populating
                # `pending` — in that case push every subscribed type so the
                # hash check below decides what's actually changed.
                if not pending:
                    pending = set(subscribed)
                to_push = pending & subscribed
                pending = set()
                if not to_push:
                    continue
                resources_by_type = await _build_resources(node_id)
                latest_version: str | None = None
                for type_url in list(to_push):
                    if type_url not in _TYPE_TO_PROTO:
                        continue
                    resources = resources_by_type.get(type_url, [])
                    version = _hash(resources)
                    if sent_version.get(type_url) == version:
                        # Same content as last push for this type. Skip — envoy
                        # is already in this state, sending again just creates
                        # an ACK round-trip that wakes us again (the loop bug).
                        continue
                    nonce = uuid.uuid4().hex
                    sent_version[type_url] = version
                    sent_nonce[type_url] = nonce
                    latest_version = version
                    resp = discovery_pb2.DiscoveryResponse(
                        version_info=version,
                        type_url=type_url,
                        nonce=nonce,
                    )
                    resp.resources.extend(resources)
                    yield resp
                if latest_version is not None:
                    await _touch_seen(node_id, version=latest_version)
        finally:
            reader_task.cancel()
            _node_events.pop(node_id, None)
            _node_loops.pop(node_id, None)
            log.info("ads stream closed: node=%s", node_id)


# --- server lifecycle ------------------------------------------------------
_server: grpc.aio.Server | None = None


async def start() -> None:
    global _server, _pubsub_task
    if _server is not None:
        return
    server = grpc.aio.server()
    ads_pb2_grpc.add_AggregatedDiscoveryServiceServicer_to_server(_ADSService(), server)
    bind = f"0.0.0.0:{settings.XDS_GRPC_PORT}"
    server.add_insecure_port(bind)
    await server.start()
    _server = server
    if _pubsub_task is None:
        _pubsub_task = asyncio.create_task(_pubsub_listener())
    auth = "token-protected" if settings.XDS_AUTH_TOKEN else "OPEN (XDS_AUTH_TOKEN unset)"
    log.info("xDS ADS gRPC server listening on %s (%s)", bind, auth)


async def stop() -> None:
    global _server, _pubsub_task
    if _pubsub_task is not None:
        _pubsub_task.cancel()
        try:
            await _pubsub_task
        except (asyncio.CancelledError, Exception):
            pass
        _pubsub_task = None
    if _server is None:
        return
    await _server.stop(grace=2.0)
    _server = None
