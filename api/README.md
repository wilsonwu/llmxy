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

During the current development phase, edit `alembic/versions/0001_initial.py`
for every schema change. Do not create `0002` or later revisions until this
repository rule is explicitly lifted. Reset or recreate the development schema
before running `alembic upgrade head`; Alembic does not replay an applied `0001`.

Swagger: http://localhost:8000/docs
