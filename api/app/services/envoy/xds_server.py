"""ADS (Aggregated Discovery Service) gRPC server — serves CDS/RDS/LDS
to remote Envoy nodes over plaintext gRPC.

Auth: shared static token via gRPC metadata `x-llmxy-token` (settings.XDS_AUTH_TOKEN)
plus node_id match — the node.id in the first DiscoveryRequest must exist in
envoy_instances with mode=remote. Empty XDS_AUTH_TOKEN disables the token
check (dev mode).
"""
from __future__ import annotations

import asyncio
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


def notify_node(node_id: str) -> None:
    """Wake the ADS stream serving `node_id` so it re-pushes config.
    Safe to call from any thread / event loop."""
    evt = _node_events.get(node_id)
    loop = _node_loops.get(node_id)
    if evt is None or loop is None:
        return
    loop.call_soon_threadsafe(evt.set)


# --- resource rendering -----------------------------------------------------

def _render_lds_remote(inst: EnvoyInstance) -> dict[str, Any]:
    public_host = settings.CONTROL_PLANE_PUBLIC_HOST or "127.0.0.1"
    public_port = settings.API_PORT
    lds = envoy_config.render_lds(inst)
    for res in lds.get("resources", []):
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

def check_token(context: grpc.aio.ServicerContext) -> bool:
    """Return True if the gRPC metadata satisfies XDS_AUTH_TOKEN. An empty
    setting always returns True (dev-mode bypass)."""
    expected = settings.XDS_AUTH_TOKEN
    if not expected:
        return True
    md = dict(context.invocation_metadata() or [])
    return md.get(_TOKEN_METADATA_KEY) == expected


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

        sent_version: dict[str, str] = {}
        sent_nonce: dict[str, str] = {}
        subscribed: set[str] = {first.type_url} if first.type_url else set()

        async def _reader():
            try:
                async for req in request_iterator:
                    type_url = req.type_url
                    subscribed.add(type_url)
                    if req.error_detail and req.error_detail.message:
                        log.warning(
                            "ads NACK from %s for %s: %s",
                            node_id, type_url, req.error_detail.message,
                        )
                    evt.set()
            except Exception as e:
                log.info("ads reader for %s ended: %s", node_id, e)

        reader_task = asyncio.create_task(_reader())

        try:
            evt.set()
            while True:
                await evt.wait()
                evt.clear()
                if not subscribed:
                    continue
                resources_by_type = await _build_resources(node_id)
                version = str(int(datetime.now(timezone.utc).timestamp()))
                for type_url in list(subscribed):
                    if type_url not in _TYPE_TO_PROTO:
                        continue
                    nonce = uuid.uuid4().hex
                    sent_version[type_url] = version
                    sent_nonce[type_url] = nonce
                    resp = discovery_pb2.DiscoveryResponse(
                        version_info=version,
                        type_url=type_url,
                        nonce=nonce,
                    )
                    resp.resources.extend(resources_by_type.get(type_url, []))
                    yield resp
                await _touch_seen(node_id, version=version)
        finally:
            reader_task.cancel()
            _node_events.pop(node_id, None)
            _node_loops.pop(node_id, None)
            log.info("ads stream closed: node=%s", node_id)


# --- server lifecycle ------------------------------------------------------
_server: grpc.aio.Server | None = None


async def start() -> None:
    global _server
    if _server is not None:
        return
    server = grpc.aio.server()
    ads_pb2_grpc.add_AggregatedDiscoveryServiceServicer_to_server(_ADSService(), server)
    bind = f"0.0.0.0:{settings.XDS_GRPC_PORT}"
    server.add_insecure_port(bind)
    await server.start()
    _server = server
    auth = "token-protected" if settings.XDS_AUTH_TOKEN else "OPEN (XDS_AUTH_TOKEN unset)"
    log.info("xDS ADS gRPC server listening on %s (%s)", bind, auth)


async def stop() -> None:
    global _server
    if _server is None:
        return
    await _server.stop(grace=2.0)
    _server = None
