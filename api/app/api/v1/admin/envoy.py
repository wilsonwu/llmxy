from __future__ import annotations

import os
import shutil
import uuid

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


async def _probe_admin(url: str) -> str | None:
    """Best-effort probe of envoy admin `/ready`. Returns None on success,
    a short human-readable error otherwise."""
    target = url.rstrip("/") + "/ready"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(target)
        if r.status_code == 200:
            return None
        return f"admin probe HTTP {r.status_code}"
    except Exception as e:
        return f"admin unreachable: {e}"


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
        if not req.admin_url:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "admin_url required for remote mode (e.g. http://envoy.example.com:9901)",
            )

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
            node_id=f"llmxy-remote-{uuid.uuid4().hex[:8]}",
            listen_port=req.listen_port,
            admin_port=None,
            admin_url=req.admin_url,
            status=EnvoyStatus.stopped,
            config_dir=None,
            log_dir=None,
        )

    if mode == EnvoyMode.remote:
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
        if req.admin_url is not None:
            inst.admin_url = req.admin_url
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


@router.post("/instances/{inst_id}/regenerate-config", response_model=EnvoyInstanceOut)
async def regenerate_config(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if inst.mode == EnvoyMode.remote:
        from app.services.envoy import xds_server
        inst.config_version = (inst.config_version or 0) + 1
        await db.commit()
        xds_server.notify_node(inst.node_id)
        await db.refresh(inst)
        return inst
    from app.services.envoy import config as envoy_config
    await envoy_config.regenerate(db, inst)
    await db.commit()
    await db.refresh(inst)
    return inst


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
    from app.services.envoy import remote_bootstrap
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if inst.mode != EnvoyMode.remote:
        raise HTTPException(status.HTTP_409_CONFLICT, "bootstrap templates are remote-only")
    return EnvoyBootstrapOut(yaml=remote_bootstrap.render_bootstrap_yaml(inst))
