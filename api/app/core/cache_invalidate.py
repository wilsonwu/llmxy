"""Cross-worker cache invalidation via Redis pub/sub.

Each worker subscribes to one channel and registers eviction callbacks
keyed by `kind`. When a worker mutates a row (PATCH/DELETE/etc.) it
calls `publish(kind, key)`; every subscribed worker (including the
publisher itself) drops the matching entry from its in-process cache.

Single-worker today, multi-worker tomorrow — this stays correct in
both. If Redis is unavailable, publish becomes a no-op and TTL on the
local caches still guarantees eventual consistency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.core.redis import get_redis

log = logging.getLogger(__name__)

CHANNEL = "llmxy:cache_invalidate"

# kind -> handler(key_str). Handlers must be sync + cheap (dict pop).
_handlers: dict[str, Callable[[str], None]] = {}
_task: asyncio.Task | None = None


def register(kind: str, handler: Callable[[str], None]) -> None:
    _handlers[kind] = handler


async def publish(kind: str, key: str) -> None:
    """Best-effort fan-out. Apply locally synchronously first so the
    publisher sees the change without a pub/sub round trip."""
    handler = _handlers.get(kind)
    if handler is not None:
        try:
            handler(key)
        except Exception as e:
            log.warning("local invalidate handler failed kind=%s key=%s: %s", kind, key, e)
    try:
        r = get_redis()
        await r.publish(CHANNEL, f"{kind}:{key}")
    except Exception as e:
        log.debug("cache_invalidate publish failed (TTL will catch up): %s", e)


async def _loop() -> None:
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(CHANNEL)
    log.info("cache_invalidate listener subscribed to %s", CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data") or ""
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            kind, sep, key = data.partition(":")
            if not sep:
                continue
            handler = _handlers.get(kind)
            if handler is None:
                continue
            try:
                handler(key)
            except Exception as e:
                log.warning("remote invalidate handler failed kind=%s key=%s: %s", kind, key, e)
    finally:
        try:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.close()
        except Exception:
            pass


async def start() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None
