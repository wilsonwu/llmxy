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
3. **Copy the bootstrap** (Bootstrap button on the instance row). The
   yaml is self-contained — it already has the right `node.id`, control
   plane host, ports, and token.

Save the bootstrap as `bootstrap.yaml` next to one of the deployment
templates below.

## Option A — Docker Compose

```sh
# bootstrap.yaml sits next to docker-compose.yaml
docker compose -f docker-compose.yaml up -d
docker compose logs -f envoy
```

Envoy now serves on `localhost:9000`. The admin UI should show the instance
turning green within a few seconds; the "last seen" column updates on each
ALS heartbeat.

## Option B — Kubernetes

```sh
kubectl create configmap llmxy-envoy --from-file=bootstrap.yaml \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k kubernetes/
kubectl rollout status deploy/llmxy-envoy
```

`Service` is `LoadBalancer` by default; switch to `ClusterIP` / `NodePort`
per your environment. Scale by editing `Deployment.spec.replicas` — every
replica uses the same bootstrap.yaml and talks to the same control plane.

## Verifying End-to-End

1. Local: `curl http://<envoy>:9901/ready` returns `LIVE`.
2. Control plane: instance shows green, last seen seconds ago, ADS connected.
3. Send a request: `curl http://<envoy>:9000/v1/chat/completions -H ...` —
   the relay billing log gets a fresh row.
4. Hot reload: change a channel in the admin UI and click Push — within a
   second envoy logs `cds: add/update cluster ...` with no restart.

## Rotating the Token

Update `XDS_AUTH_TOKEN` in the control plane env and restart it. Re-copy
the bootstrap from the admin UI (it embeds the new token), redeploy envoy.
Until that's done, the old token will get `UNAUTHENTICATED` on every
reconnect.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| envoy logs `gRPC config stream closed: 16 invalid x-llmxy-token` | Token mismatch — rebuild bootstrap from the UI |
| envoy logs `gRPC config stream closed: 7 unknown remote node ...` | Instance was deleted in control plane, or `node.id` was hand-edited |
| `no healthy upstream` on `/v1/...` | Channel disabled, or upstream API key invalid |
| Admin UI shows offline despite envoy up | `CONTROL_PLANE_PUBLIC_HOST` isn't reachable from envoy's network |
