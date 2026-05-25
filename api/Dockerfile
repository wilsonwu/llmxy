### envoy binary (multi-stage; the official image ships a static linux/amd64 binary)
FROM envoyproxy/envoy:v1.31-latest AS envoy

FROM python:3.11-slim

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# envoy binary
COPY --from=envoy /usr/local/bin/envoy /usr/local/bin/envoy

# pre-create the config/log roots
RUN mkdir -p /var/llmxy/envoy /var/llmxy/envoy-logs

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY . .

ENV ENVOY_BIN=/usr/local/bin/envoy \
    ENVOY_CONFIG_ROOT=/var/llmxy/envoy \
    ENVOY_LOG_ROOT=/var/llmxy/envoy-logs \
    INTERNAL_API_HOST=127.0.0.1 \
    INTERNAL_API_PORT=8001 \
    ALS_GRPC_PORT=8002

EXPOSE 8000 8001 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
