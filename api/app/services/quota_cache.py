"""Redis-backed quota state for the ext_authz hot path.

The hot path (relay.authz) needs to answer "does this user/key have quota
left?" in <1ms. Going to PG for the answer adds 3 SELECTs (api_key,
user, active_subscriptions) per request — fine at low QPS, fatal at the
volumes envoy enables.

This module is a **write-through cache**: PG remains the source of
truth (charge_user / topup_wallet / grant_subscription still write
PG). After commit, callers mirror the same delta to Redis so the next
hot-path request sees fresh numbers without a PG round-trip.

Redis key schema:
    wallet:{uid}          int cents          balance_cents
    subq:{sid}            int cents          subscription.remaining_cents
    usubs:{uid}           zset(score=end_at) active sub ids per user
    kused:{kid}:w{ws}     int cents          per-key used in current window
                                              (ws=0 sentinel for until_depleted)
    qhydr:{uid}           lock (5s NX)       hydration mutex

If Redis is unavailable, every helper degrades gracefully (logs a
warning and either returns a pessimistic answer or silently no-ops on
write-through). The caller can always fall back to PG via has_quota.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal
from app.models import ApiKey, QuotaMode, Subscription, User

log = logging.getLogger(__name__)

_WALLET_TTL = 3600
_SUBQ_TTL_FALLBACK = 35 * 24 * 3600
_USUBS_TTL = 3600
_KUSED_TTL_SLACK = 3600
_HYDR_LOCK_TTL = 5


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def _k_wallet(uid: int) -> str:
    return f"wallet:{uid}"


def _k_subq(sid: int) -> str:
    return f"subq:{sid}"


def _k_usubs(uid: int) -> str:
    return f"usubs:{uid}"


def _k_kused(kid: int, window_start_epoch: int) -> str:
    return f"kused:{kid}:w{window_start_epoch}"


def _k_hydr_lock(uid: int) -> str:
    return f"qhydr:{uid}"


def window_start_epoch_for(snap) -> int:
    """Map an ApiKeySnapshot's current window to the integer used in
    `kused:{kid}:w{ws}`. `until_depleted` keys use 0 as sentinel so the
    counter lives forever; periodic keys use the current window start
    epoch so rolling forward simply moves to a fresh key."""
    if snap.quota_mode == QuotaMode.periodic and snap.quota_period_start is not None:
        return int(snap.quota_period_start.astimezone(timezone.utc).timestamp())
    return 0


# ---------------------------------------------------------------------------
# Hydration (cache-aside loader)
# ---------------------------------------------------------------------------

async def hydrate_user_quota(user_id: int) -> None:
    """Load wallet + active subs from PG into Redis. Idempotent; safe to
    call concurrently (a 5s NX lock keeps the stampede small).
    """
    r = get_redis()
    try:
        got_lock = await r.set(_k_hydr_lock(user_id), "1", ex=_HYDR_LOCK_TTL, nx=True)
        if not got_lock:
            return
        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
            if user is None:
                return
            now = datetime.now(timezone.utc)
            subs = (
                await db.execute(
                    select(Subscription).where(
                        Subscription.user_id == user_id,
                        Subscription.status == "active",
                        Subscription.current_period_end > now,
                    )
                )
            ).scalars().all()

        pipe = r.pipeline()
        pipe.set(_k_wallet(user_id), int(user.balance_cents or 0), ex=_WALLET_TTL)
        pipe.delete(_k_usubs(user_id))
        if subs:
            mapping = {str(s.id): float(s.current_period_end.timestamp()) for s in subs}
            pipe.zadd(_k_usubs(user_id), mapping)
            pipe.expire(_k_usubs(user_id), _USUBS_TTL)
            for s in subs:
                ttl = max(60, int((s.current_period_end - now).total_seconds()) + _KUSED_TTL_SLACK)
                pipe.set(_k_subq(s.id), int(s.remaining_cents or 0), ex=min(ttl, _SUBQ_TTL_FALLBACK))
        await pipe.execute()
    except Exception as e:
        log.warning("hydrate_user_quota uid=%s failed: %s", user_id, e)


async def hydrate_key_used(key_id: int, window_start_epoch: int) -> None:
    r = get_redis()
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(ApiKey, key_id)
            if row is None:
                return
            used = int(row.used_cents or 0)
        await r.set(_k_kused(key_id, window_start_epoch), used, ex=_SUBQ_TTL_FALLBACK)
    except Exception as e:
        log.warning("hydrate_key_used kid=%s failed: %s", key_id, e)


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

async def has_quota_fast(
    user_id: int,
    key_id: int,
    key_quota_cents: int,
    window_start_epoch: int,
) -> tuple[bool, str]:
    """Pure-Redis quota check. On cache miss, hydrates from PG and
    retries once. Returns (ok, reason)."""
    r = get_redis()
    try:
        ok, reason = await _check(r, user_id, key_id, key_quota_cents, window_start_epoch)
        if ok or reason != "miss":
            return ok, reason
        await hydrate_user_quota(user_id)
        await hydrate_key_used(key_id, window_start_epoch)
        ok, reason = await _check(r, user_id, key_id, key_quota_cents, window_start_epoch)
        if reason == "miss":
            # Still missing after hydrate — fall back to permissive so we
            # don't lock out users on transient Redis hiccups. PG-side
            # charge_user remains authoritative.
            log.warning("has_quota_fast: persistent miss uid=%s — allowing", user_id)
            return True, ""
        return ok, reason
    except Exception as e:
        log.warning("has_quota_fast failed (fail-open): %s", e)
        return True, ""


async def _check(r, user_id: int, key_id: int, key_quota_cents: int, window_start_epoch: int) -> tuple[bool, str]:
    used_raw = await r.get(_k_kused(key_id, window_start_epoch))
    if key_quota_cents > 0:
        if used_raw is None:
            return False, "miss"
        if int(used_raw) >= key_quota_cents:
            return False, "api key quota exceeded"

    # Order: any active sub with remaining > 0, then wallet.
    sub_ids = await r.zrange(_k_usubs(user_id), 0, -1)
    if sub_ids is None:
        return False, "miss"
    for sid in sub_ids:
        v = await r.get(_k_subq(int(sid)))
        if v is None:
            return False, "miss"
        iv = int(v)
        # Negative values are a sign of cache drift (e.g. apply_charge mirror
        # decrementing past zero). Treat as miss so we re-hydrate from PG
        # rather than locking the user out on a phantom negative.
        if iv < 0:
            return False, "miss"
        if iv > 0:
            return True, ""

    wallet_raw = await r.get(_k_wallet(user_id))
    if wallet_raw is None:
        return False, "miss"
    wv = int(wallet_raw)
    if wv < 0:
        return False, "miss"
    if wv > 0:
        return True, ""
    return False, "no active subscription and insufficient balance"


# ---------------------------------------------------------------------------
# Write-through mirroring (called by billing.py callers AFTER PG commit)
# ---------------------------------------------------------------------------

# Lua: decrement KEYS[1] by ARGV[1] only if the key exists. If missing, do
# nothing — letting the next has_quota_fast cache miss re-hydrate the true
# value from PG. Prevents the "resurrect as negative" bug where DECRBY on
# a deleted/expired key materializes a phantom -X value that never expires
# and never re-hydrates.
_DECR_IF_EXISTS_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return redis.call('DECRBY', KEYS[1], ARGV[1])
end
return nil
"""


