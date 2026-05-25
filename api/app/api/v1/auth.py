from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import Plan, User, UserRole, UserStatus
from app.schemas import LoginReq, RegisterReq, TokenResp, UserOut
from app.services.billing import grant_subscription

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResp)
async def register(req: RegisterReq, db: AsyncSession = Depends(get_db)) -> TokenResp:
    exists = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email already registered")
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        role=UserRole.user,
        status=UserStatus.active,
    )
    db.add(user)
    await db.flush()

    free = (await db.execute(select(Plan).where(Plan.code == "free", Plan.active.is_(True)))).scalar_one_or_none()
    if free and (free.quota_cents or 0) > 0:
        await grant_subscription(db, user, free, ref_id="signup-free")

    await db.commit()
    await db.refresh(user)
    token = create_access_token(user.id, user.role.value)
    return TokenResp(access_token=token, role=user.role.value)


@router.post("/login", response_model=TokenResp)
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)) -> TokenResp:
    user = (await db.execute(select(User).where(User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if user.status != UserStatus.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    token = create_access_token(user.id, user.role.value)
    return TokenResp(access_token=token, role=user.role.value)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


# ---- OAuth stubs ----
@router.get("/oauth/{provider}/login")
async def oauth_login(provider: str):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, f"oauth {provider} not configured")


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, f"oauth {provider} not configured")
