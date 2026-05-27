"""Bootstrap YAML generator for envoy nodes (local + remote).

Both local and remote envoys use the SAME bootstrap shape: a node id, an
admin port, and ADS pointing at our gRPC control plane. The only difference
is the address — local dials 127.0.0.1, remote dials CONTROL_PLANE_PUBLIC_HOST.

Auth is two factor:
  1. shared static token in gRPC metadata (`x-llmxy-token`), and
  2. node.id reported in the first DiscoveryRequest/StreamAccessLogsMessage
     must match an envoy_instances row (any mode).

For remote deploys we also expose `render_k8s_manifest` / `render_docker_run`
so the operator gets a ready-to-apply artifact. Local instances only need
bootstrap.yaml — `runtime.start` writes it once and execs envoy against it.
"""
from __future__ import annotations

from typing import Any

import yaml

from app.core.config import settings
from app.models import EnvoyInstance, EnvoyMode


# Remote envoy uses FIXED ports inside the pod / container. The deploy
# manifests (k8s + docker) embed these constants verbatim — operator never
# customises them, because doing so introduces drift between (a) what envoy
# binds (set here, pushed via LDS), (b) what containerPort exposes, and
# (c) what the dashboard expects to probe. The form fields on the create
# dialog are for the operator to fill in AFTER deploying with the REAL
# externally reachable values (NodePort for k8s, host port for docker
# --network=host) — those drive admin_url/proxy_url only, never the manifest.
REMOTE_BIND_LISTEN_PORT = 9000
REMOTE_BIND_ADMIN_PORT = 9001
# k8s NodePort defaults — kubectl applies them with the manifest, and the
# operator types them back into the form so admin_url/proxy_url point at
# the externally reachable NodePort. Inside k8s' 30000-32767 valid range.
REMOTE_K8S_LISTEN_NODEPORT = 30000
REMOTE_K8S_ADMIN_NODEPORT = 30001

# Envoy container image. Defaults to upstream pinned tag (matches the proto
# schemas we vendor — envoy.service.accesslog.v3 etc.). Override via the
# ENVOY_IMAGE env var when deploying behind a mirror (e.g. daocloud).
ENVOY_IMAGE = settings.ENVOY_IMAGE


def _control_plane_host(inst: EnvoyInstance) -> str:
    """Address envoy dials for xDS/ALS. Local lives in the same host as the
    api process, so loopback works (and avoids dragging CONTROL_PLANE_PUBLIC_HOST
    into single-host dev). Remote uses the operator-supplied public host."""
    if inst.mode == EnvoyMode.local:
        return "127.0.0.1"
    return settings.CONTROL_PLANE_PUBLIC_HOST or "127.0.0.1"


def _admin_bind_port(inst: EnvoyInstance) -> int:
    """Local binds whatever admin port the operator picked; remote always
    binds the fixed REMOTE_BIND_ADMIN_PORT (manifests' containerPort)."""
    if inst.mode == EnvoyMode.local:
        return inst.admin_port or REMOTE_BIND_ADMIN_PORT
    return REMOTE_BIND_ADMIN_PORT


