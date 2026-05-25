from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import generate_api_key
from app.db.session import get_db
from app.models import ApiKey, KeyStatus, User
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.id.desc()))
    ).scalars().all()
    return rows


@router.post("", response_model=ApiKeyCreated)
async def create_key(
    req: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plain, prefix, key_hash = generate_api_key()
    row = ApiKey(
        user_id=user.id,
        name=req.name,
        key_prefix=prefix,
        key_hash=key_hash,
        quota_cents=req.quota_cents,
        expires_at=req.expires_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ApiKeyCreated(**ApiKeyOut.model_validate(row).model_dump(), key=plain)


@router.delete("/{key_id}")
async def delete_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ApiKey, key_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.post("/{key_id}/disable", response_model=ApiKeyOut)
async def disable_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ApiKey, key_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    row.status = KeyStatus.disabled
    await db.commit()
    await db.refresh(row)
    return row
