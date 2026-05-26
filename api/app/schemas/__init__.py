from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, computed_field


# -------- Auth / Users --------
class RegisterReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginReq(BaseModel):
    email: str
    password: str


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    balance_cents: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# -------- API Keys --------
class ApiKeyCreate(BaseModel):
    name: str
    quota_cents: int = 0
    expires_at: Optional[datetime] = None
    quota_mode: str = "until_depleted"  # until_depleted | periodic
    quota_period: Optional[str] = None  # day | week | month, required iff periodic


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    quota_cents: Optional[int] = Field(default=None, ge=0)
    expires_at: Optional[datetime] = None
    clear_expires_at: bool = False  # distinguish "leave alone" vs "clear to NULL"
    quota_mode: Optional[str] = None
    quota_period: Optional[str] = None


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    status: str
    quota_cents: int
    used_cents: int
    expires_at: Optional[datetime] = None
    quota_mode: str = "until_depleted"
    quota_period: Optional[str] = None
    quota_period_start: Optional[datetime] = None
    quota_period_end: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ApiKeyCreated(ApiKeyOut):
    key: str  # plaintext, shown only once


# -------- Plans --------
class PlanIn(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    plan_type: str = "recurring"  # "recurring" | "one_time"
    price_cents: int = 0
    quota_cents: int = 0
    duration_days: int = 30
    models_jsonb: Optional[dict] = None
    rate_limit_jsonb: Optional[dict] = None
    max_purchases_per_user: Optional[int] = Field(default=None, ge=1)
    active: bool = True


class PlanOut(PlanIn):
    id: int

    class Config:
        from_attributes = True


# -------- Orders / Payment --------
class OrderCreate(BaseModel):
    plan_id: Optional[int] = None
    amount_cents: int  # for top-up, or =plan.price_cents
    channel: str  # alipay/wechat/stripe


class OrderOut(BaseModel):
    id: int
    user_id: int
    plan_id: Optional[int]
    amount_cents: int
    channel: str
    status: str
    provider_order_id: Optional[str]
    created_at: datetime
    paid_at: Optional[datetime]

    class Config:
        from_attributes = True


class PaymentInitResp(BaseModel):
    order_id: int
    channel: str
    pay_url: Optional[str] = None
    qr_code: Optional[str] = None
    raw: dict[str, Any] = {}


# -------- Channels / Models / Routes --------
class ChannelIn(BaseModel):
    name: str
    provider_type: str = "openai"  # openai / anthropic / gemini
    base_url: str
    api_key_enc: Optional[str] = None
    enabled: bool = True


class ChannelOut(ChannelIn):
    id: int

    class Config:
        from_attributes = True


class ModelIn(BaseModel):
    code: str
    display_name: str
    channel_id: int
    upstream_model: str
    prompt_rate: int = 0  # micro-cent / 1K tokens
    completion_rate: int = 0
    enabled: bool = True


class ModelOut(ModelIn):
    id: int

    class Config:
        from_attributes = True


class RouteTarget(BaseModel):
    model_id: int
    weight: int = 1
    fallback_order: int = 0
    label: Optional[str] = None  # for smart strategy: matches rule/classifier output


class RouteRule(BaseModel):
    type: str  # "preset" | "tokens" | "keyword" | "code_block"
    # for preset:
    id: Optional[str] = None
    # for tokens:
    threshold: Optional[int] = None
    gt_label: Optional[str] = None
    lte_label: Optional[str] = None
    # for keyword:
    pattern: Optional[str] = None
    # for preset / keyword / code_block:
    label: Optional[str] = None


class RoutePolicyIn(BaseModel):
    user_facing_model: str
    strategy: str = "weighted"  # weighted/smart/fallback
    targets_jsonb: list[RouteTarget] = []
    smart_classifier_model_id: Optional[int] = None
    smart_rules_jsonb: list[RouteRule] = []
    smart_default_label: Optional[str] = None
    smart_classifier_hint: Optional[str] = None
    scope: str = "public"  # public | private
    enabled: bool = True


class RoutePolicyOut(BaseModel):
    id: int
    user_facing_model: str
    strategy: str
    targets_jsonb: list[dict]
    smart_classifier_model_id: Optional[int] = None
    smart_rules_jsonb: list[dict] = []
    smart_default_label: Optional[str] = None
    smart_classifier_hint: Optional[str] = None
    scope: str = "public"
    enabled: bool

    class Config:
        from_attributes = True


# -------- Usage --------
class UsageLogOut(BaseModel):
    id: int
    user_id: int
    api_key_id: Optional[int] = None
    model_id: Optional[int]
    user_facing_model: Optional[str]
    upstream_model: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_cents: int
    latency_ms: int
    status: str
    created_at: datetime
    kind: Optional[str] = "relay"
    resolved_label: Optional[str] = None

    class Config:
        from_attributes = True


class BalanceTxOut(BaseModel):
    id: int
    type: str
    amount_cents: int
    balance_after: int
    ref_id: Optional[str]
    note: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedResp(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class StatsOut(BaseModel):
    users_total: int
    api_keys_total: int
    requests_today: int
    cost_today_cents: int
    cost_total_cents: int


# -------- Subscriptions --------
class SubscriptionOut(BaseModel):
    id: int
    plan_id: int
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None
    plan_type: Optional[str] = None
    start_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    status: str
    remaining_cents: int
    cancel_at_period_end: bool = False
    canceled_at: Optional[datetime] = None
    last_renewal_at: Optional[datetime] = None
    last_renewal_error: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def depleted(self) -> bool:
        """True when an otherwise-active sub has zero quota left in the current
        period. Surfaces uniformly for one_time (no auto-refill, gone for good)
        and recurring (refills next renewal, but the current cycle is dry).
        Used by UI to distinguish "active and usable" from "active but spent."""
        return self.status == "active" and (self.remaining_cents or 0) <= 0

    class Config:
        from_attributes = True


class UserDetailOut(BaseModel):
    user: UserOut
    subscriptions: list[SubscriptionOut]
    spent_total_cents: int
    spent_30d_cents: int
    requests_total: int
    requests_30d: int


# -------- Envoy instances --------
class EnvoyInstanceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    mode: str = Field(default="local", pattern="^(local|remote)$")
    listen_port: int = Field(ge=1024, le=65535)
    # admin_port is required for local mode and ignored for remote.
    admin_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    # admin_url: required for remote (operator-supplied — how the control
    # plane reaches envoy's admin API). For local mode it's auto-derived.
    admin_url: Optional[str] = Field(default=None, max_length=512)


class EnvoyInstanceUpdate(BaseModel):
    # mode + node_id are immutable. Everything else is optional; only fields
    # the caller supplies are applied.
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    listen_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    admin_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    admin_url: Optional[str] = Field(default=None, max_length=512)


class EnvoyInstanceOut(BaseModel):
    id: int
    name: str
    mode: str
    node_id: str
    listen_port: int
    admin_port: Optional[int] = None
    admin_url: Optional[str] = None
    status: str
    pid: Optional[int] = None
    config_version: int
    config_dir: Optional[str] = None
    log_dir: Optional[str] = None
    last_health_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    last_xds_version: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def proxy_url(self) -> str:
        """Where clients point /v1/* traffic. For local mode this is exact; for
        remote it derives the host from admin_url and assumes the proxy listens
        on listen_port on the same host (override admin_url's port). If admin_url
        is not set or unparseable, falls back to listen_port-only display."""
        if self.mode == "local":
            return f"http://127.0.0.1:{self.listen_port}"
        if self.admin_url:
            from urllib.parse import urlparse
            u = urlparse(self.admin_url)
            host = u.hostname
            scheme = u.scheme or "http"
            if host:
                return f"{scheme}://{host}:{self.listen_port}"
        return f"http://<envoy-host>:{self.listen_port}"

    class Config:
        from_attributes = True


class EnvoyConnectionOut(BaseModel):
    node_id: str
    ads_connected: bool
    last_seen_at: Optional[datetime] = None
    last_xds_version: Optional[str] = None


class EnvoyBootstrapOut(BaseModel):
    yaml: str


class EnvoyTestConnIn(BaseModel):
    admin_url: str


class EnvoyTestConnOut(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
