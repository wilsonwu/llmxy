from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

log = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"


@lru_cache
def _fernet() -> Fernet:
    """Derive a Fernet key from settings.ENCRYPTION_KEY.

    Accepts any string; SHA-256 it down to 32 bytes then url-safe-base64 encode.
    """
    raw = (settings.ENCRYPTION_KEY or "").encode("utf-8")
    if not raw:
        log.warning("ENCRYPTION_KEY is empty — channel api_key will be stored in plaintext")
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: Optional[str]) -> Optional[str]:
    """Encrypt a secret. Returns None for falsy input. Already-encrypted values pass through."""
    if not plain:
        return plain
    if plain.startswith(ENC_PREFIX):
        return plain
    if not settings.ENCRYPTION_KEY:
        return plain  # dev mode w/o key — store plaintext
    token = _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a stored secret. Legacy plaintext (no prefix) is returned as-is for backward compat."""
    if not value:
        return value
    if not value.startswith(ENC_PREFIX):
        return value  # legacy plaintext
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error("decrypt failed — ENCRYPTION_KEY may have changed; secret unreadable")
        return None


def mask(value: Optional[str]) -> str:
    """Return a masked representation safe for admin UI list views."""
    if not value:
        return ""
    plain = decrypt(value) or ""
    if len(plain) <= 8:
        return "*" * len(plain)
    return plain[:4] + "*" * (len(plain) - 8) + plain[-4:]
