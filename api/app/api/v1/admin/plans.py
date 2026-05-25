from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Plan, User
from app.schemas import PlanIn, PlanOut

router = APIRouter(prefix="/plans", tags=["admin-plans"])


@router.get("", response_model=list[PlanOut])
async def list_plans(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Plan).order_by(Plan.id))).scalars().all()


@router.post("", response_model=PlanOut)
async def create_plan(req: PlanIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = Plan(**req.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{pid}", response_model=PlanOut)
async def update_plan(pid: int, req: PlanIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Plan, pid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    for k, v in req.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{pid}")
async def delete_plan(pid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Plan, pid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    return {"ok": True}
