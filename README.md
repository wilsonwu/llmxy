# llmxy

AI Token gateway / dispatcher. Three sub-projects in the same repo:

- `website/` — user-facing app (Next.js 14 + Tailwind + shadcn/ui)
- `admin/` — admin console (Next.js 14 + Tailwind + shadcn/ui)
- `api/` — backend (FastAPI + PostgreSQL + Redis). Connects directly to upstreams like OpenAI / Anthropic / Gemini, exposing a unified OpenAI-compatible protocol.

## Features

| Module | Features |
|------|------|
| website | Sign up / sign in, plan subscriptions, top up, API key management, usage & billing |
| admin | User & order management, upstream channel config, models & rates, plan config, smart routing policies & weights, stats |
| api | OpenAI-compatible `/v1/chat/completions` `/v1/embeddings` `/v1/models`; auth, billing, rate limiting; direct connections to OpenAI / Anthropic / Gemini |

## Quick start

```bash
cp .env.example .env        # edit JWT_SECRET, etc.
docker compose up -d --build
```

- API:     http://localhost:8000  (Swagger: /docs, health: /healthz)
- Website: http://localhost:3000
- Admin:   http://localhost:3001  (default admin in .env: SEED_ADMIN_EMAIL / PASSWORD)

## Local development

The three services run independently against a local Postgres + Redis. On Windows use Git Bash (or WSL) for the shell snippets below.

### 0. Prerequisites
- Python 3.11+, Node 18+, pnpm
- PostgreSQL 16+ (local install or `docker compose up -d postgres redis`)
- Redis 7+ (Windows: tporadowski fork via Scoop / Memurai; Linux/macOS: native)
- `cp .env.example .env` and edit `JWT_SECRET`, DB credentials, `ENCRYPTION_KEY` (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)

### 1. Start dependencies only (skip if you already have local PG/Redis)
```bash
docker compose up -d postgres redis
```

### 2. API (FastAPI, port 8000)
```bash
cd api
pip install -e .

# DB migrations (first run + after any model change)
alembic upgrade head

# Seed admin + free plan + demo channel/model/route; idempotent.
# Also backfills the free trial subscription for users that lack one.
python -m app.scripts.seed

# Run with reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger: http://localhost:8000/docs · Health: http://localhost:8000/healthz

Create a new Alembic migration after editing `app/models/`:
```bash
cd api
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

Run backend tests:
```bash
cd api && pytest -q
```

### 3. Website (Next.js, port 3000)
```bash
cd website
pnpm install
pnpm dev          # http://localhost:3000
# pnpm build && pnpm start   # production preview
```

### 4. Admin console (Next.js, port 3001)
```bash
cd admin
pnpm install
pnpm dev          # http://localhost:3001
# Sign in with SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD from .env
```

### Debug helpers
```bash
# Tail API logs (when launched via uvicorn --reload, logs go to stdout)

# Quick relay smoke test
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'

# Inspect DB (adjust user/db if not using defaults)
PGPASSWORD=llmxy_pass psql -U llmxy -d llmxy -h localhost \
  -c "SELECT id, user_id, type, amount_cents, note FROM balance_tx ORDER BY id DESC LIMIT 10;"

# Reset dev DB completely
cd api && alembic downgrade base && alembic upgrade head && python -m app.scripts.seed

# Mock a successful payment (dev only)
curl "http://localhost:8000/payments/alipay/mock-pay?order_id=<ID>"
```

## Architecture

See [docs/architecture.md](docs/architecture.md).

```
client ──► api (FastAPI) ──► OpenAI / Claude / Gemini / DeepSeek / ...
                ├── auth/billing/quota (PG + Redis)
website / admin ──► api
```

## Layout

```
llmxy/
├── api/        # FastAPI
├── website/    # Next.js user app
├── admin/      # Next.js admin console
├── docs/
├── docker-compose.yml
└── .env.example
```
