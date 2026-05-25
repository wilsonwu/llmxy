from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import BalanceTx, BalanceTxType, Plan, Subscription, User
from app.services.billing import renew_subscription

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("/{sub_id}/cancel")
async def cancel_subscription(
    sub_id: int,
    at_period_end: bool = Query(True, description="If true (default, Stripe-like): keep serving until the current period ends, then stop with no refund. If false: terminate immediately and refund the unused quota share."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a subscription.

    - `at_period_end=true` (default): sets `cancel_at_period_end`. The sub
      keeps serving the remaining quota until `current_period_end`, then the
      renewal worker flips it to `expired`. No refund — the user pays for
      what they used.
    - `at_period_end=false`: immediate termination. Refund is prorated by
      *unused quota* (`refund = price * remaining_cents / quota_cents`) and
      credited to wallet.
    """
    sub = await db.get(Subscription, sub_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    if sub.status not in ("active", "past_due"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"subscription is {sub.status}")

    now = datetime.now(timezone.utc)

    if at_period_end:
        if sub.cancel_at_period_end:
            return {
                "ok": True,
                "mode": "at_period_end",
                "subscription_id": sub.id,
                "current_period_end": sub.current_period_end.isoformat(),
                "note": "already scheduled to cancel at period end",
            }
        sub.cancel_at_period_end = True
        sub.canceled_at = now
        await db.commit()
        return {
            "ok": True,
            "mode": "at_period_end",
            "subscription_id": sub.id,
            "current_period_end": sub.current_period_end.isoformat(),
        }

    # Immediate cancel + prorated refund.
    plan = await db.get(Plan, sub.plan_id)
    refund = 0
    if plan and (plan.price_cents or 0) > 0 and (plan.quota_cents or 0) > 0:
        refund = (plan.price_cents * sub.remaining_cents) // plan.quota_cents
        refund = max(0, min(refund, plan.price_cents))

    sub.status = "canceled"
    sub.cancel_at_period_end = True
    sub.canceled_at = now
    sub.remaining_cents = 0
    sub.current_period_end = now

    if refund > 0:
        result = await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_cents=User.balance_cents + refund)
            .returning(User.balance_cents)
        )
        new_balance = result.scalar_one()
        user.balance_cents = new_balance
        db.add(
            BalanceTx(
                user_id=user.id,
                type=BalanceTxType.refund,
                amount_cents=refund,
                balance_after=new_balance,
                ref_id=str(sub.id),
                note=f"refund sub#{sub.id} plan {plan.code if plan else ''}",
            )
        )

    await db.commit()
    return {
        "ok": True,
        "mode": "immediate",
        "subscription_id": sub.id,
        "refund_cents": refund,
        "balance_cents": user.balance_cents,
    }


@router.post("/{sub_id}/resume")
async def resume_subscription(
    sub_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Undo a pending `cancel_at_period_end`. Only valid before the current
    period actually ends — once the worker has flipped the sub to `expired`,
    re-subscribe via POST /plans/{id}/subscribe instead."""
    sub = await db.get(Subscription, sub_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    if sub.status not in ("active", "past_due"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"subscription is {sub.status}")
    if not sub.cancel_at_period_end:
        return {"ok": True, "note": "subscription is not scheduled to cancel"}
    sub.cancel_at_period_end = False
    sub.canceled_at = None
    await db.commit()
    return {"ok": True, "subscription_id": sub.id}


@router.post("/{sub_id}/renew")
async def renew_now(
    sub_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger renewal — primarily for past_due subscriptions after
    the user has topped up. No-op if the current period hasn't ended."""
    sub = await db.get(Subscription, sub_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    now = datetime.now(timezone.utc)
    if sub.status == "active" and sub.current_period_end > now:
        return {"ok": True, "note": "current period still active, nothing to do"}
    if sub.status in ("canceled", "expired"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"subscription is {sub.status}; subscribe again instead")
    ok, reason = await renew_subscription(db, sub)
    await db.commit()
    return {"ok": ok, "subscription_id": sub.id, "status": sub.status, "reason": reason}
