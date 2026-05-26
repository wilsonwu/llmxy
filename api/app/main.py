from __future__ import annotations

import logging
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.internal import relay as internal_relay
from app.api.internal import translate as internal_translate
from app.api.relay import chat as relay_chat
from app.api.relay import embeddings as relay_embeddings
from app.api.relay import models_list as relay_models
from app.api.v1 import api_keys, auth, orders, plans, subscriptions, transport, usage
from app.api.v1.admin import channels as admin_channels
from app.api.v1.admin import envoy as admin_envoy
from app.api.v1.admin import models as admin_models
from app.api.v1.admin import plans as admin_plans
from app.api.v1.admin import routes as admin_routes
from app.api.v1.admin import stats as admin_stats
from app.api.v1.admin import users as admin_users
from app.core.config import settings
from app.core.request_ctx import request_id_var


# ---------------------------------------------------------------------------
# Logging: colorized level + access log with method/status colors. ANSI codes
# only emitted when stderr is a TTY (so file/journalctl output stays clean).
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stderr.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


_LEVEL_COLOR = {
    "DEBUG": "36",   # cyan
    "INFO": "32",    # green
    "WARNING": "33", # yellow
    "ERROR": "31",   # red
    "CRITICAL": "1;31",
}
_METHOD_COLOR = {
    "GET": "36", "HEAD": "36",
    "POST": "32", "PUT": "33", "PATCH": "33",
    "DELETE": "31", "OPTIONS": "35",
}


def _status_color(status: int) -> str:
    if status < 300:
        return "32"
    if status < 400:
        return "36"
    if status < 500:
        return "33"
    return "31"


class _ColorFormatter(logging.Formatter):
    default_time_format = "%H:%M:%S"
    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_var.get() or "-"
        ts = self.formatTime(record, self.default_time_format)
        rid = _c("90", f"[{record.request_id}]")
        level = _c(_LEVEL_COLOR.get(record.levelname, "0"), f"{record.levelname:<5}")

        if record.name == "access":
            method = getattr(record, "method", "-")
            path = getattr(record, "path", "-")
            status = int(getattr(record, "status", 0))
            dur_ms = float(getattr(record, "dur_ms", 0.0))
            client = getattr(record, "client", "-")
            m = _c(_METHOD_COLOR.get(method, "0"), f"{method:<6}")
            st = _c(_status_color(status) + ";1", str(status))
            dur = _c("90", f"{dur_ms:7.1f}ms")
            return f"{ts} {level} {rid} {client:<15} {m} {path} {st} {dur}"

        name = _c("90", f"{record.name}")
        return f"{ts} {level} {rid} {name}: {record.getMessage()}"


_handler = logging.StreamHandler()
_handler.setFormatter(_ColorFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = logging.getLogger(_name)
    _lg.handlers = [_handler]
    _lg.setLevel(logging.INFO)
    _lg.propagate = False
# Silence uvicorn's built-in access log — our middleware emits a richer one.
logging.getLogger("uvicorn.access").disabled = True


app = FastAPI(title="llmxy api", version="0.1.0")


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        dur_ms = (time.perf_counter() - start) * 1000
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        try:
            logging.getLogger("access").info(
                "",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": status,
                    "dur_ms": dur_ms,
                    "client": request.client.host if request.client else "-",
                },
            )
        except Exception:
            pass
        request_id_var.reset(token)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# v1 (user-facing platform APIs)
V1 = "/api/v1"
app.include_router(auth.router, prefix=V1)
app.include_router(api_keys.router, prefix=V1)
app.include_router(plans.router, prefix=V1)
app.include_router(orders.router, prefix=V1)
app.include_router(orders.payments_router, prefix=V1)
app.include_router(usage.router, prefix=V1)
app.include_router(transport.router, prefix=V1)
app.include_router(subscriptions.router, prefix=V1)

# admin
ADMIN = "/api/v1/admin"
app.include_router(admin_users.router, prefix=ADMIN)
app.include_router(admin_channels.router, prefix=ADMIN)
app.include_router(admin_models.router, prefix=ADMIN)
app.include_router(admin_plans.router, prefix=ADMIN)
app.include_router(admin_routes.router, prefix=ADMIN)
app.include_router(admin_stats.router, prefix=ADMIN)
app.include_router(admin_envoy.router, prefix=ADMIN)

# OpenAI-compatible relay (root /v1/*)
app.include_router(relay_chat.router)
app.include_router(relay_embeddings.router)
app.include_router(relay_models.router)

# internal (envoy ext_authz / translator); bound to 127.0.0.1 in prod via
# settings.INTERNAL_API_HOST when run as a dedicated worker.
app.include_router(internal_relay.router)
app.include_router(internal_translate.router)


@app.on_event("startup")
async def startup() -> None:
    # Run migrations & seed in dev
    import asyncio
    from alembic import command
    from alembic.config import Config

    loop = asyncio.get_event_loop()

    def _migrate() -> None:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", "+asyncpg"))
        try:
            command.upgrade(cfg, "head")
        except Exception as e:
            logging.warning("alembic upgrade failed (continuing): %s", e)

    await loop.run_in_executor(None, _migrate)

    try:
        from app.scripts.seed import seed
        await seed()
    except Exception as e:
        logging.warning("seed failed (continuing): %s", e)

    # Start ALS gRPC server for envoy access logs (usage + billing ingest).
    # Single plaintext listener serves both local and remote envoys.
    try:
        from app.services.envoy import als_server
        await als_server.start()
    except Exception as e:
        logging.warning("ALS server failed to start (continuing): %s", e)

    # xDS ADS server for remote envoys (plaintext + shared token auth).
    try:
        from app.services.envoy import xds_server
        await xds_server.start()
    except Exception as e:
        logging.warning("xDS server failed to start (continuing): %s", e)

    # Subscription renewal worker (auto-charge wallet on period boundary).
    try:
        from app.services import subscriptions_renewal
        await subscriptions_renewal.start()
    except Exception as e:
        logging.warning("renewal worker failed to start (continuing): %s", e)

    # Report transport state so operators see at a glance whether envoy
    # is taking traffic or whether the api-direct path is the only one up.
    try:
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models import EnvoyInstance, EnvoyStatus

        async with AsyncSessionLocal() as s:
            rows = (
                await s.execute(
                    select(EnvoyInstance).where(EnvoyInstance.status == EnvoyStatus.running)
                )
            ).scalars().all()
        if rows:
            ports = ", ".join(str(r.listen_port) for r in rows)
            logging.info(
                "relay transport: envoy ACTIVE on port(s) %s; api-direct on :%s remains available",
                ports, settings.API_PORT,
            )
        else:
            logging.info(
                "relay transport: api-direct on :%s (no envoy instances running — start one from admin to enable the high-perf path)",
                settings.API_PORT,
            )
    except Exception as e:
        logging.warning("transport status check failed (continuing): %s", e)


@app.on_event("shutdown")
async def shutdown() -> None:
    try:
        from app.services.envoy import xds_server
        await xds_server.stop()
    except Exception:
        pass
    try:
        from app.services.envoy import als_server
        await als_server.stop()
    except Exception:
        pass
    try:
        from app.services import subscriptions_renewal
        await subscriptions_renewal.stop()
    except Exception:
        pass
