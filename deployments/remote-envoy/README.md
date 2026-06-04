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
   remote envoy will try. Remote envoy must be able to reach that host on
   the API translator HTTP port (`8000`), ALS (`8002`), xDS (`8003`), and
   ext_proc (`8004`). If you run the API manually, bind it to a non-loopback
   interface, for example `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.
2. **Create a remote instance** in the admin UI (Envoy → New instance →
   mode = remote). For Kubernetes, the generated Service exposes NodePort
   `30000` for client traffic and `30001` for admin. For Docker host-network
   mode, Envoy binds `9000` and `9001` directly. The UI derives the `node_id`
   from the instance name and bakes it into the generated artifacts.

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

`Service` defaults to `NodePort` with `30000` (listen) and `30001` (admin),
matching the admin UI generated manifest. Scale by editing
`Deployment.spec.replicas` — every replica shares the same bootstrap and
connects to the same control plane node row.

## Verifying End-to-End

1. Local: `curl http://<envoy>:9001/ready` returns `LIVE` for Docker, or
   `curl http://<node-ip>:30001/ready` for the default Kubernetes NodePort.
2. Control plane: instance shows green, last seen seconds ago, ADS connected.
3. Send a request: `curl http://<envoy>:9000/v1/chat/completions -H ...` —
   the relay billing log gets a fresh row.
4. Hot reload: change a channel in the admin UI and click Sync — within a
   second Envoy receives a fresh xDS push with no restart.

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
| `/v1/...` returns `upstream connect error ... Connection refused` for `translator` | The API HTTP port (`CONTROL_PLANE_PUBLIC_HOST:8000`) is not reachable from remote envoy. Check that uvicorn is bound to `0.0.0.0` and that firewalls/security groups expose port `8000`. |
