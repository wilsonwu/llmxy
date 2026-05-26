from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import RoutePolicy, RouteScope, RouteStrategy, User
from app.schemas import RoutePolicyIn, RoutePolicyOut
from app.services.envoy.config import regenerate_all_running

router = APIRouter(prefix="/routes", tags=["admin-routes"])


@router.get("", response_model=list[RoutePolicyOut])
async def list_routes(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(RoutePolicy).order_by(RoutePolicy.id))).scalars().all()


def _to_orm(req: RoutePolicyIn) -> dict:
    return {
        "user_facing_model": req.user_facing_model,
        "strategy": RouteStrategy(req.strategy),
        "targets_jsonb": [t.model_dump() for t in req.targets_jsonb],
        "smart_rules_jsonb": [r.model_dump(exclude_none=True) for r in req.smart_rules_jsonb],
        "smart_default_label": req.smart_default_label,
        "smart_embedding_model_id": req.smart_embedding_model_id,
        "smart_exemplars_jsonb": [e.model_dump() for e in req.smart_exemplars_jsonb],
        "smart_score_threshold": req.smart_score_threshold,
        "scope": RouteScope(req.scope),
        "enabled": req.enabled,
    }


def _exemplar_fingerprint(policy_like) -> tuple:
    """Detect changes that should invalidate the cached exemplar embeddings."""
    if isinstance(policy_like, RoutePolicyIn):
        items = [(e.label, e.text) for e in policy_like.smart_exemplars_jsonb]
        emb_id = policy_like.smart_embedding_model_id
    else:
        items = [(str(it.get("label", "")), str(it.get("text", "")))
                 for it in (policy_like.smart_exemplars_jsonb or []) if isinstance(it, dict)]
        emb_id = policy_like.smart_embedding_model_id
    return (emb_id, tuple(sorted(items)))


@router.post("", response_model=RoutePolicyOut)
async def create_route(req: RoutePolicyIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = RoutePolicy(**_to_orm(req))
    # Start version at 1 so first cache write has a non-default key.
    row.smart_embedding_version = 1 if req.smart_embedding_model_id and req.smart_exemplars_jsonb else 0
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.put("/{rid}", response_model=RoutePolicyOut)
async def update_route(rid: int, req: RoutePolicyIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(RoutePolicy, rid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    before = _exemplar_fingerprint(row)
    for k, v in _to_orm(req).items():
        setattr(row, k, v)
    after = _exemplar_fingerprint(req)
    if before != after:
        row.smart_embedding_version = (row.smart_embedding_version or 0) + 1
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.delete("/{rid}")
async def delete_route(rid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(RoutePolicy, rid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    await regenerate_all_running(db)
    return {"ok": True}
