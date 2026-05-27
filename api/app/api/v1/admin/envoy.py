from __future__ import annotations

import os
import shutil

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_admin
from app.db.session import get_db
from app.models import EnvoyInstance, EnvoyMode, EnvoyStatus, User
from app.schemas import (
    EnvoyBootstrapOut,
    EnvoyConnectionOut,
    EnvoyInstanceIn,
    EnvoyInstanceOut,
    EnvoyInstanceUpdate,
    EnvoyManifestsOut,
    EnvoyManifestsPreviewIn,
    EnvoyTestConnIn,
    EnvoyTestConnOut,
)

router = APIRouter(prefix="/envoy", tags=["admin-envoy"])


def _paths(name: str) -> tuple[str, str]:
    cfg = os.path.abspath(os.path.join(settings.ENVOY_CONFIG_ROOT, name))
    log_dir = os.path.abspath(os.path.join(settings.ENVOY_LOG_ROOT, name))
    return cfg, log_dir


def _resolve_envoy_bin() -> str | None:
    bin_setting = settings.ENVOY_BIN
    if os.path.isabs(bin_setting):
        return bin_setting if os.path.isfile(bin_setting) and os.access(bin_setting, os.X_OK) else None
    return shutil.which(bin_setting)


def _ensure_local(inst: EnvoyInstance) -> None:
    if inst.mode == EnvoyMode.remote:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "operation not applicable to remote envoy nodes",
        )


def _normalize_host(raw: str) -> str:
    """Accept `host`, `host:port`, or `scheme://host[:port][/...]` and return
    just the hostname/IP. We then build admin_url from host + admin_port
    ourselves — operators shouldn't have to remember the URL shape."""
    s = raw.strip()
    if "://" in s:
        from urllib.parse import urlparse
        u = urlparse(s)
        return u.hostname or s
    # bare `host:port`
    if s.count(":") == 1 and not s.startswith("["):
        return s.split(":", 1)[0]
    return s


def _build_remote_admin_url(host: str | None, admin_port: int | None, fallback: str | None) -> str | None:
    """Resolve the EXTERNAL admin_url for a remote instance (the URL the
    control plane probes /ready against).

    `admin_port` here is the externally-reachable admin port the operator
    typed into the form AFTER deploying envoy — already the NodePort for k8s
    (30001) or the host port for docker --network=host (9001). No derivation
    or translation: we just join host:port. Explicit `admin_url` fallback
    wins for unusual setups (e.g. ingress-fronted access).
    """
    if fallback:
        return fallback.strip()
    if host and admin_port:
        return f"http://{_normalize_host(host)}:{admin_port}"
    return None


@router.post("/test-connection", response_model=EnvoyTestConnOut)
async def test_connection(
    req: EnvoyTestConnIn,
    _: User = Depends(require_admin),
):
    """Probe an arbitrary envoy admin URL — used by the UI's create dialog so
    operators can sanity-check `admin_url` (and indirectly the network path
    between control plane and the envoy node) before saving the row."""
    import time
    target = req.admin_url.rstrip("/") + "/ready"
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(target)
        latency = int((time.time() - t0) * 1000)
        return EnvoyTestConnOut(
            ok=r.status_code == 200,
            status_code=r.status_code,
            latency_ms=latency,
            error=None if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:120]}",
        )
    except Exception as e:
        return EnvoyTestConnOut(ok=False, error=str(e))


@router.get("/instances", response_model=list[EnvoyInstanceOut])
async def list_instances(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(EnvoyInstance).order_by(EnvoyInstance.id))).scalars().all()
    return rows


