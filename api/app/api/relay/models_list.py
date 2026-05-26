from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_api_key
from app.db.session import get_db
from app.models import ApiKey, RoutePolicy, RouteScope, User

router = APIRouter(prefix="/v1", tags=["relay"])


@router.get("/models")
async def list_models(
    creds: tuple[ApiKey, User] = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(RoutePolicy).where(
                RoutePolicy.enabled.is_(True),
                RoutePolicy.scope == RouteScope.public,
            )
        )
    ).scalars().all()
    data = [
        {"id": r.user_facing_model, "object": "model", "owned_by": "llmxy"}
        for r in rows
    ]
    return {"object": "list", "data": data}
