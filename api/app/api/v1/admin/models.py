from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Model, User
from app.schemas import ModelIn, ModelOut

router = APIRouter(prefix="/models", tags=["admin-models"])


@router.get("", response_model=list[ModelOut])
async def list_models(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Model).order_by(Model.id))).scalars().all()


@router.post("", response_model=ModelOut)
async def create_model(req: ModelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = Model(**req.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{mid}", response_model=ModelOut)
async def update_model(mid: int, req: ModelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Model, mid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    for k, v in req.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{mid}")
async def delete_model(mid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Model, mid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    return {"ok": True}
