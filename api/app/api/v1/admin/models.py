from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Model, User
from app.schemas import ModelIn, ModelOut
from app.services.envoy.config import regenerate_all_running
from app.services.providers import SUPPORTED, SUPPORTED_IMAGE_PROTOCOLS

router = APIRouter(prefix="/models", tags=["admin-models"])

# Protocols whose adapters actually serve embeddings. Anthropic has no
# embeddings API, so it's excluded for embedding models.
_EMBEDDING_PROTOCOLS = [p for p in SUPPORTED if p != "anthropic"]


def _validate_protocol(req: ModelIn) -> None:
    """Validate the optional per-model upstream protocol override against the
    protocols supported for the model's modality. Empty = fall back to the
    channel's provider_type at relay time (back-compat)."""
    if not req.upstream_protocol:
        return
    proto = req.upstream_protocol.lower()
    if req.kind == "image":
        allowed = SUPPORTED_IMAGE_PROTOCOLS
    elif req.kind == "embedding":
        allowed = _EMBEDDING_PROTOCOLS
    else:
        allowed = SUPPORTED
    if proto not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unsupported upstream_protocol {req.upstream_protocol!r} for kind {req.kind!r}; "
            f"supported: {', '.join(allowed)}",
        )


@router.get("", response_model=list[ModelOut])
async def list_models(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Model).order_by(Model.id))).scalars().all()


@router.post("", response_model=ModelOut)
async def create_model(req: ModelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _validate_protocol(req)
    row = Model(**req.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.put("/{mid}", response_model=ModelOut)
async def update_model(mid: int, req: ModelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _validate_protocol(req)
    row = await db.get(Model, mid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    for k, v in req.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return row


@router.delete("/{mid}")
async def delete_model(mid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Model, mid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    await regenerate_all_running(db)
    return {"ok": True}
