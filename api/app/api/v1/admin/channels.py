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
from app.services.providers import (
        channel_connector,
        channel_protocol,
    SUPPORTED_CHAT_PROTOCOLS,
    SUPPORTED_CONNECTORS,
    SUPPORTED_EMBEDDING_PROTOCOLS,
    SUPPORTED_IMAGE_PROTOCOLS,
    connector_supports_kind,
    connector_supports_protocol,
    normalize_connector,
    normalize_protocol,
)

router = APIRouter(prefix="/channels", tags=["admin-channels"])

def _validate_protocol(provider_type: str) -> str:
    protocol = normalize_protocol(provider_type)
    if protocol not in SUPPORTED_CHAT_PROTOCOLS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unsupported upstream protocol {provider_type!r}; supported: {', '.join(SUPPORTED_CHAT_PROTOCOLS)}",
        )
    return protocol


def _validate_connector(connector_type: str, protocol: str) -> str:
    connector = normalize_connector(connector_type)
    if connector not in SUPPORTED_CONNECTORS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unsupported upstream connector {connector_type!r}; supported: {', '.join(SUPPORTED_CONNECTORS)}",
        )
    if not connector_supports_protocol(connector, protocol):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"upstream connector {connector!r} does not support semantic protocol {protocol!r}",
        )
    return connector


def _allowed_protocols_for_kind(kind: str) -> list[str]:
    if kind == "image":
        return SUPPORTED_IMAGE_PROTOCOLS
    if kind == "embedding":
        return SUPPORTED_EMBEDDING_PROTOCOLS
    return SUPPORTED_CHAT_PROTOCOLS


async def _validate_channel_models(db: AsyncSession, channel_id: int, protocol: str, connector: str) -> None:
    rows = (
        await db.execute(
            select(Model).where(Model.channel_id == channel_id)
        )
    ).scalars().all()
    invalid: list[str] = []
    for m in rows:
        model_protocol = normalize_protocol(m.upstream_protocol or protocol)
        kind = m.kind or "chat"
        if model_protocol not in _allowed_protocols_for_kind(kind):
            invalid.append(f"{m.code} ({kind} / {model_protocol})")
        elif not connector_supports_protocol(connector, model_protocol) or not connector_supports_kind(connector, kind):
            invalid.append(f"{m.code} ({kind} / {model_protocol} via {connector})")
    if invalid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"upstream protocol/connector is not supported by bound models: {', '.join(invalid)}; "
            "adjust model overrides first or choose a compatible connector",
        )


def _to_out(row: Channel) -> dict:
    """Mask api_key in list/get responses; full key never leaves the server."""
    return {
        "id": row.id,
        "name": row.name,
        "provider_type": channel_protocol(row),
        "connector_type": channel_connector(row),
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
    data["connector_type"] = _validate_connector(data.get("connector_type") or "", data["provider_type"])
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
    data["connector_type"] = _validate_connector(data.get("connector_type") or "", data["provider_type"])
    await _validate_channel_models(db, cid, data["provider_type"], data["connector_type"])
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