@router.post("/instances", response_model=EnvoyInstanceOut, status_code=status.HTTP_201_CREATED)
async def create_instance(
    req: EnvoyInstanceIn,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    mode = EnvoyMode(req.mode)
    if mode == EnvoyMode.local:
        if not settings.ENVOY_LOCAL_MODE_ENABLED:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "local-mode envoy is disabled (ENVOY_LOCAL_MODE_ENABLED=false). "
                "Create a remote instance and deploy envoy externally instead.",
            )
        if req.admin_port is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "admin_port required for local mode")
        if req.listen_port == req.admin_port:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "listen_port and admin_port must differ")
        if _resolve_envoy_bin() is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"envoy binary not found (ENVOY_BIN={settings.ENVOY_BIN!r}). "
                "Install envoy (e.g. `brew install envoy`) or set ENVOY_BIN to an absolute path in .env.",
            )
    else:
        # Remote: require host + admin_port. The expected flow is: operator
        # opens the create dialog → picks remote → UI shows deploy manifests
        # via /manifests/preview (with deterministic node_id derived from
        # name) → operator deploys → fills in the real host:port → submits.
        # Without a host we can't probe /ready, and the row would show
        # offline/error indefinitely, which is worse than refusing.
        if req.admin_port is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "admin_port required for remote mode (envoy's admin /ready port)",
            )
        if not (req.host or req.admin_url):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "host required for remote mode — deploy envoy first, then submit with its reachable host",
            )
        admin_url = _build_remote_admin_url(req.host, req.admin_port, req.admin_url)

    # Port uniqueness only matters for local mode (those ports bind on this
    # host). For remote, listen_port is on the remote envoy host — many remote
    # nodes can legitimately reuse the same port number.
    name_clash = (
        await db.execute(select(EnvoyInstance).where(EnvoyInstance.name == req.name))
    ).scalar_one_or_none()
    if name_clash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"name already in use by instance id={name_clash.id} ({name_clash.name})",
        )
    if mode == EnvoyMode.local:
        port_filters = [EnvoyInstance.listen_port == req.listen_port]
        if req.admin_port:
            port_filters.append(EnvoyInstance.admin_port == req.admin_port)
        port_clash = (
            await db.execute(
                select(EnvoyInstance).where(
                    EnvoyInstance.mode == EnvoyMode.local,
                    or_(*port_filters),
                )
            )
        ).scalars().first()
        if port_clash:
            reason = "listen_port" if port_clash.listen_port == req.listen_port else "admin_port"
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{reason} already in use by local instance id={port_clash.id} ({port_clash.name})",
            )

    if mode == EnvoyMode.local:
        cfg_dir, log_dir = _paths(req.name)
        os.makedirs(cfg_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
        admin_url = f"http://127.0.0.1:{req.admin_port}"
        inst = EnvoyInstance(
            name=req.name,
            mode=mode,
            node_id=f"llmxy-{req.name}",
            listen_port=req.listen_port,
            admin_port=req.admin_port,
            admin_url=admin_url,
            status=EnvoyStatus.stopped,
            config_dir=cfg_dir,
            log_dir=log_dir,
        )
    else:
        inst = EnvoyInstance(
            name=req.name,
            mode=mode,
            node_id=f"llmxy-remote-{req.name}",
            listen_port=req.listen_port,
            # Persist admin_port for remote too — the bootstrap template uses
            # it so the envoy admin endpoint inside the pod matches what the
            # control plane probes.
            admin_port=req.admin_port,
            admin_url=admin_url,
            status=EnvoyStatus.stopped,
            config_dir=None,
            log_dir=None,
        )

    if mode == EnvoyMode.remote and admin_url:
        # One-shot health probe so the row reflects reachability immediately
        # instead of waiting for the next health-loop tick (which only picks up
        # rows already in running/error). Failure is non-fatal — the operator
        # may be registering the node before deploying envoy on the other side.
        from datetime import datetime, timezone
        from app.services.envoy import runtime as envoy_runtime
        ok = await envoy_runtime._probe_one(inst)
        inst.last_health_at = datetime.now(timezone.utc)
        if ok:
            inst.status = EnvoyStatus.running
            inst.last_error = None
        else:
            inst.status = EnvoyStatus.error
            inst.last_error = "initial /ready probe failed"

    db.add(inst)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"unique constraint violated: {e.orig}")
    await db.refresh(inst)
    return inst


