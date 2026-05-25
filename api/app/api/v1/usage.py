from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import BalanceTx, Plan, Subscription, UsageLog, User
from app.schemas import BalanceTxOut, PaginatedResp, SubscriptionOut, UsageLogOut

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/logs", response_model=PaginatedResp)
async def my_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    base = select(UsageLog).where(UsageLog.user_id == user.id).order_by(desc(UsageLog.id))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResp(
        items=[UsageLogOut.model_validate(r).model_dump() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/balance-tx", response_model=PaginatedResp)
async def my_balance_tx(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    base = select(BalanceTx).where(BalanceTx.user_id == user.id).order_by(desc(BalanceTx.id))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResp(
        items=[BalanceTxOut.model_validate(r).model_dump() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/subscriptions", response_model=list[SubscriptionOut])
async def my_subscriptions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Subscription, Plan.code, Plan.name)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user.id)
            .order_by(desc(Subscription.id))
        )
    ).all()
    out: list[SubscriptionOut] = []
    for sub, code, name in rows:
        item = SubscriptionOut.model_validate(sub)
        item.plan_code = code
        item.plan_name = name
        out.append(item)
    return out