def _grpc_cluster(name: str, host: str, port: int) -> dict[str, Any]:
    return {
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


def render_bootstrap(inst: EnvoyInstance) -> dict[str, Any]:
    """Bootstrap dict for any envoy instance. Caller serializes via render_bootstrap_yaml."""
    host = _control_plane_host(inst)
    # Bootstrap always uses the FIRST token in XDS_AUTH_TOKEN (csv supports
    # rotation: server accepts all listed, but new envoy templates emit the
    # current/primary one only).
    raw_token = settings.XDS_AUTH_TOKEN or ""
    token = next((t.strip() for t in raw_token.split(",") if t.strip()), "")

    grpc_service: dict[str, Any] = {
        "envoy_grpc": {"cluster_name": "xds_cluster"},
    }
    if token:
        grpc_service["initial_metadata"] = [
            {"key": "x-llmxy-token", "value": token},
        ]

    cluster_label = "llmxy-local" if inst.mode == EnvoyMode.local else "llmxy-remote"

    return {
        "node": {"id": inst.node_id, "cluster": cluster_label},
        "admin": {
            "address": {"socket_address": {"address": "0.0.0.0", "port_value": _admin_bind_port(inst)}}
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
            # xds + als are statically wired here so envoy can bring up the
            # control-plane channels before LDS/CDS arrive. The application
            # clusters (translator, ext_authz, per-channel) come over CDS.
            "clusters": [
                _grpc_cluster("xds_cluster", host, settings.XDS_GRPC_PORT),
                _grpc_cluster("als_cluster", host, settings.ALS_GRPC_PORT),
            ],
        },
    }


def render_bootstrap_yaml(inst: EnvoyInstance) -> str:
    """Serialized bootstrap.yaml. For local: runtime writes this to disk
    and exec's envoy against it. For remote: operator copies to the envoy host."""
    return yaml.safe_dump(render_bootstrap(inst), sort_keys=False)


def render_k8s_manifest(inst: EnvoyInstance) -> str:
    """Ready-to-`kubectl apply -f` manifest: ConfigMap (bootstrap.yaml) +
    Deployment + Service (NodePort). Ports are HARDCODED to envoy standards
    (containerPort 9000/9001, NodePort 30000/30001) — never sourced from the
    EnvoyInstance row, because the row's listen_port/admin_port are filled
    in by the operator AFTER deploy with the externally-reachable values
    (= NodePort in k8s). Driving the manifest from those would create a
    chicken-and-egg loop where the form's value depends on the manifest
    that depends on the form's value.

    Operator only needs to set the namespace if they want it somewhere
    other than `default`."""
    bootstrap = render_bootstrap_yaml(inst)
    name = f"llmxy-envoy-{inst.name}"
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{name}-bootstrap"},
        "data": {"bootstrap.yaml": bootstrap},
    }
    listen_port = REMOTE_BIND_LISTEN_PORT
    admin_port = REMOTE_BIND_ADMIN_PORT
    dep = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": {"app": name}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [{
                        "name": "envoy",
                        "image": ENVOY_IMAGE,
                        "args": ["-c", "/etc/envoy/bootstrap.yaml", "--service-cluster", "llmxy-remote"],
                        "ports": [
                            {"name": "listen", "containerPort": listen_port},
                            {"name": "admin", "containerPort": admin_port},
                        ],
                        "volumeMounts": [{
                            "name": "bootstrap",
                            "mountPath": "/etc/envoy",
                            "readOnly": True,
                        }],
                        "readinessProbe": {
                            "httpGet": {"path": "/ready", "port": admin_port},
                            "initialDelaySeconds": 3,
                            "periodSeconds": 5,
                        },
                    }],
                    "volumes": [{
                        "name": "bootstrap",
                        "configMap": {"name": f"{name}-bootstrap"},
                    }],
                },
            },
        },
    }
    listen_nodeport = REMOTE_K8S_LISTEN_NODEPORT
    admin_nodeport = REMOTE_K8S_ADMIN_NODEPORT
    svc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {
            "type": "NodePort",
            "selector": {"app": name},
            "ports": [
                {"name": "listen", "port": listen_port, "targetPort": listen_port, "nodePort": listen_nodeport},
                {"name": "admin",  "port": admin_port,  "targetPort": admin_port,  "nodePort": admin_nodeport},
            ],
        },
    }
    # PyYAML's default emits "---" only with explicit_start; do it manually so
    # the operator sees three labeled sections in order. Keep sort_keys=False so
    # field order matches the kubectl idiomatic top-down read (kind first, etc).
    parts = [
        yaml.safe_dump(cm, sort_keys=False),
        yaml.safe_dump(dep, sort_keys=False),
        yaml.safe_dump(svc, sort_keys=False),
    ]
    return "---\n" + "\n---\n".join(parts)


def render_docker_run(inst: EnvoyInstance) -> str:
    """Single-host quickstart: writes bootstrap.yaml then runs envoy in docker
    with --network=host so the bind ports (9000 listen / 9001 admin) are the
    same numbers clients hit externally — no port translation. Operator types
    those same numbers into the form post-deploy."""
    name = f"llmxy-envoy-{inst.name}"
    return (
        f"# 1) save the bootstrap to /tmp/{name}.yaml\n"
        f"cat > /tmp/{name}.yaml <<'EOF'\n"
        f"{render_bootstrap_yaml(inst)}EOF\n\n"
        f"# 2) run envoy (replace --network=host with -p mappings if you're on macOS)\n"
        f"docker run -d --name {name} \\\n"
        f"  --network=host \\\n"
        f"  -v /tmp/{name}.yaml:/etc/envoy/bootstrap.yaml:ro \\\n"
        f"  {ENVOY_IMAGE} \\\n"
        f"  -c /etc/envoy/bootstrap.yaml --service-cluster llmxy-remote\n"
    )
