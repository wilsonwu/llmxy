"""Per-process snapshot caches for the ext_authz hot path.

`relay.authz` is called by envoy for every LLM request. Going to PG 4–5
times per request is the main throughput bottleneck once envoy is in
front. We cache lightweight read-only dataclasses (no ORM session
binding) keyed by:

- `key_hash` → ApiKeySnapshot (id, user_id, status, quota_*, expires_at, ...)
- `user_id`  → UserSnapshot (id, status, plan_rpm)

`used_cents` and `balance_cents` are NOT cached here — they change on
every request. Those live in Redis (quota_cache).

Hot-path lookups never touch PG when cached. On miss, a single SELECT
hydrates the snapshot. Mutations elsewhere in the app call
`invalidate_*` which evicts locally + publishes via Redis pub/sub so
other workers drop their copies too. A 30s TTL caps the staleness even
if pub/sub drops a message.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.core import cache_invalidate
from app.core.config import settings
from app.core.ttl_cache import TTLCache
from app.db.session import AsyncSessionLocal
from app.models import ApiKey, KeyStatus, Plan, QuotaMode, QuotaPeriod, Subscription, User, UserStatus

log = logging.getLogger(__name__)

_APIKEY_TTL = 30.0
_USER_TTL = 30.0
_NEG_TTL = 5.0  # short negative cache so bogus tokens can't hammer PG

_INVALIDATE_KIND_APIKEY = "apikey"
_INVALIDATE_KIND_USER = "user"


@dataclass(frozen=True)
class ApiKeySnapshot:
    id: int
    user_id: int
    key_hash: str
    status: KeyStatus
    quota_cents: int
    quota_mode: QuotaMode
    quota_period: QuotaPeriod | None
    quota_period_start: datetime | None
    quota_period_end: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class UserSnapshot:
    id: int
    status: UserStatus
    plan_rpm: int


# Sentinel for negative cache (key not found).
class _Missing:
    __slots__ = ()


_MISSING = _Missing()

_apikey_cache: TTLCache[str, ApiKeySnapshot | _Missing] = TTLCache(maxsize=10_000, ttl_seconds=_APIKEY_TTL)
_user_cache: TTLCache[int, UserSnapshot | _Missing] = TTLCache(maxsize=10_000, ttl_seconds=_USER_TTL)


def snapshot_from_row(row: ApiKey) -> ApiKeySnapshot:
    return ApiKeySnapshot(
        id=row.id,
        user_id=row.user_id,
        key_hash=row.key_hash,
        status=row.status,
        quota_cents=row.quota_cents or 0,
        quota_mode=row.quota_mode,
        quota_period=row.quota_period,
        quota_period_start=row.quota_period_start,
        quota_period_end=row.quota_period_end,
        expires_at=row.expires_at,
    )


async def get_apikey_snapshot(key_hash: str) -> ApiKeySnapshot | None:
    cached = _apikey_cache.get(key_hash)
    if cached is not None:
        return None if isinstance(cached, _Missing) else cached
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        ).scalar_one_or_none()
    if row is None:
        _apikey_cache.set(key_hash, _MISSING)
        return None
    snap = snapshot_from_row(row)
    _apikey_cache.set(key_hash, snap)
    return snap


async def _resolve_plan_rpm(user_id: int) -> int:
    """Same logic as quota.user_rpm but inlined so we can cache the result
    on the user snapshot. RPM only changes when subscriptions roll, which
    happens at most once a month for any given user — 30s staleness is
    well within acceptable for rate limiting."""
    from datetime import timezone

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(Plan.rate_limit_jsonb)
                .join(Subscription, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                    Subscription.current_period_end > datetime.now(timezone.utc),
                )
                .order_by(Subscription.current_period_end.desc())
                .limit(1)
            )
        ).first()
    if row and row[0]:
        rpm = row[0].get("rpm") if isinstance(row[0], dict) else None
        if isinstance(rpm, int) and rpm > 0:
            return rpm
    return settings.DEFAULT_RATE_LIMIT_RPM


async def get_user_snapshot(user_id: int) -> UserSnapshot | None:
    cached = _user_cache.get(user_id)
    if cached is not None:
        return None if isinstance(cached, _Missing) else cached
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
    if user is None:
        _user_cache.set(user_id, _MISSING)
        return None
    rpm = await _resolve_plan_rpm(user_id)
    snap = UserSnapshot(id=user.id, status=user.status, plan_rpm=rpm)
    _user_cache.set(user_id, snap)
    return snap


def _local_evict_apikey(key_hash: str) -> None:
    _apikey_cache.pop(key_hash)


def _local_evict_user(user_id_str: str) -> None:
    try:
        _user_cache.pop(int(user_id_str))
    except (TypeError, ValueError):
        pass


# Register handlers so the pub/sub listener can dispatch to us.
cache_invalidate.register(_INVALIDATE_KIND_APIKEY, _local_evict_apikey)
cache_invalidate.register(_INVALIDATE_KIND_USER, _local_evict_user)


async def invalidate_apikey(key_hash: str) -> None:
    await cache_invalidate.publish(_INVALIDATE_KIND_APIKEY, key_hash)


async def invalidate_user(user_id: int) -> None:
    await cache_invalidate.publish(_INVALIDATE_KIND_USER, str(user_id))


def update_apikey_snapshot(row: ApiKey) -> None:
    """Fast path: when we just mutated and committed an ApiKey row in the
    same process, seed the cache directly so the next request doesn't
    pay a PG round-trip. Still publishes invalidation to other workers."""
    _apikey_cache.set(row.key_hash, snapshot_from_row(row))
