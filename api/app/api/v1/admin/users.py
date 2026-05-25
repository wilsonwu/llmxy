from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.core.security import hash_password
from app.db.session import get_db
from app.models import Plan, Subscription, UsageLog, User, UserStatus
from app.schemas import PaginatedResp, SubscriptionOut, UserDetailOut, UserOut

router = APIRouter(prefix="/users", tags=["admin-users"])


@router.get("", response_model=PaginatedResp)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    base = select(User).order_by(desc(User.id))
    if q:
        base = base.where(User.email.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResp(
        items=[UserOut.model_validate(r).model_dump() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{user_id}/disable", response_model=UserOut)
async def disable_user(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    u.status = UserStatus.disabled
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/{user_id}/enable", response_model=UserOut)
async def enable_user(user_id: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    u.status = UserStatus.active
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/{user_id}/balance/adjust", response_model=UserOut)
async def adjust_balance(
    user_id: int,
    amount_cents: int,
    note: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models import BalanceTx, BalanceTxType

    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    u.balance_cents = max((u.balance_cents or 0) + amount_cents, 0)
    db.add(
        BalanceTx(
            user_id=u.id,
            type=BalanceTxType.grant if amount_cents >= 0 else BalanceTxType.refund,
            amount_cents=amount_cents,
            balance_after=u.balance_cents,
            note=note or "admin adjust",
        )
    )
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    new_password: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    u.password_hash = hash_password(new_password)
    await db.commit()
    return {"ok": True}


@router.get("/{user_id}/detail", response_model=UserDetailOut)
async def user_detail(
    user_id: int,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    sub_rows = (
        await db.execute(
            select(Subscription, Plan.code, Plan.name)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user_id)
            .order_by(desc(Subscription.id))
        )
    ).all()
    subs: list[SubscriptionOut] = []
    for sub, code, name in sub_rows:
        item = SubscriptionOut.model_validate(sub)
        item.plan_code = code
        item.plan_name = name
        subs.append(item)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    spent_total = (
        await db.execute(
            select(func.coalesce(func.sum(UsageLog.cost_cents), 0)).where(UsageLog.user_id == user_id)
        )
    ).scalar_one()
    spent_30d = (
        await db.execute(
            select(func.coalesce(func.sum(UsageLog.cost_cents), 0)).where(
                UsageLog.user_id == user_id, UsageLog.created_at >= cutoff
            )
        )
    ).scalar_one()
    req_total = (
        await db.execute(select(func.count(UsageLog.id)).where(UsageLog.user_id == user_id))
    ).scalar_one()
    req_30d = (
        await db.execute(
            select(func.count(UsageLog.id)).where(
                UsageLog.user_id == user_id, UsageLog.created_at >= cutoff
            )
        )
    ).scalar_one()

    return UserDetailOut(
        user=UserOut.model_validate(u),
        subscriptions=subs,
        spent_total_cents=int(spent_total or 0),
        spent_30d_cents=int(spent_30d or 0),
        requests_total=int(req_total or 0),
        requests_30d=int(req_30d or 0),
    )
