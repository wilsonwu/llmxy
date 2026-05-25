from __future__ import annotations

from math import ceil

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, BalanceTx, BalanceTxType, Model, User


def calc_cost_cents(model: Model, prompt_tokens: int, completion_tokens: int) -> int:
    """Rates are micro-cents (1/10000 cent) per 1K tokens.
    cost_cents = ceil((prompt*pr + completion*cr) / 10000 / 1000)
    """
    if not model:
        return 0
    micro = prompt_tokens * model.prompt_rate + completion_tokens * model.completion_rate
    return ceil(micro / 10_000_000)


async def charge_user(
    db: AsyncSession,
    user: User,
    api_key: ApiKey | None,
    cost_cents: int,
    ref_id: str | None = None,
    note: str | None = None,
) -> None:
    if cost_cents <= 0:
        return
    new_balance = max(user.balance_cents - cost_cents, 0)
    user.balance_cents = new_balance
    if api_key is not None:
        api_key.used_cents = (api_key.used_cents or 0) + cost_cents
    db.add(
        BalanceTx(
            user_id=user.id,
            type=BalanceTxType.consume,
            amount_cents=-cost_cents,
            balance_after=new_balance,
            ref_id=ref_id,
            note=note,
        )
    )
    await db.flush()


async def topup(
    db: AsyncSession,
    user: User,
    amount_cents: int,
    ref_id: str | None,
    note: str | None = None,
) -> None:
    user.balance_cents = (user.balance_cents or 0) + amount_cents
    db.add(
        BalanceTx(
            user_id=user.id,
            type=BalanceTxType.topup,
            amount_cents=amount_cents,
            balance_after=user.balance_cents,
            ref_id=ref_id,
            note=note,
        )
    )
    await db.flush()


def check_balance(user: User, api_key: ApiKey | None) -> tuple[bool, str]:
    if user.balance_cents <= 0:
        return False, "insufficient balance"
    if api_key and api_key.quota_cents > 0 and api_key.used_cents >= api_key.quota_cents:
        return False, "api key quota exceeded"
    return True, ""
