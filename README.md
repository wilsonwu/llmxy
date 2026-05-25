# llmxy

AI Token 中转/分发平台。同一仓库下三个子项目：

- `website/` — 用户端 (Next.js 14 + Tailwind + shadcn/ui)
- `admin/` — 管理端 (Next.js 14 + Tailwind + shadcn/ui)
- `api/` — 后端 (FastAPI + PostgreSQL + Redis)，直连 OpenAI / Anthropic / Gemini 等上游，对外统一 OpenAI 兼容协议。

## 功能概览

| 模块 | 功能 |
|------|------|
| website | 注册/登录、套餐订阅、充值、API Key 管理、用量与账单 |
| admin | 用户/订单管理、上游通道配置、模型与倍率、套餐配置、智能路由策略与权重、统计 |
| api | OpenAI 兼容 `/v1/chat/completions` `/v1/embeddings` `/v1/models`；鉴权、计费、限流、直连 OpenAI / Anthropic / Gemini 上游 |

## 一键启动

```bash
cp .env.example .env        # 按需修改 JWT_SECRET 等
docker compose up -d --build
```

- API:     http://localhost:8000  (Swagger: /docs, 健康: /healthz)
- Website: http://localhost:3000
- Admin:   http://localhost:3001  (默认管理员见 .env: SEED_ADMIN_EMAIL / PASSWORD)

## 本地开发

```bash
# API
cd api && pip install -e . && uvicorn app.main:app --reload

# Website / Admin
cd website && pnpm install && pnpm dev
cd admin   && pnpm install && pnpm dev
```

## 架构

详见 [docs/architecture.md](docs/architecture.md)。

```
client ──► api (FastAPI) ──► OpenAI / Claude / Gemini / DeepSeek / ...
                ├── auth/billing/quota (PG + Redis)
website / admin ──► api
```

## 目录

```
llmxy/
├── api/        # FastAPI
├── website/    # Next.js 用户端
├── admin/      # Next.js 管理端
├── docs/
├── docker-compose.yml
└── .env.example
```
