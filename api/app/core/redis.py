from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        # redis-py 8.0 changed default socket_timeout from None to 5s, which
        # breaks long-lived PubSub.listen() loops (xds notify channel is idle
        # most of the time). Force unbounded reads + health check pings to
        # keep dead-connection detection.
        _redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=None,
            socket_keepalive=True,
            health_check_interval=30,
        )
    return _redis
