from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import Subscription
from app.services.billing import renew_subscription

log = logging.getLogger(__name__)

_TASK: asyncio.Task | None = None
_TICK_SECONDS = 5 * 60  # 5 minutes


async def _scan_once() -> None:
    """One pass: find subs whose period has ended and try to renew (or close)."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Subscription)
                .where(
                    Subscription.current_period_end <= now,
                    Subscription.status.in_(("active", "past_due")),
                )
                .order_by(Subscription.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for sub in rows:
            try:
                ok, reason = await renew_subscription(db, sub)
                log.info(
                    "sub#%s renewal: ok=%s status=%s reason=%s",
                    sub.id, ok, sub.status, reason,
                )
            except Exception as e:
                log.exception("sub#%s renewal crashed: %s", sub.id, e)
        await db.commit()


async def _loop() -> None:
    while True:
        try:
            await _scan_once()
        except Exception as e:
            log.exception("renewal scan failed: %s", e)
        await asyncio.sleep(_TICK_SECONDS)


async def start() -> None:
    global _TASK
    if _TASK and not _TASK.done():
        return
    _TASK = asyncio.create_task(_loop())
    log.info("subscription renewal worker started (every %ss)", _TICK_SECONDS)


async def stop() -> None:
    global _TASK
    if _TASK and not _TASK.done():
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):
            pass
    _TASK = None
