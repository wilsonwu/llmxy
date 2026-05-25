from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.relay import chat as relay_chat
from app.api.relay import embeddings as relay_embeddings
from app.api.relay import models_list as relay_models
from app.api.v1 import api_keys, auth, orders, plans, usage
from app.api.v1.admin import channels as admin_channels
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

# admin
ADMIN = "/api/v1/admin"
app.include_router(admin_users.router, prefix=ADMIN)
app.include_router(admin_channels.router, prefix=ADMIN)
app.include_router(admin_models.router, prefix=ADMIN)
app.include_router(admin_plans.router, prefix=ADMIN)
app.include_router(admin_routes.router, prefix=ADMIN)
app.include_router(admin_stats.router, prefix=ADMIN)

# OpenAI-compatible relay (root /v1/*)
app.include_router(relay_chat.router)
app.include_router(relay_embeddings.router)
app.include_router(relay_models.router)


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