async def apply_charge(
    user_id: int,
    key_id: int | None,
    cost_cents: int,
    window_start_epoch: int,
    breakdown: list[tuple[str, int]],
) -> None:
    """Mirror a successful PG charge into Redis counters.

    `breakdown` is the per-source deduction list from charge_user:
        [("sub", sub_id, amount), ("wallet", amount)]  # using strings + ints
    We pass it precomputed so Redis stays consistent with what PG did
    (no double-deduction risk if subs ordering differs)."""
    if cost_cents <= 0:
        return
    r = get_redis()
    try:
        pipe = r.pipeline()
        for kind, ident, amount in breakdown:
            if amount <= 0:
                continue
            if kind == "sub":
                pipe.eval(_DECR_IF_EXISTS_LUA, 1, _k_subq(int(ident)), amount)
            elif kind == "wallet":
                pipe.eval(_DECR_IF_EXISTS_LUA, 1, _k_wallet(user_id), amount)
        if key_id is not None:
            # kused is safe to INCRBY-create: starting fresh at +cost matches
            # what hydrate_key_used would set if there were no prior charges.
            pipe.incrby(_k_kused(key_id, window_start_epoch), cost_cents)
        await pipe.execute()
    except Exception as e:
        log.warning("apply_charge mirror failed uid=%s kid=%s: %s — invalidating", user_id, key_id, e)
        await invalidate_user_quota(user_id)


