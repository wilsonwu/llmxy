"""Bootstrap YAML generator for remote envoy nodes.

Remote envoys connect back to the control plane over plaintext gRPC.
Authentication is two factor:
  1. shared static token in gRPC metadata (`x-llmxy-token`), and
  2. node.id reported in the first DiscoveryRequest/StreamAccessLogsMessage
     must match an envoy_instances row with mode=remote.

The operator copies this YAML to the envoy host (docker/k8s/bare metal),
saves it as bootstrap.yaml, and runs envoy with it. No certificates, no
bundle download, no CA — the control plane only ever serves the template.
"""
from __future__ import annotations

from typing import Any

import yaml

from app.core.config import settings
from app.models import EnvoyInstance


def _grpc_cluster(name: str, host: str, port: int, token: str) -> dict[str, Any]:
    cluster: dict[str, Any] = {
        "name": name,
        "type": "STRICT_DNS",
        "connect_timeout": "5s",
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
                        "address": {"socket_address": {"address": host, "port_value": port}}
                    }
                }]
            }],
        },
    }
    return cluster


def render_remote_bootstrap(inst: EnvoyInstance) -> dict[str, Any]:
    """Bootstrap dict. Caller serializes to YAML via render_bootstrap_yaml."""
    host = settings.CONTROL_PLANE_PUBLIC_HOST or "127.0.0.1"
    token = settings.XDS_AUTH_TOKEN or ""

    grpc_service = {
        "envoy_grpc": {"cluster_name": "xds_cluster"},
    }
    if token:
        grpc_service["initial_metadata"] = [
            {"key": "x-llmxy-token", "value": token},
        ]

    return {
        "node": {"id": inst.node_id, "cluster": "llmxy-remote"},
        "admin": {
            "address": {"socket_address": {"address": "0.0.0.0", "port_value": 9901}}
        },
        "dynamic_resources": {
            "ads_config": {
                "api_type": "GRPC",
                "transport_api_version": "V3",
                "grpc_services": [grpc_service],
                "set_node_on_first_message_only": True,
            },
            "cds_config": {"ads": {}, "resource_api_version": "V3"},
            "lds_config": {"ads": {}, "resource_api_version": "V3"},
        },
        "static_resources": {
            "clusters": [
                _grpc_cluster("xds_cluster", host, settings.XDS_GRPC_PORT, token),
                _grpc_cluster("als_cluster", host, settings.ALS_GRPC_PORT, token),
            ],
        },
    }


def render_bootstrap_yaml(inst: EnvoyInstance) -> str:
    """Serialized bootstrap.yaml an operator can copy to the envoy host."""
    return yaml.safe_dump(render_remote_bootstrap(inst), sort_keys=False)
