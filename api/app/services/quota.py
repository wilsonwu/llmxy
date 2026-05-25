from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models import Plan, Subscription


async def rate_limit(user_id: int, per_min: int | None = None) -> bool:
    """Sliding-ish minute bucket. Returns True if under limit."""
    limit = per_min if per_min and per_min > 0 else settings.DEFAULT_RATE_LIMIT_RPM
    r = get_redis()
    key = f"rl:u:{user_id}"
    n = await r.incr(key)
    if n == 1:
        await r.expire(key, 60)
    return n <= limit


async def user_rpm(db: AsyncSession, user_id: int) -> int:
    """Resolve user's per-minute cap from active subscription plan, falling back to default."""
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(
            select(Plan.rate_limit_jsonb)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.end_at > now,
            )
            .order_by(Subscription.end_at.desc())
            .limit(1)
        )
    ).first()
    if row and row[0]:
        rpm = row[0].get("rpm") if isinstance(row[0], dict) else None
        if isinstance(rpm, int) and rpm > 0:
            return rpm
    return settings.DEFAULT_RATE_LIMIT_RPM
