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


async def active_subscriptions(
    db: AsyncSession, user_id: int, *, lock: bool = False
) -> list[Subscription]:
    """Return active subscriptions with remaining quota, ordered by end_at ASC
    (expiring soonest first). Pass lock=True to SELECT FOR UPDATE."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.end_at > now,
            Subscription.remaining_cents > 0,
        )
        .order_by(Subscription.end_at.asc())
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
    """Create a new active subscription from a plan and write a grant BalanceTx
    for audit. Wallet balance is not touched."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        start_at=now,
        end_at=now + timedelta(days=plan.duration_days or 30),
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
