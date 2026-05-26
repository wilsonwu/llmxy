"""Background sweeper that flips active keys to expired when their expires_at
has passed. Lazy enforcement in `enforce_key_state` covers keys that are
actively used; this worker is the safety net for idle keys so the UI doesn't
show "active" for something that would reject the next request anyway.

Window rolling is NOT done here — it happens lazily on the request path. A
key whose period has rolled but is idle is harmless; the next request rolls
it before any quota check.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import update

from app.db.session import AsyncSessionLocal
from app.models import ApiKey, KeyStatus

log = logging.getLogger(__name__)

_TASK: asyncio.Task | None = None
_TICK_SECONDS = 5 * 60


async def _scan_once() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(ApiKey)
            .where(
                ApiKey.status == KeyStatus.active,
                ApiKey.expires_at.is_not(None),
                ApiKey.expires_at <= now,
            )
            .values(status=KeyStatus.expired)
        )
        await db.commit()
        if result.rowcount:
            log.info("api_key expiry sweep: flipped %s keys to expired", result.rowcount)


async def _loop() -> None:
    while True:
        try:
            await _scan_once()
        except Exception as e:
            log.exception("api_key expiry scan failed: %s", e)
        await asyncio.sleep(_TICK_SECONDS)


async def start() -> None:
    global _TASK
    if _TASK and not _TASK.done():
        return
    _TASK = asyncio.create_task(_loop())
    log.info("api_key expiry worker started (every %ss)", _TICK_SECONDS)


async def stop() -> None:
    global _TASK
    if _TASK and not _TASK.done():
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):
            pass
    _TASK = None
