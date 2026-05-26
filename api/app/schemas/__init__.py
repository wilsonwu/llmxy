from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


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


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    status: str
    quota_cents: int
    used_cents: int
    expires_at: Optional[datetime] = None
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

    class Config:
        from_attributes = True


class EnvoyConnectionOut(BaseModel):
    node_id: str
    ads_connected: bool
    last_seen_at: Optional[datetime] = None
    last_xds_version: Optional[str] = None


class EnvoyBootstrapOut(BaseModel):
    yaml: str
