from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.internal import relay as internal_relay
from app.api.internal import translate as internal_translate
from app.api.relay import chat as relay_chat
from app.api.relay import embeddings as relay_embeddings
from app.api.relay import models_list as relay_models
from app.api.v1 import api_keys, auth, orders, plans, transport, usage
from app.api.v1.admin import channels as admin_channels
from app.api.v1.admin import envoy as admin_envoy
from app.api.v1.admin import models as admin_models
from app.api.v1.admin import plans as admin_plans
from app.api.v1.admin import routes as admin_routes
from app.api.v1.admin import stats as admin_stats
from app.api.v1.admin import users as admin_users
from app.core.config import settings
from app.core.request_ctx import request_id_var


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [rid=%(request_id)s] %(message)s"))
_handler.addFilter(_RequestIdFilter())
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)


app = FastAPI(title="llmxy api", version="0.1.0")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


app.add_middleware(RequestIdMiddleware)

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
    try:
        from app.services.envoy import als_server
        await als_server.start()
    except Exception as e:
        logging.warning("ALS server failed to start (continuing): %s", e)

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
        from app.services.envoy import als_server
        await als_server.stop()
    except Exception:
        pass
