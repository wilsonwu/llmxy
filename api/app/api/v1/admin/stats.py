from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import ApiKey, UsageLog, User
from app.schemas import StatsOut

router = APIRouter(prefix="/stats", tags=["admin-stats"])


@router.get("", response_model=StatsOut)
async def stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    keys_total = (await db.execute(select(func.count(ApiKey.id)))).scalar_one()
    cost_total = (await db.execute(select(func.coalesce(func.sum(UsageLog.cost_cents), 0)))).scalar_one()
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    req_today = (
        await db.execute(select(func.count(UsageLog.id)).where(UsageLog.created_at >= since))
    ).scalar_one()
    cost_today = (
        await db.execute(select(func.coalesce(func.sum(UsageLog.cost_cents), 0)).where(UsageLog.created_at >= since))
    ).scalar_one()
    return StatsOut(
        users_total=users_total,
        api_keys_total=keys_total,
        requests_today=req_today,
        cost_today_cents=cost_today,
        cost_total_cents=cost_total,
    )
