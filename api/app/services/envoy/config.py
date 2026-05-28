"""Renders Envoy CDS/RDS/LDS resource dicts. Bootstrap rendering lives in
`bootstrap.py`.

All envoy instances (local + remote) consume CDS/LDS/RDS via xDS ADS from
the control plane gRPC server — no file-based config, no `watched_directory`
reload. The xds_server packs these dicts into proto Any messages and pushes
them on the ADS stream. The address rewrites that depend on mode (loopback
vs CONTROL_PLANE_PUBLIC_HOST) and the bind port that depends on mode
(operator-picked vs fixed REMOTE_BIND_LISTEN_PORT) are applied in xds_server,
not here — this file only emits the canonical shape.

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
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Channel, EnvoyInstance

log = logging.getLogger(__name__)

# Inline Lua filter: ensures stream_options.include_usage for streaming chat
# requests, and emits prompt/completion tokens from the response body into
# dynamic metadata (llmxy.usage) for the ALS sink to bill on.
#
# We do NOT rely on cjson — bundled envoy Lua doesn't ship it. Instead we use
# string.match on the OpenAI-style `"usage": { ... }` block, which is stable
# across openai / azure-openai / most compat backends.
#
# Streaming: SSE responses are scanned per chunk via handle:bodyChunks() so
# the proxy never buffers the full response — preserves TTFT for clients.
# Non-stream JSON uses handle:body() (bounded by per_connection_buffer_limit).
USAGE_LUA = r"""
-- gsub replacement strings treat `%` specially. Upstream model names can
-- legitimately contain `%` (rare) or `/` (common, e.g. "public/foo"); we
-- escape `%` to keep the replacement literal.
local function lua_gsub_escape_repl(s)
  return (string.gsub(s, "%%", "%%%%"))
end

