# llmxy api

FastAPI backend providing OpenAI-compatible `/v1/*` forwarding, user/admin REST APIs, and billing.

## Local run

```bash
pip install -e ".[dev]"
export $(cat ../.env | xargs)        # or copy .env into this directory
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs
