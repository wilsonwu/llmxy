---
description: "Use when working on the llmxy FastAPI backend, SQLAlchemy models, Alembic migrations, relay endpoints, provider adapters, billing, quota, Redis caches, Envoy services, or pytest tests."
applyTo:
  - "api/**/*.py"
  - "api/alembic/**/*.py"
  - "api/pyproject.toml"
---
# Backend API Instructions

## Architecture Map

- `api/app/main.py` wires routers and startup workers.
- `api/app/api/v1/` serves JWT-authenticated user APIs; `api/app/api/v1/admin/` serves admin APIs.
- `api/app/api/relay/` serves OpenAI-compatible `sk-` authenticated endpoints: chat, responses, embeddings, images, models, and Anthropic compatibility.
- `api/app/services/providers/` contains upstream connector adapters and route selection.
- `api/app/services/protocols/` contains protocol ID and request/response conversion helpers.
- `api/app/models/__init__.py` is the SQLAlchemy model hub. During development, all schema changes stay in `api/alembic/versions/0001_initial.py`; do not add later revisions until the repository rule is explicitly lifted.

## Python Style

- Keep `from __future__ import annotations` in Python modules that already use it.
- Use async FastAPI and SQLAlchemy patterns. Do not add sync database or HTTP calls on request paths.
- Prefer Pydantic/FastAPI validation and typed helper functions over ad hoc dictionaries at API boundaries.
- Keep comments rare and useful. Existing code has short comments for billing, cache, Envoy, and migration edge cases where the invariant is easy to break.

## Relay And Provider Rules

- Load route policies with the expected modality and client-exposed protocol. A chat route must not be callable from embeddings/images endpoints, and a private route must not appear in public model lists.
- Resolve the effective upstream protocol from `Model.upstream_protocol` first, then `Channel.provider_type`. Resolve the connector from `Channel.connector_type` and connector aliases.
- Register connector/protocol support in `api/app/services/providers/registry.py` when adding a new connector or semantic protocol.
- Streaming chat responses returned to OpenAI-compatible clients must be OpenAI `chat.completion.chunk` SSE. Responses API streaming uses conversion helpers in `api/app/services/protocols/openai_responses.py`.
- Preserve usage extraction for streaming responses. Billing and `UsageLog` rows depend on prompt/completion tokens found in final chunks or adapter results.
- Keep max token compatibility behavior in `api/app/services/providers/openai_compat.py`; upstreams may require `max_tokens` or `max_completion_tokens`.

## Billing And Quota Rules

- Token rates are micro-cents per 1K tokens. Use `calc_cost_cents`, not inline formulas.
- `charge_user` drains active subscription quota by nearest period end, then wallet balance, then increments API key usage and writes one consume `BalanceTx`.
- Smart-routing embedding classifier usage is charged and logged separately with `UsageLog.kind == "classifier"` and the same request ID as the relay row.
- PG is authoritative. Redis quota state is a hot-path mirror; use existing `quota_cache.apply_*` helpers after successful commits when mirroring exact deltas, or mark `db.info["_quota_invalidate_uids"]` for post-commit invalidation.
- API key/user snapshot caches live in `api/app/services/api_key_cache.py`. Invalidate or refresh snapshots when changing API key status, expiry, quotas, user status, or plan RPM inputs.

## Envoy Rules

- Envoy local mode is managed through admin-created instances and listener ports 9000-9099. Remote mode uses xDS ADS, ext_proc, and ALS gRPC ports from `.env.example`.
- The Envoy path uses ext_proc for authz/route resolution and ALS for usage and async billing. Avoid adding PG-heavy work to per-request ext_proc hot paths unless there is no cache-safe option.
- Non-OpenAI upstream protocols may be routed through internal translator endpoints so Envoy can observe uniform OpenAI-shaped streams for usage extraction.

## Testing And Validation

- Run `cd api && pytest -q` for broad backend validation.
- For provider/protocol changes, run or update `tests/test_openai_compat.py` and add focused adapter tests when behavior changes.
- For billing/quota changes, run or update `tests/test_billing.py` and verify cache invalidation/mirroring behavior by reading the affected helper.
- For smart routing changes, run or update `tests/test_smart_routing.py`.
- For model schema changes, edit `0001_initial.py` directly, rebuild/reset the development schema as needed, and verify `cd api && alembic upgrade head`. Do not create `0002` or later revisions.
