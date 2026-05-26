from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import generate_api_key
from app.db.session import get_db
from app.models import ApiKey, KeyStatus, QuotaMode, QuotaPeriod, User
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, ApiKeyUpdate
from app.services.api_key import init_periodic_window

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _parse_mode(raw: str | None) -> QuotaMode:
    if raw is None:
        return QuotaMode.until_depleted
    try:
        return QuotaMode(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid quota_mode: {raw}") from e


def _parse_period(raw: str | None) -> QuotaPeriod | None:
    if raw is None:
        return None
    try:
        return QuotaPeriod(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid quota_period: {raw}") from e


def _validate_mode_period(mode: QuotaMode, period: QuotaPeriod | None) -> None:
    if mode == QuotaMode.periodic and period is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quota_period is required when quota_mode=periodic")
    if mode == QuotaMode.until_depleted and period is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quota_period must be null when quota_mode=until_depleted")


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
    mode = _parse_mode(req.quota_mode)
    period = _parse_period(req.quota_period)
    _validate_mode_period(mode, period)

    plain, prefix, key_hash = generate_api_key()
    row = ApiKey(
        user_id=user.id,
        name=req.name,
        key_prefix=prefix,
        key_hash=key_hash,
        quota_cents=req.quota_cents,
        expires_at=req.expires_at,
        quota_mode=mode,
        quota_period=period,
    )
    if mode == QuotaMode.periodic and period is not None:
        start, end = init_periodic_window(datetime.now(timezone.utc), period)
        row.quota_period_start = start
        row.quota_period_end = end
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ApiKeyCreated(**ApiKeyOut.model_validate(row).model_dump(), key=plain)


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def update_key(
    key_id: int,
    req: ApiKeyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ApiKey, key_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    if req.name is not None:
        row.name = req.name
    if req.quota_cents is not None:
        row.quota_cents = req.quota_cents
    if req.clear_expires_at:
        row.expires_at = None
    elif req.expires_at is not None:
        row.expires_at = req.expires_at

    # Mode/period changes reset the window + used_cents because the old
    # window's semantics (e.g. "this $50 was the daily budget") no longer apply.
    mode_change = req.quota_mode is not None and _parse_mode(req.quota_mode) != row.quota_mode
    period_change = req.quota_period is not None and _parse_period(req.quota_period) != row.quota_period
    if req.quota_mode is not None:
        row.quota_mode = _parse_mode(req.quota_mode)
    if req.quota_period is not None:
        row.quota_period = _parse_period(req.quota_period)
    _validate_mode_period(row.quota_mode, row.quota_period)

    if mode_change or period_change:
        row.used_cents = 0
        if row.quota_mode == QuotaMode.periodic and row.quota_period is not None:
            start, end = init_periodic_window(datetime.now(timezone.utc), row.quota_period)
            row.quota_period_start = start
            row.quota_period_end = end
        else:
            row.quota_period_start = None
            row.quota_period_end = None

    await db.commit()
    await db.refresh(row)
    return row


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


@router.post("/{key_id}/enable", response_model=ApiKeyOut)
async def enable_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-enable a disabled/expired key. For expired keys the caller must PATCH
    a future expires_at first — otherwise the key would just re-expire on the
    next request and the UI would loop."""
    row = await db.get(ApiKey, key_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    now = datetime.now(timezone.utc)
    if row.expires_at is not None and row.expires_at.astimezone(timezone.utc) <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "extend_expires_at_first",
                "message": "expires_at is in the past; update it to a future time before re-enabling",
            },
        )
    row.status = KeyStatus.active
    await db.commit()
    await db.refresh(row)
    return row
