# llmxy api

FastAPI 后端，提供 OpenAI 兼容 `/v1/*` 转发、用户/管理 REST API、计费。

## 本地运行

```bash
pip install -e ".[dev]"
export $(cat ../.env | xargs)        # 或拷贝 .env 到当前目录
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs
