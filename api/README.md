# llmxy api

FastAPI backend providing OpenAI-compatible `/v1/*` forwarding, user/admin REST APIs, and billing.

## Local run

```bash
pip install -e ".[dev]"
export $(cat ../.env | xargs)        # or copy .env into this directory
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger: http://localhost:8000/docs
