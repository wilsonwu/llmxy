# Remote Envoy Deployment

Templates for running an Envoy front-proxy that connects back to a llmxy
control plane as a managed remote node. The control plane delivers
CDS/RDS/LDS over plaintext gRPC ADS and ingests access logs over plaintext
gRPC ALS. Authentication is **shared static token + node id** — no TLS, no
client certificates.

> Put TLS in front of the control plane via your own reverse proxy if you
> need wire-level encryption. The flow stays the same; envoy speaks plain
> gRPC to whatever address the control plane gives you.

## Prerequisites

1. **Control plane** has `XDS_AUTH_TOKEN` and `CONTROL_PLANE_PUBLIC_HOST`
   set in its env. The token goes into envoy's gRPC metadata as
   `x-llmxy-token`; `CONTROL_PLANE_PUBLIC_HOST` is the first address a
   remote envoy will try.
2. **Create a remote instance** in the admin UI (Envoy → New instance →
   mode = remote, listen_port = the port your envoy will expose,
   admin_url = how the control plane will reach this envoy's admin API).
   Note the `node_id` shown on the resulting row — you'll paste it into
   the deployment env vars below.

No need to download a bootstrap.yaml — both deployment options here ship a
template (`bootstrap.template.yaml`) and substitute the per-instance values
at container start.

## Option A — Docker Compose

```sh
# 1. Edit the five LLMXY_* env vars in docker-compose.yaml (search "EDIT ME").
# 2. Bring it up — bootstrap.template.yaml sits next to docker-compose.yaml.
docker compose -f docker-compose.yaml up -d
docker compose logs -f envoy
```

Envoy now serves on `localhost:9000`. The admin UI should show the instance
turning green within a few seconds; the "last seen" column updates on each
ALS heartbeat.

## Option B — Kubernetes (single-file apply)

`kubernetes.yaml` is self-contained: standard ConfigMap + Deployment + Service,
no operators, CRDs, Helm or kustomize. The ConfigMap embeds the same bootstrap
template; the per-instance values are passed as env vars and substituted at
container start.

```sh
# 1. Edit the five LLMXY_* env vars at the top of the Deployment in
#    kubernetes.yaml (search for "EDIT ME").
# 2. Apply.
kubectl apply -f kubernetes.yaml
kubectl rollout status deploy/llmxy-envoy
```

`Service` defaults to `LoadBalancer`; switch to `ClusterIP` / `NodePort` per
your environment. Scale by editing `Deployment.spec.replicas` — every replica
shares the same bootstrap and connects to the same control plane node row.

## Verifying End-to-End

1. Local: `curl http://<envoy>:9901/ready` returns `LIVE`.
2. Control plane: instance shows green, last seen seconds ago, ADS connected.
3. Send a request: `curl http://<envoy>:9000/v1/chat/completions -H ...` —
   the relay billing log gets a fresh row.
4. Hot reload: change a channel in the admin UI and click Push — within a
   second envoy logs `cds: add/update cluster ...` with no restart.

## Rotating the Token

Set `XDS_AUTH_TOKEN=oldtoken,newtoken` on the control plane and restart it —
both are accepted concurrently. Update `LLMXY_TOKEN` in your envoy deployment
(docker-compose `environment:` or k8s Deployment env) and redeploy. Once all
envoys carry the new token, drop the old one from the control plane env.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| envoy logs `gRPC config stream closed: 16 invalid x-llmxy-token` | Token mismatch — rebuild bootstrap from the UI |
| envoy logs `gRPC config stream closed: 7 unknown remote node ...` | Instance was deleted in control plane, or `node.id` was hand-edited |
| `no healthy upstream` on `/v1/...` | Channel disabled, or upstream API key invalid |
| Admin UI shows offline despite envoy up | `CONTROL_PLANE_PUBLIC_HOST` isn't reachable from envoy's network |
