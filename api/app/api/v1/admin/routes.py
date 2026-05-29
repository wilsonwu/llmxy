from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Model, RoutePolicy, RouteScope, RouteStrategy, User
from app.schemas import RoutePolicyIn, RoutePolicyOut
from app.services.envoy.config import regenerate_all_running

router = APIRouter(prefix="/routes", tags=["admin-routes"])

VALID_MODALITIES = {"chat", "embedding", "image"}


@router.get("", response_model=list[RoutePolicyOut])
async def list_routes(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(RoutePolicy).order_by(RoutePolicy.id))).scalars().all()


async def _validate_modality(db: AsyncSession, req: RoutePolicyIn) -> None:
    """Each route is bound to a single modality; all targets must be models of
    that kind so the route can only be resolved by the matching endpoint."""
    if req.modality not in VALID_MODALITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid modality {req.modality!r}")
    target_ids = [t.model_id for t in req.targets_jsonb]
    if not target_ids:
        return
    models = (await db.execute(select(Model).where(Model.id.in_(target_ids)))).scalars().all()
    by_id = {m.id: m for m in models}
    for t in req.targets_jsonb:
        m = by_id.get(t.model_id)
        if m is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"target model {t.model_id} not found")
        if (m.kind or "chat") != req.modality:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"target model {m.code!r} is kind={m.kind!r}, expected {req.modality!r}",
            )


def _to_orm(req: RoutePolicyIn) -> dict:
    return {
        "user_facing_model": req.user_facing_model,
        "modality": req.modality,
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
    await _validate_modality(db, req)
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
    await _validate_modality(db, req)
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
