from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_admin
from app.db.session import get_db
from app.models import EnvoyInstance, EnvoyStatus, User
from app.schemas import EnvoyInstanceIn, EnvoyInstanceOut

router = APIRouter(prefix="/envoy", tags=["admin-envoy"])


def _paths(name: str) -> tuple[str, str]:
    cfg = os.path.abspath(os.path.join(settings.ENVOY_CONFIG_ROOT, name))
    log = os.path.abspath(os.path.join(settings.ENVOY_LOG_ROOT, name))
    return cfg, log


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
    if req.listen_port == req.admin_port:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "listen_port and admin_port must differ")
    cfg_dir, log_dir = _paths(req.name)
    os.makedirs(cfg_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    inst = EnvoyInstance(
        name=req.name,
        listen_port=req.listen_port,
        admin_port=req.admin_port,
        status=EnvoyStatus.stopped,
        config_dir=cfg_dir,
        log_dir=log_dir,
    )
    db.add(inst)
    await db.commit()
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
    if inst.status == EnvoyStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "stop the instance before deleting")
    await db.delete(inst)
    await db.commit()


# Lifecycle / observability endpoints below are wired in Phase 4 (runtime.py).
# Stubs returning 501 keep the surface visible to the admin UI from day one.

@router.post("/instances/{inst_id}/start")
async def start_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime  # lazy import; runtime lands in Phase 4
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await runtime.start(db, inst)


@router.post("/instances/{inst_id}/stop")
async def stop_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await runtime.stop(db, inst)


@router.post("/instances/{inst_id}/restart")
async def restart_instance(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
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
    from app.services.envoy import config as envoy_config
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await envoy_config.regenerate(db, inst)
    await db.commit()
    await db.refresh(inst)
    return inst


@router.get("/instances/{inst_id}/stats")
async def instance_stats(inst_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.services.envoy import runtime
    inst = await db.get(EnvoyInstance, inst_id)
    if not inst:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await runtime.stats(inst)


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
    return {"lines": await runtime.tail_logs(inst, tail)}
