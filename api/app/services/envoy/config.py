"""Renders Envoy bootstrap + CDS/RDS/LDS YAML for a single instance.

Direct clusters (envoy → upstream) handle OpenAI-compatible providers only.
Anything else (Anthropic / Gemini / Azure) routes to a single `translator`
cluster that targets the FastAPI internal port, where the existing Python
adapters convert protocols and emit OpenAI-shape responses (with usage in
the last SSE chunk) so the downstream Lua filter parses usage uniformly.

The route's cluster is chosen at request time via `cluster_header:
x-llmxy-cluster`, set by the ext_authz response. Path is forwarded as-is
(this requires direct OpenAI channels' `base_url` to end with the same
prefix the client sends, e.g. `.../v1`).
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Channel, EnvoyInstance, EnvoyMode, EnvoyStatus

log = logging.getLogger(__name__)

# Inline Lua filter: ensures stream_options.include_usage for streaming chat
# requests, and emits prompt/completion tokens from the response body into
# dynamic metadata (llmxy.usage) for the ALS sink to bill on.
#
# We do NOT rely on cjson — bundled envoy Lua doesn't ship it. Instead we use
# string.match on the OpenAI-style `"usage": { ... }` block, which is stable
# across openai / azure-openai / most compat backends.
USAGE_LUA = r"""
local function extract_usage(s)
  if not s or #s == 0 then return 0, 0 end
  -- Match "usage": { ... "prompt_tokens": N ... "completion_tokens": M ... }
  -- Tokens may appear in either order, with arbitrary whitespace.
  local block = string.match(s, '"usage"%s*:%s*(%b{})')
  if not block then return 0, 0 end
  local pt = tonumber(string.match(block, '"prompt_tokens"%s*:%s*(%d+)')) or 0
  local ct = tonumber(string.match(block, '"completion_tokens"%s*:%s*(%d+)')) or 0
  return pt, ct
end

