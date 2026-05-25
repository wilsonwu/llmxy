from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import RoutePolicy, RouteStrategy, User
from app.schemas import RoutePolicyIn, RoutePolicyOut
from app.services.envoy.config import regenerate_all_running

router = APIRouter(prefix="/routes", tags=["admin-routes"])


@router.get("", response_model=list[RoutePolicyOut])
async def list_routes(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(RoutePolicy).order_by(RoutePolicy.id))).scalars().all()


def _to_orm(req: RoutePolicyIn) -> dict:
    return {
        "user_facing_model": req.user_facing_model,
        "strategy": RouteStrategy(req.strategy),
        "targets_jsonb": [t.model_dump() for t in req.targets_jsonb],
        "enabled": req.enabled,
    }


@router.post("", response_model=RoutePolicyOut)
async def create_route(req: RoutePolicyIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = RoutePolicy(**_to_orm(req))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.put("/{rid}", response_model=RoutePolicyOut)
async def update_route(rid: int, req: RoutePolicyIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(RoutePolicy, rid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    for k, v in _to_orm(req).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.delete("/{rid}")
async def delete_route(rid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(RoutePolicy, rid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    await regenerate_all_running(db)
    return {"ok": True}
