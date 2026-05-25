from __future__ import annotations

from app.core.redis import get_redis

REQUESTS_PER_MIN = 600  # default user RPM cap


async def rate_limit(user_id: int, per_min: int = REQUESTS_PER_MIN) -> bool:
    """Sliding-ish minute bucket. Returns True if under limit."""
    r = get_redis()
    key = f"rl:u:{user_id}"
    n = await r.incr(key)
    if n == 1:
        await r.expire(key, 60)
    return n <= per_min