@router.patch("/instances/{inst_id}", response_model=EnvoyInstanceOut)
async def update_instance(
    inst_id: int,
    req: EnvoyInstanceUpdate,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update mutable fields on an envoy instance. `mode` and `node_id` are
    immutable. For local-mode instances, port changes only take effect after
    restart — the caller is expected to restart explicitly."""
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    if req.name is not None and req.name != inst.name:
        clash = (
            await db.execute(
                select(EnvoyInstance).where(
                    EnvoyInstance.name == req.name,
                    EnvoyInstance.id != inst_id,
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"name already in use by instance id={clash.id}",
            )
        inst.name = req.name

    if inst.mode == EnvoyMode.local:
        new_listen = req.listen_port if req.listen_port is not None else inst.listen_port
        new_admin = req.admin_port if req.admin_port is not None else inst.admin_port
        if not new_admin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "admin_port required for local mode")
        if new_listen == new_admin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "listen_port and admin_port must differ")
        if req.listen_port is not None and req.listen_port != inst.listen_port:
            clash = (
                await db.execute(
                    select(EnvoyInstance).where(
                        EnvoyInstance.mode == EnvoyMode.local,
                        EnvoyInstance.listen_port == req.listen_port,
                        EnvoyInstance.id != inst_id,
                    )
                )
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"listen_port in use by local instance id={clash.id}",
                )
            inst.listen_port = req.listen_port
        if req.admin_port is not None and req.admin_port != inst.admin_port:
            clash = (
                await db.execute(
                    select(EnvoyInstance).where(
                        EnvoyInstance.mode == EnvoyMode.local,
                        EnvoyInstance.admin_port == req.admin_port,
                        EnvoyInstance.id != inst_id,
                    )
                )
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"admin_port in use by local instance id={clash.id}",
                )
            inst.admin_port = req.admin_port
            inst.admin_url = f"http://127.0.0.1:{req.admin_port}"
    else:
        if req.listen_port is not None:
            inst.listen_port = req.listen_port
        # For remote, recompute admin_url if any of host / admin_port / admin_url
        # changed. host+admin_port is the preferred input; admin_url stays for
        # back-compat with older clients.
        host_changed = req.host is not None
        port_changed = req.admin_port is not None
        url_changed = req.admin_url is not None
        if host_changed or port_changed or url_changed:
            # Derive new admin_url from whatever the caller supplied, falling
            # back to existing fields so partial updates work.
            new_host = req.host if host_changed else None
            new_port = req.admin_port if port_changed else inst.admin_port
            new_url = req.admin_url if url_changed else None
            # If only host or only admin_port supplied, pair with the existing
            # counterpart so we can still build host:port.
            if host_changed and not port_changed and inst.admin_port:
                new_port = inst.admin_port
            if port_changed and not host_changed and inst.admin_url:
                from urllib.parse import urlparse
                u = urlparse(inst.admin_url)
                if u.hostname:
                    new_host = u.hostname
            derived = _build_remote_admin_url(new_host, new_port, new_url)
            if not derived:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "remote mode requires `host` + `admin_port` to update connection info",
                )
            inst.admin_url = derived
            if port_changed:
                inst.admin_port = req.admin_port
            # Re-probe so the operator sees the new URL's reachability right
            # away instead of waiting for the next health-loop tick.
            from datetime import datetime, timezone
            from app.services.envoy import runtime as envoy_runtime
            ok = await envoy_runtime._probe_one(inst)
            inst.last_health_at = datetime.now(timezone.utc)
            if ok:
                inst.status = EnvoyStatus.running
                inst.last_error = None
            else:
                inst.status = EnvoyStatus.error
                inst.last_error = "post-update /ready probe failed"

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"unique constraint violated: {e.orig}")
    await db.refresh(inst)
    return inst


@router.delete("/instances/{inst_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    inst_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if inst.mode == EnvoyMode.local and inst.status == EnvoyStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "stop the instance before deleting")
    # Capture local-mode paths before deleting the row so we can clean up
    # rendered config + logs. Failures here shouldn't block deletion.
    cfg_dir = inst.config_dir if inst.mode == EnvoyMode.local else None
    log_dir = inst.log_dir if inst.mode == EnvoyMode.local else None
    await db.delete(inst)
    await db.commit()
    for d in (cfg_dir, log_dir):
        if not d:
            continue
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


@router.post("/instances/{inst_id}/start")
async def start_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _ensure_local(inst)
    return await runtime.start(db, inst)


@router.post("/instances/{inst_id}/stop")
async def stop_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _ensure_local(inst)
    return await runtime.stop(db, inst)


@router.post("/instances/{inst_id}/restart")
async def restart_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _ensure_local(inst)
    return await runtime.restart(db, inst)


@router.post("/instances/{inst_id}/reload")
async def reload_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await runtime.reload(db, inst)


@router.get("/instances/{inst_id}/stats")
async def instance_stats(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Curated stats from envoy admin. Works for both local and remote — uses
    `admin_url` which is auto-derived for local and operator-supplied for remote."""
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not inst.admin_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "admin_url not set")
    url = (
        inst.admin_url.rstrip("/")
        + "/stats?format=json&filter=(cluster\\.|http\\.ingress_http\\.downstream)"
    )
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"stats fetch failed: {e}") from e
    wanted_suffixes = (
        "upstream_rq_total", "upstream_rq_2xx", "upstream_rq_4xx",
        "upstream_rq_5xx", "upstream_cx_active",
        "downstream_rq_total", "downstream_rq_2xx", "downstream_rq_4xx",
        "downstream_rq_5xx",
    )
    out: dict[str, int] = {}
    for s in data.get("stats", []) or []:
        name = s.get("name", "")
        if any(name.endswith(suf) for suf in wanted_suffixes):
            v = s.get("value")
            if isinstance(v, (int, float)):
                out[name] = int(v)
    return {"counters": out}


