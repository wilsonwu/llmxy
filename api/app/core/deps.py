from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, hash_api_key
from app.db.session import get_db
from app.models import ApiKey, KeyStatus, User, UserRole, UserStatus


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    user = await db.get(User, int(payload["sub"]))
    if not user or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or disabled")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return user


async def get_api_key(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> tuple[ApiKey, User]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing api key")
    plain = authorization.split(" ", 1)[1].strip()
    if not plain.startswith("sk-"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key format")
    h = hash_api_key(plain)
    row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == h))).scalar_one_or_none()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
    # Lazy state machine: expire stale keys + roll periodic windows before
    # the active-status check below sees them.
    from app.services.api_key import enforce_key_state
    await enforce_key_state(db, row)
    if row.status != KeyStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"api key {row.status.value}")
    user = await db.get(User, row.user_id)
    if not user or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")
    return row, user
