"""API key lifecycle helpers: quota window math + lazy state enforcement.

These run on the request hot path (deps.get_api_key + relay.authz) so any
expensive computation must stay out — DB writes only fire when state actually
changes (expiry tripped or window rolled). A background sweeper in
`api_key_expiry.py` handles the same expiry flip for keys that aren't
actively used, so the UI's status reflects reality without a request needing
to trigger it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import ApiKey, KeyStatus, QuotaMode, QuotaPeriod
from app.services.billing import next_period_end as _next_calendar_month


def next_quota_window(start: datetime, period: QuotaPeriod | str) -> datetime:
    """Given a window start, return the window end.

    - day:   start + 24h
    - week:  start + 7d
    - month: first day of next calendar month at 00:00 UTC (matches subscription
      semantics so a "$50/month" key lines up with the user's monthly statement)
    """
    p = QuotaPeriod(period) if isinstance(period, str) else period
    start = start.astimezone(timezone.utc)
    if p == QuotaPeriod.day:
        return start + timedelta(days=1)
    if p == QuotaPeriod.week:
        return start + timedelta(days=7)
    return _next_calendar_month(start)


def init_periodic_window(now: datetime, period: QuotaPeriod | str) -> tuple[datetime, datetime]:
    """Build the (start, end) tuple for a fresh periodic key."""
    start = now.astimezone(timezone.utc)
    return start, next_quota_window(start, period)


async def enforce_key_state(db: AsyncSession, api_key: ApiKey) -> None:
    """Lazy state machine applied before any request the key authorises.

    - If `expires_at` has passed: flip status=expired, commit, raise 401.
    - If mode=periodic and the window has rolled past: zero `used_cents` and
      advance the window (commit), then return so the request continues.

    Caller is expected to subsequently check `status == active` — we don't
    raise for an already-disabled key here (that's the caller's job, since
    the rejection message differs between "disabled by user" and "expired").
    """
    now = datetime.now(timezone.utc)

    if (
        api_key.status == KeyStatus.active
        and api_key.expires_at is not None
        and api_key.expires_at.astimezone(timezone.utc) <= now
    ):
        api_key.status = KeyStatus.expired
        await db.commit()
        await db.refresh(api_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "api key expired")

    if (
        api_key.status == KeyStatus.active
        and api_key.quota_mode == QuotaMode.periodic
        and api_key.quota_period is not None
        and api_key.quota_period_end is not None
        and api_key.quota_period_end.astimezone(timezone.utc) <= now
    ):
        # Roll forward. If many windows have elapsed (e.g. key idle for weeks)
        # we still only advance by one window at a time — `used_cents` only
        # accrued during the last active window, so zeroing it once is correct.
        new_start = api_key.quota_period_end
        new_end = next_quota_window(new_start, api_key.quota_period)
        # If now is still past the new end (key idle for >1 window), fast-forward.
        while new_end <= now:
            new_start = new_end
            new_end = next_quota_window(new_start, api_key.quota_period)
        api_key.quota_period_start = new_start
        api_key.quota_period_end = new_end
        api_key.used_cents = 0
        await db.commit()
        await db.refresh(api_key)


async def enforce_key_state_cached(snap):
    """Snapshot-based variant used on the ext_authz hot path.

    Pure-memory fast path: if not expired and the periodic window hasn't
    rolled, return the snapshot unchanged (zero DB work). Otherwise open
    a short-lived session, mutate the row, invalidate caches, and either
    raise 401 (expired) or return a fresh snapshot (window rolled).
    """
    from fastapi import HTTPException, status
    from app.services import api_key_cache, quota_cache

    now = datetime.now(timezone.utc)

    needs_expire = (
        snap.status == KeyStatus.active
        and snap.expires_at is not None
        and snap.expires_at.astimezone(timezone.utc) <= now
    )
    needs_roll = (
        snap.status == KeyStatus.active
        and snap.quota_mode == QuotaMode.periodic
        and snap.quota_period is not None
        and snap.quota_period_end is not None
        and snap.quota_period_end.astimezone(timezone.utc) <= now
    )
    if not needs_expire and not needs_roll:
        return snap

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ApiKey).where(ApiKey.id == snap.id))
        ).scalar_one_or_none()
        if row is None:
            # Row was deleted out from under us — evict and reject.
            await api_key_cache.invalidate_apikey(snap.key_hash)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "api key not found")

        if needs_expire and row.status == KeyStatus.active and row.expires_at is not None and row.expires_at.astimezone(timezone.utc) <= now:
            row.status = KeyStatus.expired
            await db.commit()
            api_key_cache.update_apikey_snapshot(row)
            await api_key_cache.invalidate_apikey(row.key_hash)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "api key expired")

        old_window_start_epoch: int | None = None
        if needs_roll and row.quota_mode == QuotaMode.periodic and row.quota_period is not None and row.quota_period_end is not None and row.quota_period_end.astimezone(timezone.utc) <= now:
            old_window_start_epoch = (
                int(row.quota_period_start.timestamp()) if row.quota_period_start else 0
            )
            new_start = row.quota_period_end
            new_end = next_quota_window(new_start, row.quota_period)
            while new_end <= now:
                new_start = new_end
                new_end = next_quota_window(new_start, row.quota_period)
            row.quota_period_start = new_start
            row.quota_period_end = new_end
            row.used_cents = 0
            await db.commit()
            await db.refresh(row)

        api_key_cache.update_apikey_snapshot(row)
        await api_key_cache.invalidate_apikey(row.key_hash)
        fresh = api_key_cache.snapshot_from_row(row)

    if old_window_start_epoch is not None:
        try:
            await quota_cache.apply_window_roll(snap.id, old_window_start_epoch)
        except Exception:
            pass
    return fresh
