"""IP → ISO 3166-1 alpha-2 country code lookup.

Backed by a bundled DB-IP IP-to-Country Lite database (MMDB format, CC BY 4.0
— see app/data/geoip/ATTRIBUTION.md). The path defaults to the bundled file;
operators can override via `GEOIP_DB_PATH` to swap in a fresher DB-IP build
or a licensed MaxMind GeoLite2-Country.mmdb (same format).

Reader is loaded lazily on first lookup and reused — `geoip2.database.Reader`
is documented as thread-safe for reads.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from app.core.config import settings

log = logging.getLogger(__name__)

_BUNDLED_DB = str(Path(__file__).resolve().parent.parent / "data" / "geoip" / "dbip-country-lite.mmdb")

_reader = None
_reader_lock = threading.Lock()
_load_failed = False  # one-shot warning sentinel


def _db_path() -> str:
    override = (settings.GEOIP_DB_PATH or "").strip()
    return override or _BUNDLED_DB


def _get_reader():
    global _reader, _load_failed
    if _reader is not None:
        return _reader
    if _load_failed:
        return None
    with _reader_lock:
        if _reader is not None:
            return _reader
        if _load_failed:
            return None
        path = _db_path()
        if not os.path.isfile(path):
            log.warning(
                "geoip db missing at %r — geo routing rules disabled", path,
            )
            _load_failed = True
            return None
        try:
            import geoip2.database  # type: ignore
            _reader = geoip2.database.Reader(path)
            log.info("geoip db loaded: %s", path)
            return _reader
        except Exception as e:
            log.warning("geoip db load failed (%s): %s — geo routing disabled", path, e)
            _load_failed = True
            return None


def lookup_country(ip: str | None) -> Optional[str]:
    """Return ISO 3166-1 alpha-2 country code for `ip`, or None if the DB
    is unavailable, the IP is invalid, or the country can't be resolved."""
    if not ip:
        return None
    r = _get_reader()
    if r is None:
        return None
    try:
        resp = r.country(ip)
        return resp.country.iso_code
    except Exception:
        return None
