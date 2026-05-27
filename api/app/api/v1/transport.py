from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import EnvoyInstance, EnvoyMode, EnvoyStatus

router = APIRouter(prefix="/relay", tags=["relay"])


def _proxy_url(inst: EnvoyInstance) -> str:
    """Where clients should point /v1/* traffic for this envoy. Mirrors
    EnvoyInstanceOut.proxy_url so user-facing callers don't need admin access."""
    if inst.mode == EnvoyMode.local:
        return f"http://127.0.0.1:{inst.listen_port}"
    if inst.admin_url:
        u = urlparse(inst.admin_url)
        host = u.hostname
        scheme = u.scheme or "http"
        if host:
            return f"{scheme}://{host}:{inst.listen_port}"
    return f"http://<envoy-host>:{inst.listen_port}"


@router.get("/transport")
async def transport(db: AsyncSession = Depends(get_db)):
    """Report which relay transport(s) are currently available.

    `direct` (api FastAPI on :8000) is always available. `envoy` is available
    when one or more EnvoyInstance rows are in the `running` state. Clients
    that want the high-performance path should target an envoy listen port.
    The `proxy_url` field is the full base (scheme://host:port) — append
    `/v1/...` to call OpenAI-compatible endpoints.
    """
    rows = (
        await db.execute(
            select(EnvoyInstance)
            .where(EnvoyInstance.status == EnvoyStatus.running)
            .order_by(EnvoyInstance.listen_port)
        )
    ).scalars().all()
    instances = [
        {
            "name": r.name,
            "mode": r.mode.value if hasattr(r.mode, "value") else str(r.mode),
            "listen_port": r.listen_port,
            "proxy_url": _proxy_url(r),
        }
        for r in rows
    ]
    return {
        "direct": {"available": True, "note": "api-direct relay on the api port (always available)"},
        "envoy": {"available": bool(instances), "instances": instances},
        "active": "envoy" if instances else "direct",
    }
