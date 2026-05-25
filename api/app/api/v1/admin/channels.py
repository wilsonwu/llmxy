from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Channel, User
from app.schemas import ChannelIn, ChannelOut

router = APIRouter(prefix="/channels", tags=["admin-channels"])


@router.get("", response_model=list[ChannelOut])
async def list_channels(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Channel).order_by(Channel.id))).scalars().all()


@router.post("", response_model=ChannelOut)
async def create_channel(req: ChannelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = Channel(**req.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{cid}", response_model=ChannelOut)
async def update_channel(cid: int, req: ChannelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Channel, cid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    for k, v in req.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{cid}")
async def delete_channel(cid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Channel, cid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    return {"ok": True}
