from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, BalanceTx, BalanceTxType, Model, Plan, Subscription, User


def calc_cost_cents(model: Model, prompt_tokens: int, completion_tokens: int) -> int:
    """Rates are micro-cents (1/10000 cent) per 1K tokens."""
    if not model:
        return 0
    micro = prompt_tokens * model.prompt_rate + completion_tokens * model.completion_rate
    return ceil(micro / 10_000_000)


def next_period_end(now: datetime) -> datetime:
    """First day of the next calendar month at 00:00 UTC."""
    now = now.astimezone(timezone.utc)
    year, month = now.year, now.month + 1
    if month > 12:
        year += 1
        month = 1
    return datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)


def initial_period_end(plan: Plan, now: datetime) -> datetime:
    """First period end for a fresh subscription.

    - recurring: until the first of next calendar month (00:00 UTC).
    - one_time: now + plan.duration_days.
    """
    if (plan.plan_type or "recurring") == "one_time":
        days = max(1, int(plan.duration_days or 30))
        return now.astimezone(timezone.utc) + timedelta(days=days)
    return next_period_end(now)


async def active_subscriptions(
    db: AsyncSession, user_id: int, *, lock: bool = False
) -> list[Subscription]:
    """Return active subscriptions with remaining quota for the current period,
    ordered by current_period_end ASC (expiring soonest first).
    Pass lock=True to SELECT FOR UPDATE."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.current_period_end > now,
            Subscription.remaining_cents > 0,
        )
        .order_by(Subscription.current_period_end.asc())
    )
    if lock:
        stmt = stmt.with_for_update()
    return list((await db.execute(stmt)).scalars().all())


async def has_quota(
    db: AsyncSession, user: User, api_key: ApiKey | None
) -> tuple[bool, str]:
    """Check user can spend: either has an active subscription with remaining
    quota, or has wallet balance. Also enforce per-key cap."""
    if api_key and api_key.quota_cents > 0 and api_key.used_cents >= api_key.quota_cents:
        return False, "api key quota exceeded"
    subs = await active_subscriptions(db, user.id)
    if subs:
        return True, ""
    if (user.balance_cents or 0) > 0:
        return True, ""
    return False, "no active subscription and insufficient balance"


# Backward-compat alias.
def check_balance(user: User, api_key: ApiKey | None) -> tuple[bool, str]:
    if api_key and api_key.quota_cents > 0 and api_key.used_cents >= api_key.quota_cents:
        return False, "api key quota exceeded"
    if (user.balance_cents or 0) <= 0:
        return False, "insufficient balance"
    return True, ""


async def charge_user(
    db: AsyncSession,
    user: User,
    api_key: ApiKey | None,
    cost_cents: int,
    ref_id: str | None = None,
    note: str | None = None,
) -> None:
    """Deduct cost. Drains active subscriptions in end_at-ASC order first; any
    remainder falls back to wallet balance. Writes a single BalanceTx whose note
    encodes the breakdown, e.g. "gpt-4o [sub#3:-150;wallet:-50]".
    """
    if cost_cents <= 0:
        return

    remaining = cost_cents
    breakdown: list[str] = []

    subs = await active_subscriptions(db, user.id, lock=True)
    for sub in subs:
        if remaining <= 0:
            break
        take = min(sub.remaining_cents, remaining)
        if take <= 0:
            continue
        # NOTE: do NOT mutate sub.remaining_cents in Python. SQLAlchemy's
        # synchronize_session on the bulk UPDATE below already mirrors the
        # decrement onto the in-session object; a manual decrement would
        # double-deduct on the next flush. We read the post-update value
        # back via RETURNING for the breakdown/logging.
        result = await db.execute(
            update(Subscription)
            .where(Subscription.id == sub.id)
            .values(remaining_cents=Subscription.remaining_cents - take)
            .returning(Subscription.remaining_cents)
        )
        _ = result.scalar_one()
        remaining -= take
        breakdown.append(f"sub#{sub.id}:-{take}")

    wallet_after = user.balance_cents or 0
    if remaining > 0:
        result = await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_cents=User.balance_cents - remaining)
            .returning(User.balance_cents)
        )
        wallet_after = result.scalar_one()
        user.balance_cents = wallet_after
        breakdown.append(f"wallet:-{remaining}")

    if api_key is not None:
        await db.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(used_cents=ApiKey.used_cents + cost_cents)
        )
        api_key.used_cents = (api_key.used_cents or 0) + cost_cents

    full_note = note or ""
    if breakdown:
        full_note = f"{full_note} [{';'.join(breakdown)}]".strip()

    db.add(
        BalanceTx(
            user_id=user.id,
            type=BalanceTxType.consume,
            amount_cents=-cost_cents,
            balance_after=wallet_after,
            ref_id=ref_id,
            note=full_note or None,
        )
    )
    await db.flush()


async def topup_wallet(
    db: AsyncSession,
    user: User,
    amount_cents: int,
    ref_id: str | None,
    note: str | None = None,
) -> None:
    result = await db.execute(
        update(User)
        .where(User.id == user.id)
        .values(balance_cents=User.balance_cents + amount_cents)
        .returning(User.balance_cents)
    )
    new_balance = result.scalar_one()
    user.balance_cents = new_balance
    db.add(
        BalanceTx(
            user_id=user.id,
            type=BalanceTxType.topup,
            amount_cents=amount_cents,
            balance_after=new_balance,
            ref_id=ref_id,
            note=note,
        )
    )
    await db.flush()


# Backward-compat alias.
topup = topup_wallet


async def grant_subscription(
    db: AsyncSession,
    user: User,
    plan: Plan,
    ref_id: str | None = None,
) -> Subscription:
    """Create a new active subscription. Current period runs from now until
    the first day of the next calendar month at 00:00 UTC. Quota is full for
    the first cycle (no proration). Wallet balance is not touched here — the
    caller (subscribe endpoint) deducts the price separately."""
    now = datetime.now(timezone.utc)
    end = initial_period_end(plan, now)
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        start_at=now,
        current_period_start=now,
        current_period_end=end,
        status="active",
        remaining_cents=plan.quota_cents or 0,
    )
    db.add(sub)
    await db.flush()
    db.add(
        BalanceTx(
            user_id=user.id,
            type=BalanceTxType.grant,
            amount_cents=plan.quota_cents or 0,
            balance_after=user.balance_cents or 0,
            ref_id=ref_id,
            note=f"plan {plan.code} sub#{sub.id}",
        )
    )
    await db.flush()
    return sub


async def renew_subscription(
    db: AsyncSession, sub: Subscription
) -> tuple[bool, str]:
    """Attempt to renew a subscription whose current_period_end has passed.

    - Skips and closes if cancel_at_period_end is set (returns success=True with
      reason="canceled at period end").
    - Tries to deduct plan.price_cents from the user's wallet:
        success → advance period, refill remaining_cents, write a `consume`
        BalanceTx, set status=active.
        failure (insufficient balance) → status=past_due, remaining_cents=0,
        store last_renewal_error. Period is NOT advanced; the worker will
        retry on the next tick once the wallet is topped up.

    Caller is responsible for db.commit().
    """
    now = datetime.now(timezone.utc)
    plan = await db.get(Plan, sub.plan_id)
    if not plan:
        sub.status = "expired"
        sub.last_renewal_error = "plan deleted"
        return False, "plan deleted"

    if sub.cancel_at_period_end:
        sub.status = "expired"
        sub.canceled_at = sub.canceled_at or now
        sub.remaining_cents = 0
        return True, "canceled at period end"

    # One-time plans never auto-renew — they simply expire when the period ends.
    if (plan.plan_type or "recurring") == "one_time":
        sub.status = "expired"
        sub.remaining_cents = 0
        sub.last_renewal_error = None
        return True, "one-time plan expired"

    user = await db.get(User, sub.user_id)
    if not user or user.status != "active":
        sub.status = "expired"
        sub.last_renewal_error = "user inactive"
        return False, "user inactive"

    price = plan.price_cents or 0
    balance = user.balance_cents or 0
    if price > balance:
        sub.status = "past_due"
        sub.remaining_cents = 0
        sub.last_renewal_error = f"insufficient balance ({balance} < {price})"
        return False, sub.last_renewal_error

    # Charge wallet and advance the period.
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
                ref_id=f"sub#{sub.id}",
                note=f"plan {plan.code} renewal",
            )
        )

    sub.current_period_start = sub.current_period_end
    sub.current_period_end = next_period_end(sub.current_period_start)
    sub.remaining_cents = plan.quota_cents or 0
    sub.status = "active"
    sub.last_renewal_at = now
    sub.last_renewal_error = None
    return True, "renewed"