@router.get("/instances/{inst_id}/metrics")
async def instance_metrics(
    inst_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Proxy envoy admin /stats/prometheus so a scraper can hit one URL per
    instance through the control plane (avoids exposing envoy admin directly).
    Returns text/plain in Prometheus exposition format."""
    from fastapi.responses import PlainTextResponse
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not inst.admin_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "admin_url not set")
    url = inst.admin_url.rstrip("/") + "/stats/prometheus"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"metrics fetch failed: {e}") from e
    return PlainTextResponse(r.text, media_type="text/plain; version=0.0.4")


@router.get("/instances/{inst_id}/logs")
async def instance_logs(
    inst_id: int,
    tail: int = Query(200, ge=1, le=5000),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    _ensure_local(inst)
    return {"lines": await runtime.tail_logs(inst, tail)}


# ----- Remote-node-only endpoints -------------------------------------------

@router.get("/instances/{inst_id}/connection", response_model=EnvoyConnectionOut)
async def get_connection(
    inst_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.envoy import xds_server
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return EnvoyConnectionOut(
        node_id=inst.node_id,
        ads_connected=inst.node_id in xds_server._node_events,
        last_seen_at=inst.last_seen_at,
        last_xds_version=inst.last_xds_version,
    )


@router.get("/instances/{inst_id}/bootstrap-template", response_model=EnvoyBootstrapOut)
async def bootstrap_template(
    inst_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return a ready-to-paste envoy bootstrap.yaml. Operator saves it on the
    envoy host and runs `envoy -c bootstrap.yaml`."""
    from app.services.envoy import bootstrap as envoy_bootstrap
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if inst.mode != EnvoyMode.remote:
        raise HTTPException(status.HTTP_409_CONFLICT, "bootstrap templates are remote-only")
    return EnvoyBootstrapOut(yaml=envoy_bootstrap.render_bootstrap_yaml(inst))


@router.post("/manifests/preview", response_model=EnvoyManifestsOut)
async def manifests_preview(
    req: EnvoyManifestsPreviewIn,
    _: User = Depends(require_admin),
):
    """Render deploy artifacts for a not-yet-created remote instance. Only
    `name` is meaningful — node_id is derived from it with the same rule the
    POST /instances handler uses, so the manifest the operator deploys here
    matches the row that will be persisted on create. Ports in the manifest
    are FIXED (envoy standards 9000/9001 + NodePort 30000/30001) and never
    sourced from form input; the form's listen/admin port fields are filled
    in LATER by the operator with the real reachable values."""
    from app.services.envoy import bootstrap as envoy_bootstrap
    transient = EnvoyInstance(
        name=req.name,
        mode=EnvoyMode.remote,
        node_id=f"llmxy-remote-{req.name}",
        listen_port=envoy_bootstrap.REMOTE_BIND_LISTEN_PORT,
        admin_port=envoy_bootstrap.REMOTE_BIND_ADMIN_PORT,
        status=EnvoyStatus.stopped,
    )
    k8s_yaml = envoy_bootstrap.render_k8s_manifest(transient)
    return EnvoyManifestsOut(
        bootstrap_yaml=envoy_bootstrap.render_bootstrap_yaml(transient),
        k8s_yaml=k8s_yaml,
        docker_run=envoy_bootstrap.render_docker_run(transient),
        node_id=transient.node_id,
        control_plane_host=settings.CONTROL_PLANE_PUBLIC_HOST,
        xds_port=settings.XDS_GRPC_PORT,
        als_port=settings.ALS_GRPC_PORT,
        k8s_listen_nodeport=envoy_bootstrap.REMOTE_K8S_LISTEN_NODEPORT,
        k8s_admin_nodeport=envoy_bootstrap.REMOTE_K8S_ADMIN_NODEPORT,
    )


@router.get("/instances/{inst_id}/manifests", response_model=EnvoyManifestsOut)
async def manifests(
    inst_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bundle of deploy artifacts for a remote instance: bootstrap.yaml, a
    `kubectl apply -f` manifest (ConfigMap + Deployment + Service), and a
    docker-run script. All three embed this instance's node_id and the
    control plane's xDS/ALS endpoints — no further string substitution
    needed unless the operator wants to override the namespace / image."""
    from app.services.envoy import bootstrap as envoy_bootstrap
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if inst.mode != EnvoyMode.remote:
        raise HTTPException(status.HTTP_409_CONFLICT, "manifests are remote-only")
    try:
        k8s_yaml = envoy_bootstrap.render_k8s_manifest(inst)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return EnvoyManifestsOut(
        bootstrap_yaml=envoy_bootstrap.render_bootstrap_yaml(inst),
        k8s_yaml=k8s_yaml,
        docker_run=envoy_bootstrap.render_docker_run(inst),
        node_id=inst.node_id,
        control_plane_host=settings.CONTROL_PLANE_PUBLIC_HOST,
        xds_port=settings.XDS_GRPC_PORT,
        als_port=settings.ALS_GRPC_PORT,
        k8s_listen_nodeport=envoy_bootstrap.REMOTE_K8S_LISTEN_NODEPORT,
        k8s_admin_nodeport=envoy_bootstrap.REMOTE_K8S_ADMIN_NODEPORT,
    )
