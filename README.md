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

```bash
# API
cd api && pip install -e . && uvicorn app.main:app --reload

# Website / Admin
cd website && pnpm install && pnpm dev
cd admin   && pnpm install && pnpm dev
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
