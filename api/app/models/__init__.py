from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class UserStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class KeyStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    expired = "expired"


class QuotaMode(str, enum.Enum):
    until_depleted = "until_depleted"
    periodic = "periodic"


class QuotaPeriod(str, enum.Enum):
    day = "day"
    week = "week"
    month = "month"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    canceled = "canceled"
    refunded = "refunded"


class PaymentChannel(str, enum.Enum):
    alipay = "alipay"
    wechat = "wechat"
    stripe = "stripe"
    manual = "manual"


class RouteStrategy(str, enum.Enum):
    weighted = "weighted"
    smart = "smart"
    fallback = "fallback"


class RouteScope(str, enum.Enum):
    public = "public"   # listed in /v1/models, callable by any user
    private = "private"  # hidden from listing & user calls; reserved for internal use (e.g. smart classifier)


class BalanceTxType(str, enum.Enum):
    topup = "topup"
    consume = "consume"
    refund = "refund"
    grant = "grant"


class PlanType(str, enum.Enum):
    recurring = "recurring"   # monthly auto-renew, charged per cycle
    one_time = "one_time"     # fixed duration, charged once, then expires


class EnvoyStatus(str, enum.Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    error = "error"


class EnvoyMode(str, enum.Enum):
    local = "local"
    remote = "remote"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.user, nullable=False)
    balance_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[UserStatus] = mapped_column(SAEnum(UserStatus), default=UserStatus.active, nullable=False)
    oauth_provider: Mapped[Optional[str]] = mapped_column(String(32))
    oauth_uid: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all,delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[KeyStatus] = mapped_column(SAEnum(KeyStatus), default=KeyStatus.active, nullable=False)
    quota_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # 0 = unlimited
    used_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quota_mode: Mapped[QuotaMode] = mapped_column(
        SAEnum(QuotaMode), default=QuotaMode.until_depleted, nullable=False
    )
    # periodic-only: nullable for until_depleted mode.
    quota_period: Mapped[Optional[QuotaPeriod]] = mapped_column(SAEnum(QuotaPeriod))
    quota_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quota_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="api_keys")


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text)
    # recurring: price_cents is per-cycle (monthly), duration_days ignored.
    # one_time: price_cents charged once, duration_days = lifetime, no renewal.
    plan_type: Mapped[str] = mapped_column(String(16), default="recurring", nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    quota_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    models_jsonb: Mapped[Optional[dict]] = mapped_column(JSON)
    rate_limit_jsonb: Mapped[Optional[dict]] = mapped_column(JSON)
    # one_time only. NULL = unlimited; positive int = lifetime purchase cap per user.
    # Ignored for recurring (one-active-sub rule still applies).
    max_purchases_per_user: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Current billing period. Renewal advances both fields and refills remaining_cents.
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # status: active | past_due | canceled | expired
    status: Mapped[str] = mapped_column(String(32), default="active")
    remaining_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_renewal_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_renewal_error: Mapped[Optional[str]] = mapped_column(String(256))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    channel: Mapped[PaymentChannel] = mapped_column(SAEnum(PaymentChannel))
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.pending)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Channel(Base):
    """Upstream channel — one row per provider account (openai/anthropic/gemini)."""
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    provider_type: Mapped[str] = mapped_column(String(32), default="openai")
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_enc: Mapped[Optional[str]] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Model(Base):
    """Sellable model bound to a channel, with billing rates."""
    __tablename__ = "models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), index=True)  # public-facing name
    display_name: Mapped[str] = mapped_column(String(128))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
    upstream_model: Mapped[str] = mapped_column(String(128))  # real upstream model name
    # "chat" (default) | "embedding" | "image". Smart-routing exemplar
    # embeddings use kind=embedding. Image models bill via pricing_jsonb.
    kind: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)
    # Per-model upstream wire-protocol override; selects the translation
    # adapter (openai/azure/anthropic/gemini for chat; openai/azure/gemini for
    # image). NULL falls back to the channel's provider_type, so one channel
    # can host models of different protocols (e.g. Azure AI Foundry serves both
    # azure-openai gpt-* and openai-compatible Llama/DeepSeek deployments).
    upstream_protocol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # rate per 1K tokens, stored as 1/10000 cents for precision (i.e. micro-cents).
    # cost_cents = ceil((prompt*pr + completion*cr) / 10000 / 1000)
    # For embedding models completion_rate is ignored.
    prompt_rate: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_rate: Mapped[int] = mapped_column(BigInteger, default=0)
    # Modality-specific pricing. Empty {} = token-based (chat/embedding).
    # image per_image mode:
    #   {"mode":"per_image",
    #    "tiers":[{"size":"1024x1024","quality":"standard","price_micro":400000}, ...],
    #    "default_price_micro":400000}
    # image token mode (gpt-image-1 style): uses prompt_rate/completion_rate plus
    #   {"mode":"token","output_tokens":{"1024x1024|high":4160,...},"default_out_tokens":1056}
    # price_micro / out_tokens are per single image; total scales by n.
    pricing_jsonb: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoutePolicy(Base):
    """Route from a user-facing model name to one or more concrete models."""
    __tablename__ = "route_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_facing_model: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Forwarding modality this route serves: "chat" | "embedding" | "image".
    # Each modality is independent; all targets must be models of this kind,
    # and only the matching endpoint (/v1/chat/completions, /v1/embeddings,
    # /v1/images/generations) will resolve it.
    modality: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat", nullable=False)
    strategy: Mapped[RouteStrategy] = mapped_column(SAEnum(RouteStrategy), default=RouteStrategy.weighted)
    targets_jsonb: Mapped[list] = mapped_column(JSON, default=list)
    # targets: [{model_id:int, weight:int, fallback_order:int, label?:str}]
    # ---- smart-strategy config (ignored unless strategy=smart) ----
    smart_rules_jsonb: Mapped[list] = mapped_column(JSON, default=list)
    # rules: ordered list; first hit wins. Forms:
    #   {"type":"tokens","threshold":500,"gt_label":"strong","lte_label":"cheap"}
    #   {"type":"keyword","pattern":"<python-regex>","label":"<label>"}
    #   {"type":"code_block","label":"<label>"}
    smart_default_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Embedding-based classifier: model used to embed prompts + exemplars.
    smart_embedding_model_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL"), nullable=True
    )
    # exemplars: [{"label":"strong","text":"…"}, ...] — 3-10 per label is plenty.
    smart_exemplars_jsonb: Mapped[list] = mapped_column(JSON, default=list)
    # Cosine similarity cutoff as integer percent (0-100). Below this, fall through
    # to smart_default_label. 55 ≈ "moderately confident" for sentence-level embeddings.
    smart_score_threshold: Mapped[int] = mapped_column(Integer, default=55, nullable=False)
    # Bumped on every exemplar / embedding-model change → invalidates Redis cache.
    smart_embedding_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scope: Mapped[RouteScope] = mapped_column(SAEnum(RouteScope), default=RouteScope.public)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageLog(Base):
    __tablename__ = "usage_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    api_key_id: Mapped[Optional[int]] = mapped_column(ForeignKey("api_keys.id", ondelete="SET NULL"))
    model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("models.id"))
    user_facing_model: Mapped[Optional[str]] = mapped_column(String(128))
    upstream_model: Mapped[Optional[str]] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Images actually billed for this row (0 for token modalities).
    image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Modality detail: {"size":..., "quality":..., "requested_n":..., "mode":...}.
    meta_jsonb: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ok")
    request_id: Mapped[Optional[str]] = mapped_column(String(64))
    # "relay" = main upstream call; "classifier" = smart-mode classifier overhead.
    # Same request_id ties classifier rows to their relay row.
    kind: Mapped[str] = mapped_column(String(16), default="relay")
    # Label chosen by smart routing (rules or classifier). Null for non-smart.
    resolved_label: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class BalanceTx(Base):
    __tablename__ = "balance_tx"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[BalanceTxType] = mapped_column(SAEnum(BalanceTxType))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    ref_id: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnvoyInstance(Base):
    """A single envoy daemon — either locally managed (subprocess) or remote (gRPC ADS)."""
    __tablename__ = "envoy_instances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode: Mapped[EnvoyMode] = mapped_column(
        SAEnum(EnvoyMode, native_enum=False, length=16),
        default=EnvoyMode.local,
        nullable=False,
        server_default="local",
    )
    # node_id reported by envoy in xDS/ALS — must match a row here for the
    # stream to be accepted.
    node_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    # listen_port: for local, the local bind port (uniqueness enforced in API).
    # For remote, the port envoy listens on at the remote host — many remote
    # nodes can share the same port number, so no DB uniqueness.
    listen_port: Mapped[int] = mapped_column(Integer)
    # admin_port: local mode binds envoy admin here. NULL for remote.
    admin_port: Mapped[Optional[int]] = mapped_column(Integer)
    # admin_url: how the control plane reaches envoy's admin API for stats /
    # readiness probes. Local: auto-derived `http://127.0.0.1:{admin_port}`.
    # Remote: supplied by the operator at create time.
    admin_url: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[EnvoyStatus] = mapped_column(SAEnum(EnvoyStatus, native_enum=False, length=16), default=EnvoyStatus.stopped, nullable=False)
    pid: Mapped[Optional[int]] = mapped_column(Integer)
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config_dir: Mapped[Optional[str]] = mapped_column(String(512))
    log_dir: Mapped[Optional[str]] = mapped_column(String(512))
    last_health_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_xds_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
