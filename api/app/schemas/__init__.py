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
    priority: int = 100
    weight: int = 1


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


class RoutePolicyIn(BaseModel):
    user_facing_model: str
    strategy: str = "weighted"  # weighted/smart/fallback
    targets_jsonb: list[RouteTarget] = []
    enabled: bool = True


class RoutePolicyOut(BaseModel):
    id: int
    user_facing_model: str
    strategy: str
    targets_jsonb: list[dict]
    enabled: bool

    class Config:
        from_attributes = True


# -------- Usage --------
class UsageLogOut(BaseModel):
    id: int
    user_id: int
    model_id: Optional[int]
    user_facing_model: Optional[str]
    upstream_model: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_cents: int
    latency_ms: int
    status: str
    created_at: datetime

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
