from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Plan
from app.schemas import PlanOut

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Plan).where(Plan.active.is_(True)).order_by(Plan.price_cents))).scalars().all()
    return rows