function envoy_on_request(handle)
  local path = handle:headers():get(":path") or ""
  -- Body rewrite applies to any OpenAI-shape endpoint that carries a top-level
  -- `model` field: chat/completions, completions, embeddings. The presence of
  -- the x-llmxy-upstream-model header (set by ext_authz) gates the rewrite,
  -- so non-relay paths are unaffected.
  local is_chat = string.find(path, "/v1/chat/completions", 1, true) ~= nil
  local has_model = is_chat
                    or string.find(path, "/v1/completions", 1, true) ~= nil
                    or string.find(path, "/v1/embeddings", 1, true) ~= nil
  if not (is_chat or has_model) then return end

  local body = handle:body()
  if not body then return end
  local len = body:length()
  if len == 0 then return end
  local raw = body:getBytes(0, len)
  local patched = raw

  -- 1) Rewrite top-level "model": user-facing code → upstream model name.
  -- FastAPI relay path does this in openai.py; Envoy relays the body as-is
  -- so without this rewrite the upstream sees the public alias and 404s.
  local upstream_model = handle:headers():get("x-llmxy-upstream-model")
  if has_model and upstream_model and upstream_model ~= "" then
    -- Replace the FIRST top-level `"model":"..."` occurrence only. Nested
    -- assistant message content that happens to contain the substring won't
    -- match this exact JSON-key shape in practice.
    local repl = '"model":"' .. lua_gsub_escape_repl(upstream_model) .. '"'
    local new_raw, n = string.gsub(patched, '"model"%s*:%s*"[^"]*"', repl, 1)
    if n > 0 then patched = new_raw end
  end

  -- 2) Only patch streaming chat requests: ensure include_usage so the final
  -- SSE chunk carries a usage block. Detection is by string match — avoids
  -- JSON parsing in Lua and is safe because "stream":true is canonical.
  if is_chat and string.find(patched, '"stream"%s*:%s*true')
              and not string.find(patched, '"include_usage"%s*:%s*true') then
    if string.find(patched, '"stream_options"') then
      patched = string.gsub(patched, '("stream_options"%s*:%s*{)',
                            '%1"include_usage":true,', 1)
    else
      patched = string.gsub(patched, '}%s*$',
                            ',"stream_options":{"include_usage":true}}', 1)
    end
  end

  if patched ~= raw then
    body:setBytes(patched)
    handle:headers():replace("content-length", tostring(#patched))
  end
end

local function scan_usage(raw, last_pt, last_ct)
  for block in string.gmatch(raw, '"usage"%s*:%s*(%b{})') do
    local p = tonumber(string.match(block, '"prompt_tokens"%s*:%s*(%d+)'))
    local c = tonumber(string.match(block, '"completion_tokens"%s*:%s*(%d+)'))
    if p or c then last_pt, last_ct = p or last_pt, c or last_ct end
  end
  return last_pt, last_ct
end

function envoy_on_response(handle)
  local ct_hdr = (handle:headers():get("content-type") or ""):lower()
  local pt, ct_tok = 0, 0
  if string.find(ct_hdr, "text/event-stream", 1, true) then
    -- Streaming: iterate chunks as they pass through — no full-body buffer.
    -- A chunk may split a "usage": {...} block across boundaries; we carry a
    -- small tail (last 4KB) between chunks to absorb that case.
    local carry = ""
    for chunk in handle:bodyChunks() do
      local len = chunk:length()
      if len > 0 then
        local raw = chunk:getBytes(0, len)
        local scanbuf = carry .. raw
        pt, ct_tok = scan_usage(scanbuf, pt, ct_tok)
        if #scanbuf > 4096 then
          carry = string.sub(scanbuf, -4096)
        else
          carry = scanbuf
        end
      end
    end
  else
    local body = handle:body()
    if body then
      local len = body:length()
      if len > 0 then
        pt, ct_tok = scan_usage(body:getBytes(0, len), 0, 0)
      end
    end
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
    """Listener template. RDS source is ADS for everyone (file-based path is
    gone). The actual bind port and ext_authz endpoint get rewritten in
    xds_server._render_lds per-instance — what we emit here is just a
    placeholder using inst.listen_port that gets overridden."""
    rds_config_source = {"ads": {}, "resource_api_version": "V3"}
    hcm = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "ingress_http",
        "codec_type": "AUTO",
        # Populate X-Forwarded-For from the downstream socket so geo rules
        # and other IP-based logic in ext_authz see the real client. Without
        # this, ext_authz only ever sees envoy as the peer.
        "use_remote_address": True,
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
                    "grpc_service": {"envoy_grpc": {"cluster_name": "als_cluster"}},
                    "transport_api_version": "V3",
                },
                "additional_request_headers_to_log": [
                    "x-llmxy-request-id", "x-llmxy-user-id", "x-llmxy-api-key-id",
                    "x-llmxy-model-id", "x-llmxy-user-facing-model",
                    "x-llmxy-upstream-model", "x-llmxy-provider-type",
                    "x-llmxy-cluster", "x-llmxy-resolved-label",
                    "x-llmxy-classifier-model-id", "x-llmxy-classifier-upstream-model",
                    "x-llmxy-classifier-prompt-tokens", "x-llmxy-classifier-latency-ms",
                    "x-llmxy-classifier-status",
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
                            "timeout": settings.ENVOY_EXT_AUTHZ_TIMEOUT,
                        },
                        "path_prefix": "/internal/relay/authz",
                        "authorization_request": {
                            "allowed_headers": {
                                "patterns": [
                                    {"exact": "authorization"},
                                    {"exact": "content-type"},
                                    {"exact": "x-request-id"},
                                    {"exact": "x-forwarded-for"},
                                    {"exact": "x-real-ip"},
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
                        "max_request_bytes": settings.ENVOY_EXT_AUTHZ_MAX_BYTES,
                        "allow_partial_message": False,
                        "pack_as_bytes": True,
                    },
                    "failure_mode_allow": False,
                    # ext_authz returns `x-llmxy-cluster` to pick the upstream;
                    # the router caches the route on first match, so without
                    # this flag the original (pre-authz) route — which has no
                    # cluster_header value bound yet — would be reused.
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
