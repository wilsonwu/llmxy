# Project Guidelines

## Project Shape

- `llmxy` is an AI token gateway / dispatcher. It exposes OpenAI-compatible relay APIs, routes requests to upstream LLM providers, handles auth, quota, billing, and usage logs, and optionally serves a high-performance Envoy front-proxy path.
- The repo has three main apps: `api/` is the FastAPI backend and only backend; `website/` is the user portal; `admin/` is the management console. Shared deployment assets live under `deployments/`.
- Prefer existing docs over restating everything. Start with `README.md` and `docs/architecture.md` when a task needs architecture context.

## Hard Invariants

- `/v1/*` relay requests authenticate with `sk-` API keys. `/api/v1/*` platform APIs authenticate with JWT bearer tokens from website/admin.
- PostgreSQL is the source of truth for users, plans, subscriptions, API keys, routes, models, channels, balances, and usage logs. Redis is cache or hot-path mirror state.
- Billing uses integer cents for balances and transactions. Model token rates are micro-cents per 1K tokens: `cost_cents = ceil((prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 10_000_000)`.
- Keep `provider_type` / `upstream_protocol` separate from `connector_type`. Protocol is semantic request/response shape such as `openai.chat`, `openai.responses`, `anthropic.messages`, or `gemini.generate_content`; connector is URL/auth/path implementation such as `openai`, `azure_openai`, `anthropic`, or `gemini`.
- Route policies map a public `user_facing_model` to concrete `Model` targets. Always preserve `modality`, `exposed_protocols`, target weights, labels, and `scope` semantics. Smart targets without a label belong to the implicit `default` label used for unmatched selection. Weighted and smart routes may configure one optional fallback model for failed primaries; it may also appear among targets but must not be attempted twice in one request.
- Envoy is optional and never silently redirects clients. Clients choose `api-direct` via `:8000` or Envoy via `:9000-9099` / remote Envoy URLs.

## Backend Conventions

- Backend code is Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, Redis, and httpx. Keep I/O async and use `AsyncSession` patterns already present in `api/app/`.
- Router split: `api/app/api/v1/` for user platform APIs, `api/app/api/v1/admin/` for admin APIs, `api/app/api/relay/` for public OpenAI-compatible relay endpoints, and `api/app/api/internal/` for internal translator callbacks.
- Provider adapters live in `api/app/services/providers/`; protocol conversion helpers live in `api/app/services/protocols/`; route selection lives in `api/app/services/providers/router.py`.
- During the current development phase, `api/alembic/versions/0001_initial.py` is the only migration. When models or tables change, update that file directly; do not create `0002` or later revisions until this rule is explicitly lifted. Rebuild or reset local development schemas as needed. Keep `api/app/scripts/seed.py` idempotent when seed data must change.
- After balance, quota, subscription, user status, API key status, route, model, or channel mutations, preserve the existing cache invalidation or Redis mirror behavior.

## Frontend Conventions

- `website/` and `admin/` are separate Next.js 14 App Router apps with Tailwind CSS and strict TypeScript.
- Use the local API wrapper in `src/lib/api.ts` for backend calls. It owns `NEXT_PUBLIC_API_BASE_URL`, localStorage token handling, auth headers, 401 redirect behavior, and error shaping.
- `website` stores JWT in `llmxy_token`; `admin` stores JWT in `llmxy_admin_token`.
- Follow each app's existing visual style. Admin screens are operational dashboards: dense, calm, scannable, and action-oriented. Website screens can be more user-facing but should still use the existing components and brand palette.
- This repo currently has `package-lock.json` files and no `pnpm-lock.yaml`. Prefer npm commands unless the package manager is deliberately changed for the whole app.

## Common Commands

- Full compose: `cp .env.example .env` then `docker compose up -d --build`.
- Dependencies only: `docker compose up -d postgres redis`.
- Backend setup: `cd api && pip install -e ".[dev]" && alembic upgrade head && python -m app.scripts.seed`.
- Backend dev server: `cd api && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`.
- Backend tests: `cd api && pytest -q`.
- Targeted backend tests: `cd api && pytest -q tests/test_billing.py tests/test_openai_compat.py tests/test_smart_routing.py`.
- Website dev: `cd website && npm install && npm run dev` for port 3000.
- Admin dev: `cd admin && npm install && npm run dev` for port 3001.
- Frontend type checks: `cd website && npx tsc --noEmit --incremental false`; `cd admin && npx tsc --noEmit --incremental false`.
- `npm run lint` may trigger Next.js first-run ESLint setup in this repo. Prefer explicit TypeScript checks unless ESLint has been configured.

## Change Discipline

- Keep edits scoped to the touched subsystem. Do not introduce new frameworks, package managers, background workers, or storage layers unless the task requires it.
- Never commit secrets. `.env` is local; `.env.example` is the documented shape.
- Do not double-charge relay requests. API-direct billing happens in the relay request path. Envoy translator-backed requests carry `x-llmxy-billed-sync=true` and bill after the successful upstream call in the translator; ALS skips marked requests and bills only unmarked Envoy paths.
- When touching relay protocols, billing, quota, route selection, or provider adapters, add or update focused backend tests first-class with the existing tests.
