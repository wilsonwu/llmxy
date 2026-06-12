---
description: "Use when working on llmxy Envoy front-proxy, remote-envoy deployment manifests, xDS, ext_proc, ALS usage reporting, generated Envoy configs, Docker Compose, or Kubernetes deployment files."
applyTo:
  - "api/app/services/envoy/**/*"
  - "api/app/api/internal/**/*"
  - "deployments/**/*"
  - "docker-compose.yml"
---
# Envoy And Deployment Instructions

## Transport Model

- `api-direct` is always available at the FastAPI `/v1/*` endpoints. It is simpler and does synchronous billing in the request path.
- `envoy` is optional and high-throughput. Clients opt in by using an Envoy listener URL; the gateway must not silently redirect users from API-direct to Envoy.
- Local Envoy instances are created and managed from the admin UI, typically on ports 9000-9099. Remote Envoy nodes connect to the API control plane through xDS and report usage through ALS.

## Control Plane Responsibilities

- ext_proc performs API key authz, quota checks, request body limits, and route resolution for the Envoy hot path.
- ALS receives usage after upstream responses and performs async billing and `UsageLog` insertion. Avoid duplicate billing between API-direct and ALS paths.
- xDS publishes cluster, route, and listener config to remote Envoy nodes. Remote nodes authenticate with `x-llmxy-token` when `XDS_AUTH_TOKEN` is configured and must match a known `node_id`.
- Channel, model, and route changes must refresh generated config for running instances without requiring a manual restart when the existing runtime supports hot reload.

## Performance And Consistency

- Keep per-request Envoy hot-path code cache-first. Use API key snapshots and Redis quota cache instead of repeated PostgreSQL reads where existing helpers support it.
- PG remains the source of truth. Redis drift should fail open or rehydrate according to existing `quota_cache` behavior, not become a permanent denial state.
- Local mode stores process state in one API replica. For multi-replica deployments, prefer remote-only mode because xDS updates are broadcast through Redis pub/sub.

## Deployment Files

- Keep env var names aligned with `.env.example` and `docker-compose.yml`.
- Do not add secrets to manifests. Use environment variables, Kubernetes secrets, or documented operator-provided values.
- When changing ports, update all affected docs/manifests together: API HTTP, ALS gRPC, xDS gRPC, ext_proc gRPC, Envoy listener, and admin ports.
