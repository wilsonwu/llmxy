from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def client_ip(request: "Request") -> str | None:
    """Best-effort client IP. Prefers the first hop in X-Forwarded-For
    (set by Envoy via use_remote_address, or by an upstream LB), falls
    back to the socket peer. Returns None if neither is available."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    if request.client and request.client.host:
        return request.client.host
    return None
