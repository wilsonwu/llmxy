"""Renders Envoy CDS/RDS/LDS resource dicts. Bootstrap rendering lives in
`bootstrap.py`.

All envoy instances (local + remote) consume CDS/LDS/RDS via xDS ADS from
the control plane gRPC server — no file-based config, no `watched_directory`
reload. The xds_server packs these dicts into proto Any messages and pushes
them on the ADS stream. The address rewrites that depend on mode (loopback
vs CONTROL_PLANE_PUBLIC_HOST) and the bind port that depends on mode
(operator-picked vs fixed REMOTE_BIND_LISTEN_PORT) are applied in xds_server,
not here — this file only emits the canonical shape.

The ext_proc filter authenticates and resolves the concrete route target, then
mutates `x-llmxy-*` headers and sends relay requests to the internal
`translator` cluster. The translator performs protocol conversion and
synchronous billing.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Channel, EnvoyInstance
from app.services.providers import channel_connector

log = logging.getLogger(__name__)

def _channel_cluster_name(channel_id: int) -> str:
    return f"ch_{channel_id}"


def _upstream_host_port(base_url: str) -> tuple[str, int, bool]:
    """Parse `https://api.openai.com/v1` → (host, port, is_tls)."""
    u = urlparse(base_url)
    is_tls = u.scheme == "https"
    host = u.hostname or "localhost"
    port = u.port or (443 if is_tls else 80)
    return host, port, is_tls


def _is_direct(channel: Channel) -> bool:
    """Whether this channel can be proxied directly by Envoy.

    ext_proc currently routes relay traffic through the translator for uniform
    protocol conversion and billing. Direct clusters remain in CDS so a future
    decision policy can opt into direct OpenAI-compatible egress without a
    bootstrap/config shape change.
    """
    return channel_connector(channel) == "openai"


def _grpc_cluster(name: str, host: str, port: int) -> dict[str, Any]:
    return {
        "name": name,
        "type": "STRICT_DNS",
        "connect_timeout": "5s",
        "lb_policy": "ROUND_ROBIN",
        "dns_lookup_family": "V4_PREFERRED",
        "typed_extension_protocol_options": {
            "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
                "@type": "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions",
                "explicit_http_config": {"http2_protocol_options": {}},
            }
        },
        "load_assignment": {
            "cluster_name": name,
            "endpoints": [{
                "lb_endpoints": [{
                    "endpoint": {
                        "address": {
                            "socket_address": {"address": host, "port_value": port}
                        }
                    }
                }]
            }],
        },
    }


def render_cds(channels: list[Channel]) -> dict[str, Any]:
    clusters: list[dict[str, Any]] = []

    # 1. translator: points back at the FastAPI internal port for relay execution
    clusters.append({
        "name": "translator",
        "type": "STRICT_DNS",
        "connect_timeout": "5s",
        "lb_policy": "ROUND_ROBIN",
        "load_assignment": {
            "cluster_name": "translator",
            "endpoints": [{
                "lb_endpoints": [{
                    "endpoint": {
                        "address": {
                            "socket_address": {
                                "address": settings.INTERNAL_API_HOST,
                                "port_value": settings.INTERNAL_API_PORT,
                            }
                        }
                    }
                }]
            }],
        },
    })

    # 2. ext_proc target: gRPC external processor on the control plane
    clusters.append(_grpc_cluster("ext_proc", settings.INTERNAL_API_HOST, settings.EXT_PROC_GRPC_PORT))

    # 3. per-channel direct clusters. ALS and xDS clusters are declared
    # statically in bootstrap (so envoy can dial them before CDS arrives) —
    # never sent via CDS.
    for ch in channels:
        if not ch.enabled or not _is_direct(ch):
            continue
        host, port, is_tls = _upstream_host_port(ch.base_url)
        cluster: dict[str, Any] = {
            "name": _channel_cluster_name(ch.id),
            "type": "STRICT_DNS",
            "connect_timeout": "5s",
            "lb_policy": "ROUND_ROBIN",
            "dns_lookup_family": "V4_ONLY",
            "load_assignment": {
                "cluster_name": _channel_cluster_name(ch.id),
                "endpoints": [{
                    "lb_endpoints": [{
                        "endpoint": {
                            "address": {
                                "socket_address": {"address": host, "port_value": port}
                            }
                        }
                    }]
                }],
            },
        }
        if is_tls:
            cluster["transport_socket"] = {
                "name": "envoy.transport_sockets.tls",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext",
                    "sni": host,
                },
            }
        clusters.append(cluster)

    return {"resources": [
        {"@type": "type.googleapis.com/envoy.config.cluster.v3.Cluster", **c} for c in clusters
    ]}


def render_rds() -> dict[str, Any]:
    """Two routes per virtual host:
      A. `x-llmxy-cluster=translator` -> internal translator, with
         prefix_rewrite `/v1/` -> `/internal/translate/v1/`.
      B. catch-all `/v1/` -> cluster picked from header. This is retained for
         future direct egress policies; ext_proc sets translator today.
    """
    common_route_opts = {
        "timeout": "0s",       # disable per-route timeout (streaming may be long)
        "idle_timeout": "300s",
    }
    return {"resources": [{
        "@type": "type.googleapis.com/envoy.config.route.v3.RouteConfiguration",
        "name": "llmxy_routes",
        "virtual_hosts": [{
            "name": "llmxy",
            "domains": ["*"],
            "routes": [
                {
                    "match": {
                        "prefix": "/v1/",
                        "headers": [
                            {"name": "x-llmxy-cluster", "string_match": {"exact": "translator"}}
                        ],
                    },
                    "route": {
                        "cluster": "translator",
                        "prefix_rewrite": "/internal/translate/v1/",
                        "auto_host_rewrite": True,
                        **common_route_opts,
                    },
                },
                {
                    "match": {"prefix": "/v1/"},
                    "route": {
                        "cluster_header": "x-llmxy-cluster",
                        "auto_host_rewrite": True,
                        **common_route_opts,
                    },
                },
            ],
        }],
    }]}


def render_lds(inst: EnvoyInstance) -> dict[str, Any]:
    """Listener template. RDS source is ADS for everyone (file-based path is
    gone). The actual bind port and control-plane endpoints get rewritten in
    xds_server._render_lds per-instance — what we emit here is just a
    placeholder using inst.listen_port that gets overridden."""
    rds_config_source = {"ads": {}, "resource_api_version": "V3"}
    hcm = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "ingress_http",
        "codec_type": "AUTO",
        # Populate X-Forwarded-For from the downstream socket so geo rules
        # and other IP-based logic in ext_proc see the real client.
        "use_remote_address": True,
        # Explicit migration step for envoy >=1.32: without this, envoy logs
        # a startup warning that the default "trust RFC1918" behaviour will
        # change. We trust nothing — clients hit envoy directly, so anything
        # claiming to be internal would be a spoof. With use_remote_address=true
        # the real client IP is still recovered correctly.
        "internal_address_config": {
            "unix_sockets": False,
            "cidr_ranges": [],
        },
        "rds": {
            "route_config_name": "llmxy_routes",
            "config_source": rds_config_source,
        },
        "access_log": [{
            "name": "envoy.access_loggers.http_grpc",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.access_loggers.grpc.v3.HttpGrpcAccessLogConfig",
                "common_config": {
                    "log_name": "llmxy_relay",
                    "grpc_service": {
                        "envoy_grpc": {"cluster_name": "als_cluster"},
                        # Same shared static token as xDS. Required when
                        # XDS_AUTH_TOKEN is set on the control plane —
                        # without it, ALS aborts with UNAUTHENTICATED and
                        # access logs (including billing usage) silently
                        # never reach _stream_handler.
                        **({"initial_metadata": [
                            {"key": "x-llmxy-token", "value": settings.XDS_AUTH_TOKEN}
                        ]} if settings.XDS_AUTH_TOKEN else {}),
                    },
                    "transport_api_version": "V3",
                },
                "additional_request_headers_to_log": [
                    "x-llmxy-request-id", "x-llmxy-user-id", "x-llmxy-api-key-id",
                    "x-llmxy-model-id", "x-llmxy-user-facing-model",
                    "x-llmxy-upstream-model", "x-llmxy-provider-type",
                    "x-llmxy-cluster", "x-llmxy-resolved-label",
                    "x-llmxy-client-protocol", "x-llmxy-billed-sync",
                    "x-llmxy-classifier-model-id", "x-llmxy-classifier-upstream-model",
                    "x-llmxy-classifier-prompt-tokens", "x-llmxy-classifier-latency-ms",
                    "x-llmxy-classifier-status",
                ],
            },
        }],
        "http_filters": [
            {
                "name": "envoy.filters.http.ext_proc",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_proc.v3.ExternalProcessor",
                    "grpc_service": {
                        "envoy_grpc": {"cluster_name": "ext_proc"},
                        "timeout": settings.ENVOY_EXT_PROC_TIMEOUT,
                        **({"initial_metadata": [
                            {"key": "x-llmxy-token", "value": settings.XDS_AUTH_TOKEN}
                        ]} if settings.XDS_AUTH_TOKEN else {}),
                    },
                    "processing_mode": {
                        "request_header_mode": "SEND",
                        "request_body_mode": "BUFFERED",
                        "response_header_mode": "SKIP",
                        "response_body_mode": "NONE",
                    },
                    "message_timeout": settings.ENVOY_EXT_PROC_TIMEOUT,
                    "failure_mode_allow": False,
                },
            },
            {
                "name": "envoy.filters.http.router",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router",
                },
            },
        ],
        "stream_idle_timeout": "0s",
        "request_timeout": "0s",
    }

    return {"resources": [{
        "@type": "type.googleapis.com/envoy.config.listener.v3.Listener",
        "name": "llmxy_listener",
        "address": {
            "socket_address": {"address": "0.0.0.0", "port_value": inst.listen_port}
        },
        "filter_chains": [{
            "filters": [{
                "name": "envoy.filters.network.http_connection_manager",
                "typed_config": hcm,
            }]
        }],
    }]}


async def regenerate_all_running(db: AsyncSession) -> int:
    """Trigger an xDS push to every envoy instance. Bumps config_version so
    operators can see something moved, and wakes the live ADS stream (or
    no-ops if the node isn't currently connected — they'll pick the new
    version up on next stream open).

    Mode-agnostic: local and remote both consume CDS/LDS/RDS via the same
    xDS server. There is no longer a file-based path for local."""
    from app.services.envoy import xds_server
    rows = (await db.execute(select(EnvoyInstance))).scalars().all()
    n = 0
    for inst in rows:
        inst.config_version = (inst.config_version or 0) + 1
        n += 1
    if n:
        await db.commit()
    for inst in rows:
        try:
            xds_server.notify_node(inst.node_id)
        except Exception as e:
            log.debug("xds notify skipped for %s: %s", inst.node_id, e)
    return n
