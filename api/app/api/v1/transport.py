from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import EnvoyInstance, EnvoyStatus

router = APIRouter(prefix="/relay", tags=["relay"])


@router.get("/transport")
async def transport(db: AsyncSession = Depends(get_db)):
    """Report which relay transport(s) are currently available.

    `direct` (api FastAPI on :8000) is always available. `envoy` is available
    when one or more EnvoyInstance rows are in the `running` state. Clients
    that want the high-performance path should target an envoy listen port.
    """
    rows = (
        await db.execute(
            select(EnvoyInstance)
            .where(EnvoyInstance.status == EnvoyStatus.running)
            .order_by(EnvoyInstance.listen_port)
        )
    ).scalars().all()
    instances = [
        {"name": r.name, "listen_port": r.listen_port}
        for r in rows
    ]
    return {
        "direct": {"available": True, "note": "api-direct relay on the api port (always available)"},
        "envoy": {"available": bool(instances), "instances": instances},
        "active": "envoy" if instances else "direct",
    }
