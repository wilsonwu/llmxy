from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import RoutePolicy, RouteScope, User

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
async def list_models(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Public model catalog for logged-in users — mirrors /v1/models but auth via JWT."""
    rows = (
        await db.execute(
            select(RoutePolicy)
            .where(
                RoutePolicy.enabled.is_(True),
                RoutePolicy.scope == RouteScope.public,
            )
            .order_by(RoutePolicy.user_facing_model)
        )
    ).scalars().all()
    return [
        {
            "id": r.user_facing_model,
            "strategy": r.strategy.value if hasattr(r.strategy, "value") else r.strategy,
            "target_count": len(r.targets_jsonb or []),
        }
        for r in rows
    ]
