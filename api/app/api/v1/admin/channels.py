from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt, mask
from app.core.deps import require_admin
from app.db.session import get_db
from app.models import Channel, Model, User
from app.schemas import ChannelIn, ChannelOut
from app.services.envoy.config import regenerate_all_running
from app.services.providers import SUPPORTED, SUPPORTED_IMAGE_PROTOCOLS

router = APIRouter(prefix="/channels", tags=["admin-channels"])

_EMBEDDING_PROTOCOLS = [p for p in SUPPORTED if p != "anthropic"]


def _validate_protocol(provider_type: str) -> str:
    protocol = (provider_type or "").lower().strip()
    if protocol not in SUPPORTED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unsupported upstream protocol {provider_type!r}; supported: {', '.join(SUPPORTED)}",
        )
    return protocol


def _allowed_protocols_for_kind(kind: str) -> list[str]:
    if kind == "image":
        return SUPPORTED_IMAGE_PROTOCOLS
    if kind == "embedding":
        return _EMBEDDING_PROTOCOLS
    return SUPPORTED


async def _validate_inheriting_models(db: AsyncSession, channel_id: int, protocol: str) -> None:
    rows = (
        await db.execute(
            select(Model).where(
                Model.channel_id == channel_id,
                Model.upstream_protocol.is_(None),
            )
        )
    ).scalars().all()
    invalid = [m.code for m in rows if protocol not in _allowed_protocols_for_kind(m.kind or "chat")]
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"upstream protocol {protocol!r} is not supported by inheriting models: {', '.join(invalid)}; "
            "set model-level overrides first or choose a compatible channel protocol",
        )


def _to_out(row: Channel) -> dict:
    """Mask api_key in list/get responses; full key never leaves the server."""
    return {
        "id": row.id,
        "name": row.name,
        "provider_type": row.provider_type,
        "base_url": row.base_url,
        "api_key_enc": mask(row.api_key_enc),
        "enabled": row.enabled,
    }


@router.get("", response_model=list[ChannelOut])
async def list_channels(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Channel).order_by(Channel.id))).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=ChannelOut)
async def create_channel(req: ChannelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    data = req.model_dump()
    data["provider_type"] = _validate_protocol(data.get("provider_type") or "")
    data["api_key_enc"] = encrypt(data.get("api_key_enc"))
    row = Channel(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return _to_out(row)


@router.put("/{cid}", response_model=ChannelOut)
async def update_channel(cid: int, req: ChannelIn, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Channel, cid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    data = req.model_dump()
    data["provider_type"] = _validate_protocol(data.get("provider_type") or "")
    await _validate_inheriting_models(db, cid, data["provider_type"])
    # treat empty/masked strings as "no change" so the UI's mask roundtrip doesn't overwrite the real key
    incoming = data.get("api_key_enc")
    if not incoming or incoming.startswith("*") or "*" in (incoming or ""):
        data.pop("api_key_enc", None)
    else:
        data["api_key_enc"] = encrypt(incoming)
    for k, v in data.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    await regenerate_all_running(db)
    return _to_out(row)


@router.delete("/{cid}")
async def delete_channel(cid: int, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(Channel, cid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    await regenerate_all_running(db)
    return {"ok": True}
