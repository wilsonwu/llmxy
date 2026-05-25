from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import (
    BalanceTx,
    BalanceTxType,
    Order,
    OrderStatus,
    PaymentChannel,
    Plan,
    Subscription,
    User,
)
from app.schemas import PlanOut
from app.services.billing import grant_subscription

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Plan).where(Plan.active.is_(True)).order_by(Plan.price_cents))).scalars().all()
    return rows


@router.post("/{plan_id}/subscribe")
async def subscribe_with_balance(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Subscribe to a plan by deducting the price from the user's wallet
    balance. If the balance is insufficient, returns 402 with the shortfall
    so the frontend can redirect to topup."""
    plan = await db.get(Plan, plan_id)
    if not plan or not plan.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")

    now = datetime.now(timezone.utc)
    existing = (
        await db.execute(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.plan_id == plan.id,
                Subscription.status == "active",
                Subscription.current_period_end > now,
            )
        )
    ).scalars().first()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "already_subscribed",
                "message": "you already have an active subscription to this plan",
                "subscription_id": existing.id,
                "current_period_end": existing.current_period_end.isoformat(),
            },
        )

    price = plan.price_cents or 0
    balance = user.balance_cents or 0
    if price > 0 and balance < price:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "insufficient_balance",
                "message": "insufficient balance",
                "balance_cents": balance,
                "price_cents": price,
                "shortfall_cents": price - balance,
                "plan_id": plan.id,
            },
        )

    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        amount_cents=price,
        channel=PaymentChannel.manual,
        status=OrderStatus.paid,
        paid_at=datetime.now(timezone.utc),
        provider_order_id=f"wallet-{user.id}",
    )
    db.add(order)
    await db.flush()

    if price > 0:
        result = await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_cents=User.balance_cents - price)
            .returning(User.balance_cents)
        )
        new_balance = result.scalar_one()
        user.balance_cents = new_balance
        db.add(
            BalanceTx(
                user_id=user.id,
                type=BalanceTxType.consume,
                amount_cents=-price,
                balance_after=new_balance,
                ref_id=str(order.id),
                note=f"plan {plan.code} sub (wallet)",
            )
        )

    sub = await grant_subscription(db, user, plan, ref_id=str(order.id))
    await db.commit()
    return {
        "ok": True,
        "order_id": order.id,
        "subscription_id": sub.id,
        "balance_cents": user.balance_cents,
    }

