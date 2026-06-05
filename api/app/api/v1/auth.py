from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import OAuthAccount, Plan, User, UserRole, UserStatus
from app.schemas import LoginReq, RegisterReq, TokenResp, UserOut
from app.services.billing import grant_subscription

router = APIRouter(prefix="/auth", tags=["auth"])


@dataclass(frozen=True)
class OAuthProviderConfig:
    client_id: str
    client_secret: str
    auth_url: str
    token_url: str
    scope: str


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    provider_user_id: str
    email: str


class OAuthError(Exception):
    pass


async def _grant_signup_free_plan(db: AsyncSession, user: User) -> None:
    free = (await db.execute(select(Plan).where(Plan.code == "free", Plan.active.is_(True)))).scalar_one_or_none()
    if free and (free.quota_cents or 0) > 0:
        await grant_subscription(db, user, free, ref_id="signup-free")


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
    await _grant_signup_free_plan(db, user)

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


@router.get("/oauth/{provider}/login")
async def oauth_login(provider: str):
    provider = provider.lower()
    try:
        cfg = _provider_config(provider)
    except OAuthError as e:
        return RedirectResponse(_website_oauth_url(error=str(e)))

    params = {
        "client_id": cfg.client_id,
        "redirect_uri": _callback_url(provider),
        "response_type": "code",
        "scope": cfg.scope,
        "state": _encode_oauth_state(provider),
    }
    if provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return RedirectResponse(f"{cfg.auth_url}?{urlencode(params)}")


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    provider = provider.lower()
    if error:
        return RedirectResponse(_website_oauth_url(error=error_description or error))

    try:
        if not code or not state:
            raise OAuthError("missing oauth code or state")
        _verify_oauth_state(provider, state)
        cfg = _provider_config(provider)
        async with httpx.AsyncClient(timeout=15.0) as client:
            access_token = await _exchange_code(client, provider, cfg, code)
            profile = await _fetch_profile(client, provider, access_token)
        user = await _login_or_create_oauth_user(db, profile)
        token = create_access_token(user.id, user.role.value)
        return RedirectResponse(_website_oauth_url(access_token=token, role=user.role.value))
    except HTTPException:
        raise
    except OAuthError as e:
        await db.rollback()
        return RedirectResponse(_website_oauth_url(error=str(e)))


def _provider_config(provider: str) -> OAuthProviderConfig:
    if provider == "google":
        cfg = OAuthProviderConfig(
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            scope="openid email profile",
        )
    elif provider == "github":
        cfg = OAuthProviderConfig(
            client_id=settings.GITHUB_OAUTH_CLIENT_ID,
            client_secret=settings.GITHUB_OAUTH_CLIENT_SECRET,
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            scope="read:user user:email",
        )
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unsupported oauth provider")

    if not cfg.client_id or not cfg.client_secret:
        raise OAuthError(f"{provider} oauth is not configured")
    return cfg


def _callback_url(provider: str) -> str:
    base = (settings.OAUTH_CALLBACK_BASE_URL or settings.API_PUBLIC_URL).rstrip("/")
    return f"{base}/api/v1/auth/oauth/{provider}/callback"


def _website_oauth_url(**params: str) -> str:
    base = settings.WEBSITE_PUBLIC_URL.rstrip("/")
    return f"{base}/login/oauth#{urlencode(params)}"


def _encode_oauth_state(provider: str) -> str:
    payload = {
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def _verify_oauth_state(provider: str, state: str) -> None:
    try:
        payload = jwt.decode(state, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except JWTError as e:
        raise OAuthError("invalid oauth state") from e
    if payload.get("provider") != provider:
        raise OAuthError("oauth state provider mismatch")


async def _exchange_code(
    client: httpx.AsyncClient,
    provider: str,
    cfg: OAuthProviderConfig,
    code: str,
) -> str:
    response = await client.post(
        cfg.token_url,
        data={
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _callback_url(provider),
        },
        headers={"Accept": "application/json"},
    )
    data = _json_or_error(response, "oauth token exchange failed")
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthError("oauth provider did not return an access token")
    return access_token


async def _fetch_profile(client: httpx.AsyncClient, provider: str, access_token: str) -> OAuthProfile:
    if provider == "google":
        return await _fetch_google_profile(client, access_token)
    if provider == "github":
        return await _fetch_github_profile(client, access_token)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "unsupported oauth provider")


async def _fetch_google_profile(client: httpx.AsyncClient, access_token: str) -> OAuthProfile:
    response = await client.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    data = _json_or_error(response, "google profile fetch failed")
    provider_user_id = data.get("sub")
    email = data.get("email")
    verified = data.get("email_verified") is True or data.get("email_verified") == "true"
    if not isinstance(provider_user_id, str) or not isinstance(email, str) or not verified:
        raise OAuthError("google account must expose a verified email")
    return OAuthProfile(provider="google", provider_user_id=provider_user_id, email=email.lower())


async def _fetch_github_profile(client: httpx.AsyncClient, access_token: str) -> OAuthProfile:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    user_response = await client.get("https://api.github.com/user", headers=headers)
    user_data = _json_or_error(user_response, "github profile fetch failed")
    provider_user_id = user_data.get("id")
    if provider_user_id is None:
        raise OAuthError("github account did not return a user id")

    emails_response = await client.get("https://api.github.com/user/emails", headers=headers)
    emails_data = _json_or_error(emails_response, "github email fetch failed")
    if not isinstance(emails_data, list):
        raise OAuthError("github account did not return email addresses")
    email = _pick_github_email(emails_data)
    if not email:
        raise OAuthError("github account must expose a verified email")
    return OAuthProfile(provider="github", provider_user_id=str(provider_user_id), email=email.lower())


def _pick_github_email(items: list[object]) -> str | None:
    verified = [item for item in items if isinstance(item, dict) and item.get("verified") and item.get("email")]
    primary = next((item for item in verified if item.get("primary")), None)
    selected = primary or (verified[0] if verified else None)
    email = selected.get("email") if isinstance(selected, dict) else None
    return email if isinstance(email, str) else None


def _json_or_error(response: httpx.Response, message: str) -> dict | list:
    if response.status_code >= 400:
        raise OAuthError(message)
    try:
        return response.json()
    except ValueError as e:
        raise OAuthError(message) from e


async def _login_or_create_oauth_user(db: AsyncSession, profile: OAuthProfile) -> User:
    account = (
        await db.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == profile.provider,
                OAuthAccount.provider_user_id == profile.provider_user_id,
            )
        )
    ).scalar_one_or_none()
    if account:
        user = await db.get(User, account.user_id)
        if not user or user.status != UserStatus.active:
            raise OAuthError("account disabled")
        account.email = profile.email
        await db.commit()
        await db.refresh(user)
        return user

    user = (await db.execute(select(User).where(User.email == profile.email))).scalar_one_or_none()
    if user and user.status != UserStatus.active:
        raise OAuthError("account disabled")

    if not user:
        user = User(
            email=profile.email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=UserRole.user,
            status=UserStatus.active,
        )
        db.add(user)
        await db.flush()
        await _grant_signup_free_plan(db, user)

    db.add(
        OAuthAccount(
            user_id=user.id,
            provider=profile.provider,
            provider_user_id=profile.provider_user_id,
            email=profile.email,
        )
    )
    if not user.oauth_provider:
        user.oauth_provider = profile.provider
        user.oauth_uid = profile.provider_user_id
    await db.commit()
    await db.refresh(user)
    return user
