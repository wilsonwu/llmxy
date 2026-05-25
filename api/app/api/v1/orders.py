from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import Order, OrderStatus, PaymentChannel, Plan, User
from app.schemas import OrderCreate, OrderOut, PaymentInitResp
from app.services.billing import grant_subscription, topup_wallet
from app.services.payment import get_provider

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
async def my_orders(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()))).scalars().all()
    return rows


@router.post("", response_model=PaymentInitResp)
async def create_order(
    req: OrderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider = get_provider(req.channel)
    if not provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported channel {req.channel}")
    amount = req.amount_cents
    plan_id = req.plan_id
    if plan_id:
        plan = await db.get(Plan, plan_id)
        if not plan or not plan.active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
        amount = plan.price_cents
    if amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be > 0")

    order = Order(
        user_id=user.id,
        plan_id=plan_id,
        amount_cents=amount,
        channel=PaymentChannel(req.channel),
        status=OrderStatus.pending,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    init = await provider.create_payment(order)
    return PaymentInitResp(order_id=order.id, channel=req.channel, **{k: init.get(k) for k in ("pay_url", "qr_code", "raw")})


# ---- Payment callbacks (stub-friendly) ----
payments_router = APIRouter(prefix="/payments", tags=["payments"])


@payments_router.post("/{channel}/callback")
async def payment_callback(
    channel: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    provider = get_provider(channel)
    if not provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown channel")
    try:
        payload = await request.json()
    except Exception:
        payload = dict((await request.form()).items())
    ok, provider_order_id = await provider.verify_callback(payload, dict(request.headers))
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")
    order_id = int(payload.get("order_id"))
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    if order.status == OrderStatus.paid:
        return {"ok": True, "msg": "already paid"}
    order.status = OrderStatus.paid
    order.paid_at = datetime.now(timezone.utc)
    order.provider_order_id = provider_order_id
    user = await db.get(User, order.user_id)
    if order.plan_id:
        plan = await db.get(Plan, order.plan_id)
        if plan:
            await grant_subscription(db, user, plan, ref_id=str(order.id))
        else:
            await topup_wallet(db, user, order.amount_cents, ref_id=str(order.id), note=f"order#{order.id} {channel}")
    else:
        await topup_wallet(db, user, order.amount_cents, ref_id=str(order.id), note=f"order#{order.id} {channel}")
    await db.commit()
    return {"ok": True}


@payments_router.get("/{channel}/mock-pay")
async def mock_pay(channel: str, order_id: int, db: AsyncSession = Depends(get_db)):
    """Convenience endpoint to simulate a successful callback in dev."""
    provider = get_provider(channel)
    if not provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown channel")
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    if order.status != OrderStatus.paid:
        order.status = OrderStatus.paid
        order.paid_at = datetime.now(timezone.utc)
        order.provider_order_id = f"mock-{channel}-{order.id}"
        user = await db.get(User, order.user_id)
        if order.plan_id:
            plan = await db.get(Plan, order.plan_id)
            if plan:
                await grant_subscription(db, user, plan, ref_id=str(order.id))
            else:
                await topup_wallet(db, user, order.amount_cents, ref_id=str(order.id), note=f"mock {channel}")
        else:
            await topup_wallet(db, user, order.amount_cents, ref_id=str(order.id), note=f"mock {channel}")
        await db.commit()
    return {"ok": True, "order_id": order.id, "status": order.status.value}
