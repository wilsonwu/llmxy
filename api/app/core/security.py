from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

API_KEY_PREFIX = "sk-"


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str | int, role: str, expires_minutes: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.JWT_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {"sub": str(subject), "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except JWTError as e:
        raise ValueError(f"invalid token: {e}") from e


def generate_api_key() -> tuple[str, str, str]:
    """Returns (plain_key, key_prefix, key_hash). Plain key is shown only once."""
    raw = secrets.token_urlsafe(32)
    plain = f"{API_KEY_PREFIX}{raw}"
    prefix = plain[:12]
    key_hash = sha256(plain.encode()).hexdigest()
    return plain, prefix, key_hash


def hash_api_key(plain: str) -> str:
    return sha256(plain.encode()).hexdigest()
