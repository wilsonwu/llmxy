from __future__ import annotations

import asyncio
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


_log = logging.getLogger(__name__)


@event.listens_for(Session, "after_commit")
def _fire_quota_invalidate(sess: Session) -> None:
    """When billing code marks `_quota_invalidate_uids` on the session's
    `info` dict, drop the affected users' Redis quota cache after the
    transaction commits. Re-hydration happens lazily on the next
    has_quota_fast call. Used for write paths (topup/grant/renew) where
    fine-grained mirroring isn't worth the code; the ALS hot path uses
    `quota_cache.apply_charge` directly instead."""
    uids = sess.info.pop("_quota_invalidate_uids", None)
    if not uids:
        return
    try:
        from app.services import quota_cache
        loop = asyncio.get_running_loop()
        for uid in uids:
            loop.create_task(quota_cache.invalidate_user_quota(uid))
    except RuntimeError:
        # No running loop (commit happened in sync context) — skip.
        pass
    except Exception as e:
        _log.warning("post-commit quota invalidate scheduling failed: %s", e)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