async def apply_topup(user_id: int, amount_cents: int) -> None:
    if amount_cents <= 0:
        return
    try:
        r = get_redis()
        await r.incrby(_k_wallet(user_id), amount_cents)
    except Exception as e:
        log.warning("apply_topup mirror failed uid=%s: %s — invalidating", user_id, e)
        await invalidate_user_quota(user_id)


async def apply_grant(user_id: int, sub_id: int, remaining_cents: int, end_at: datetime) -> None:
    try:
        r = get_redis()
        ttl = max(60, int((end_at - datetime.now(timezone.utc)).total_seconds()) + _KUSED_TTL_SLACK)
        pipe = r.pipeline()
        pipe.zadd(_k_usubs(user_id), {str(sub_id): float(end_at.timestamp())})
        pipe.expire(_k_usubs(user_id), max(_USUBS_TTL, ttl))
        pipe.set(_k_subq(sub_id), int(remaining_cents), ex=min(ttl, _SUBQ_TTL_FALLBACK))
        await pipe.execute()
    except Exception as e:
        log.warning("apply_grant mirror failed uid=%s sid=%s: %s — invalidating", user_id, sub_id, e)
        await invalidate_user_quota(user_id)


async def apply_renew(user_id: int, sub_id: int, remaining_cents: int, end_at: datetime) -> None:
    await apply_grant(user_id, sub_id, remaining_cents, end_at)


async def apply_sub_expire(user_id: int, sub_id: int) -> None:
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.zrem(_k_usubs(user_id), str(sub_id))
        pipe.delete(_k_subq(sub_id))
        await pipe.execute()
    except Exception as e:
        log.warning("apply_sub_expire mirror failed uid=%s sid=%s: %s", user_id, sub_id, e)


async def apply_window_roll(key_id: int, old_window_start_epoch: int) -> None:
    """Window rolled — drop the stale per-key used counter. New window
    starts at 0 lazily on the next charge."""
    try:
        r = get_redis()
        await r.delete(_k_kused(key_id, old_window_start_epoch))
    except Exception as e:
        log.warning("apply_window_roll kid=%s ws=%s: %s", key_id, old_window_start_epoch, e)


async def invalidate_user_quota(user_id: int) -> None:
    """Nuclear option: drop all cached quota state for a user so the
    next request re-hydrates from PG. Used when mirror writes fail or
    when something has gone visibly out of sync."""
    try:
        r = get_redis()
        sub_ids = await r.zrange(_k_usubs(user_id), 0, -1)
        pipe = r.pipeline()
        pipe.delete(_k_wallet(user_id), _k_usubs(user_id))
        for sid in sub_ids or []:
            pipe.delete(_k_subq(int(sid)))
        await pipe.execute()
    except Exception as e:
        log.warning("invalidate_user_quota uid=%s: %s", user_id, e)


async def resync_on_startup() -> None:
    """Drop all cached quota state on api startup so the next request
    re-hydrates from PG. Guards against drift accumulated by older code
    paths or by api processes that crashed mid-mirror.

    Uses SCAN (non-blocking) rather than KEYS so this is safe on large
    deployments. Hydration stays lazy — we don't pre-warm because there
    may be many idle users; the hot users will re-hydrate on first hit.

    Multi-worker safe: a short NX lock guarantees only one worker per
    restart cycle does the SCAN+DEL; the others no-op.
    """
    try:
        r = get_redis()
        got = await r.set("quota_cache:resync_lock", "1", ex=60, nx=True)
        if not got:
            return
        patterns = ("wallet:*", "subq:*", "usubs:*", "kused:*", "qhydr:*")
        total = 0
        for pat in patterns:
            cursor = 0
            while True:
                cursor, keys = await r.scan(cursor=cursor, match=pat, count=500)
                if keys:
                    await r.delete(*keys)
                    total += len(keys)
                if cursor == 0:
                    break
        log.info("quota_cache resync on startup: cleared %d keys", total)
    except Exception as e:
        log.warning("quota_cache resync_on_startup failed: %s", e)
