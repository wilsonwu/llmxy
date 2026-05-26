from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres
    POSTGRES_USER: str = "llmxy"
    POSTGRES_PASSWORD: str = "llmxy_pass"
    POSTGRES_DB: str = "llmxy"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PUBLIC_URL: str = "http://localhost:8000"
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALG: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001"

    # Seed admin
    SEED_ADMIN_EMAIL: str = "admin@llmxy.local"
    SEED_ADMIN_PASSWORD: str = "admin123456"

    # Upstream HTTP timeout (seconds) for non-stream requests
    UPSTREAM_TIMEOUT: int = 120

    # Azure OpenAI default api-version (channel-level override planned via extra config)
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"

    # Encryption key for upstream secrets (channels.api_key_enc).
    # Any string accepted — SHA-256 derives a Fernet key. Empty = dev plaintext mode.
    ENCRYPTION_KEY: str = ""

    # Default per-user requests-per-minute when plan provides none
    DEFAULT_RATE_LIMIT_RPM: int = 600

    # ===== Envoy front-proxy =====
    # Path to envoy binary
    ENVOY_BIN: str = "envoy"
    # Root dir for per-instance rendered configs: {ENVOY_CONFIG_ROOT}/{instance_name}/
    ENVOY_CONFIG_ROOT: str = "./var/envoy"
    ENVOY_LOG_ROOT: str = "./var/envoy-logs"
    # Internal API base envoy ext_authz / translator calls; must be reachable from envoy
    INTERNAL_API_HOST: str = "127.0.0.1"
    INTERNAL_API_PORT: int = 8000
    # gRPC ALS server (envoy AccessLogService client)
    ALS_GRPC_PORT: int = 8002
    # ===== Remote envoy (gRPC ADS, plaintext + shared token) =====
    # xDS ADS gRPC server. Public-reachable for remote envoy nodes.
    XDS_GRPC_PORT: int = 8003
    # Shared static token. Remote envoy bootstrap puts this in gRPC metadata
    # as `x-llmxy-token` for both ADS and ALS streams. Empty disables the check
    # (dev only — node_id matching still applies).
    XDS_AUTH_TOKEN: str = ""
    # First reachable host for the control plane, written into the remote
    # bootstrap template returned to operators.
    CONTROL_PLANE_PUBLIC_HOST: str = "127.0.0.1"
    # ext_authz max body buffer (bytes). Must be >= largest expected chat
    # completion request body — anything larger gets rejected upstream.
    ENVOY_EXT_AUTHZ_MAX_BYTES: int = 1024 * 1024  # 1 MiB
    # ext_authz call timeout (seconds). Should comfortably exceed p99 of the
    # internal /internal/relay/authz/* handler (DB lookup + balance check).
    ENVOY_EXT_AUTHZ_TIMEOUT: str = "5s"
    # Background health monitor: probe /ready on every active instance and
    # update last_health_at. After this many consecutive failures, flip
    # status=error and write last_error. 0 disables the monitor.
    ENVOY_HEALTH_INTERVAL_SECONDS: int = 30
    ENVOY_HEALTH_FAIL_THRESHOLD: int = 3
    # Local-mode envoys are managed by a single python process (subprocess.Popen
    # lives in memory). In multi-replica api deployments only one replica can
    # own a given local instance; set this false to forbid local-mode entirely
    # and require remote-mode envoys instead. Remote mode is multi-replica safe
    # by design (xDS push is broadcast via redis pub/sub).
    ENVOY_LOCAL_MODE_ENABLED: bool = True

    # Payments
    ALIPAY_APP_ID: str = ""
    ALIPAY_PRIVATE_KEY: str = ""
    ALIPAY_PUBLIC_KEY: str = ""
    WECHAT_APP_ID: str = ""
    WECHAT_MCH_ID: str = ""
    WECHAT_API_KEY: str = ""
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