function envoy_on_request(handle)
  local path = handle:headers():get(":path") or ""
  if not string.find(path, "/v1/chat/completions", 1, true) then return end
  local body = handle:body()
  if not body then return end
  local len = body:length()
  if len == 0 then return end
  local raw = body:getBytes(0, len)
  -- Only patch streaming requests: ensure include_usage so the final SSE
  -- chunk carries a usage block. Detection is by string match — avoids JSON
  -- parsing in Lua and is safe because "stream":true is canonical.
  if not string.find(raw, '"stream"%s*:%s*true') then return end
  if string.find(raw, '"include_usage"%s*:%s*true') then return end
  local patched
  if string.find(raw, '"stream_options"') then
    -- inject include_usage into existing stream_options object
    patched = string.gsub(raw, '("stream_options"%s*:%s*{)',
                          '%1"include_usage":true,', 1)
  else
    -- append a stream_options field before the closing brace
    patched = string.gsub(raw, '}%s*$',
                          ',"stream_options":{"include_usage":true}}', 1)
  end
  if patched and patched ~= raw then
    body:setBytes(patched)
    handle:headers():replace("content-length", tostring(#patched))
  end
end

function envoy_on_response(handle)
  local ct_hdr = (handle:headers():get("content-type") or ""):lower()
  local body = handle:body()
  if not body then return end
  local len = body:length()
  if len == 0 then return end
  local raw = body:getBytes(0, len)
  local pt, ct_tok = 0, 0
  if string.find(ct_hdr, "text/event-stream", 1, true) then
    -- For SSE, the usage block lives in the last `data:` frame that has it.
    -- Search the whole concatenated body — the last match wins.
    local last_pt, last_ct = 0, 0
    for block in string.gmatch(raw, '"usage"%s*:%s*(%b{})') do
      local p = tonumber(string.match(block, '"prompt_tokens"%s*:%s*(%d+)'))
      local c = tonumber(string.match(block, '"completion_tokens"%s*:%s*(%d+)'))
      if p or c then last_pt, last_ct = p or last_pt, c or last_ct end
    end
    pt, ct_tok = last_pt, last_ct
  else
    pt, ct_tok = extract_usage(raw)
  end
  if pt > 0 or ct_tok > 0 then
    handle:streamInfo():dynamicMetadata():set(
      "llmxy.usage", "prompt_tokens", pt
    )
    handle:streamInfo():dynamicMetadata():set(
      "llmxy.usage", "completion_tokens", ct_tok
    )
  end
end
"""


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
    OpenAI-compatible providers: pass-through, path forwarded as-is.
    Anything else falls back to the translator cluster.
    """
    return (channel.provider_type or "").lower() == "openai"


def render_cds(channels: list[Channel]) -> dict[str, Any]:
    clusters: list[dict[str, Any]] = []

    # 1. translator: points back at the FastAPI internal port for Anthropic / Gemini / Azure
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

    # 2. ext_authz target: same FastAPI internal port
    clusters.append({
        "name": "ext_authz",
        "type": "STRICT_DNS",
        "connect_timeout": "1s",
        "lb_policy": "ROUND_ROBIN",
        "load_assignment": {
            "cluster_name": "ext_authz",
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

    # 3. ALS gRPC target
    clusters.append({
        "name": "als",
        "type": "STRICT_DNS",
        "connect_timeout": "1s",
        "lb_policy": "ROUND_ROBIN",
        "typed_extension_protocol_options": {
            "envoy.extensions.upstreams.http.v3.HttpProtocolOptions": {
                "@type": "type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions",
                "explicit_http_config": {"http2_protocol_options": {}},
            }
        },
        "load_assignment": {
            "cluster_name": "als",
            "endpoints": [{
                "lb_endpoints": [{
                    "endpoint": {
                        "address": {
                            "socket_address": {
                                "address": settings.INTERNAL_API_HOST,
                                "port_value": settings.ALS_GRPC_PORT,
                            }
                        }
                    }
                }]
            }],
        },
    })

    # 4. per-channel direct clusters
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
      A. header `x-llmxy-cluster=translator` → cluster `translator`, with
         prefix_rewrite `/v1/` → `/internal/translate/v1/`. Used for
         anthropic/gemini/azure that need protocol translation.
      B. catch-all `/v1/` → cluster picked from header (direct openai-compat).
    Route A must be declared first so envoy matches header-equipped requests
    before falling through to the cluster_header default.
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
    hcm = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "ingress_http",
        "codec_type": "AUTO",
        "rds": {
            "route_config_name": "llmxy_routes",
            "config_source": {
                "path_config_source": {
                    "path": os.path.join(inst.config_dir, "rds.yaml"),
                    "watched_directory": {"path": inst.config_dir},
                },
                "resource_api_version": "V3",
            },
        },
        "access_log": [{
            "name": "envoy.access_loggers.http_grpc",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.access_loggers.grpc.v3.HttpGrpcAccessLogConfig",
                "common_config": {
                    "log_name": "llmxy_relay",
                    "grpc_service": {"envoy_grpc": {"cluster_name": "als"}},
                    "transport_api_version": "V3",
                },
                "additional_request_headers_to_log": [
                    "x-llmxy-request-id", "x-llmxy-user-id", "x-llmxy-api-key-id",
                    "x-llmxy-model-id", "x-llmxy-user-facing-model",
                    "x-llmxy-upstream-model", "x-llmxy-provider-type",
                    "x-llmxy-cluster",
                ],
            },
        }],
        "http_filters": [
            {
                "name": "envoy.filters.http.ext_authz",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz",
                    "transport_api_version": "V3",
                    "http_service": {
                        "server_uri": {
                            "uri": f"http://{settings.INTERNAL_API_HOST}:{settings.INTERNAL_API_PORT}",
                            "cluster": "ext_authz",
                            "timeout": "5s",
                        },
                        "path_prefix": "/internal/relay/authz",
                        "authorization_request": {
                            "allowed_headers": {
                                "patterns": [
                                    {"exact": "authorization"},
                                    {"exact": "content-type"},
                                    {"exact": "x-request-id"},
                                ]
                            },
                        },
                        "authorization_response": {
                            "allowed_upstream_headers": {
                                "patterns": [
                                    {"prefix": "x-llmxy-"},
                                    {"exact": "authorization"},
                                    {"exact": "x-api-key"},
                                    {"exact": "anthropic-version"},
                                ]
                            },
                        },
                    },
                    "with_request_body": {
                        "max_request_bytes": 131072,
                        "allow_partial_message": False,
                        "pack_as_bytes": True,
                    },
                    "failure_mode_allow": False,
                    "clear_route_cache": True,
                },
            },
            {
                "name": "envoy.filters.http.lua",
                "typed_config": {
                    "@type": "type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua",
                    "inline_code": USAGE_LUA,
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


def render_bootstrap(inst: EnvoyInstance) -> dict[str, Any]:
    cds_path = os.path.join(inst.config_dir, "cds.yaml")
    lds_path = os.path.join(inst.config_dir, "lds.yaml")
    return {
        "node": {"id": f"llmxy-{inst.name}", "cluster": "llmxy"},
        "admin": {
            "address": {
                "socket_address": {"address": "127.0.0.1", "port_value": inst.admin_port}
            }
        },
        "dynamic_resources": {
            "cds_config": {
                "path_config_source": {
                    "path": cds_path,
                    "watched_directory": {"path": inst.config_dir},
                },
                "resource_api_version": "V3",
            },
            "lds_config": {
                "path_config_source": {
                    "path": lds_path,
                    "watched_directory": {"path": inst.config_dir},
                },
                "resource_api_version": "V3",
            },
        },
    }


def _atomic_write(path: str, content: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


async def write_all(db: AsyncSession, inst: EnvoyInstance) -> int:
    channels = (await db.execute(select(Channel).order_by(Channel.id))).scalars().all()
    os.makedirs(inst.config_dir, exist_ok=True)

    files = {
        "cds.yaml": render_cds(channels),
        "rds.yaml": render_rds(),
        "lds.yaml": render_lds(inst),
        "bootstrap.yaml": render_bootstrap(inst),
    }
    # write rds/cds/lds first (file-based hot-reload reads these), then bootstrap
    for fname in ("cds.yaml", "rds.yaml", "lds.yaml", "bootstrap.yaml"):
        path = os.path.join(inst.config_dir, fname)
        _atomic_write(path, yaml.safe_dump(files[fname], sort_keys=False))

    inst.config_version = (inst.config_version or 0) + 1
    log.info("envoy[%s] wrote config v%d to %s", inst.name, inst.config_version, inst.config_dir)
    return inst.config_version


async def regenerate(db: AsyncSession, inst: EnvoyInstance) -> int:
    return await write_all(db, inst)


async def regenerate_all_running(db: AsyncSession) -> int:
    """Refresh config for every active envoy: rewrite YAML for local instances
    that are running, and ping the xDS server for remote nodes (regardless of
    connection state — they'll pick it up next stream open)."""
    rows = (await db.execute(select(EnvoyInstance))).scalars().all()
    n = 0
    dirty_remote: list[str] = []
    for inst in rows:
        if inst.mode == EnvoyMode.remote:
            inst.config_version = (inst.config_version or 0) + 1
            dirty_remote.append(inst.node_id)
            n += 1
            continue
        if inst.status != EnvoyStatus.running:
            continue
        try:
            await write_all(db, inst)
            n += 1
        except Exception as e:
            log.warning("regen failed for envoy[%s]: %s", inst.name, e)
    if n:
        await db.commit()
    if dirty_remote:
        try:
            from app.services.envoy import xds_server
            for nid in dirty_remote:
                xds_server.notify_node(nid)
        except Exception as e:
            log.debug("xds notify skipped: %s", e)
    return n
