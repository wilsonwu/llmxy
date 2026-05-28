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
    kind: str = "chat"  # "chat" | "embedding"
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


class RouteExemplar(BaseModel):
    label: str
    text: str


class RoutePolicyIn(BaseModel):
    user_facing_model: str
    strategy: str = "weighted"  # weighted/smart/fallback
    targets_jsonb: list[RouteTarget] = []
    smart_rules_jsonb: list[RouteRule] = []
    smart_default_label: Optional[str] = None
    smart_embedding_model_id: Optional[int] = None
    smart_exemplars_jsonb: list[RouteExemplar] = []
    smart_score_threshold: int = 55  # cosine similarity percent, 0-100
    scope: str = "public"  # public | private
    enabled: bool = True


class RoutePolicyOut(BaseModel):
    id: int
    user_facing_model: str
    strategy: str
    targets_jsonb: list[dict]
    smart_rules_jsonb: list[dict] = []
    smart_default_label: Optional[str] = None
    smart_embedding_model_id: Optional[int] = None
    smart_exemplars_jsonb: list[dict] = []
    smart_score_threshold: int = 55
    smart_embedding_version: int = 0
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
    user_id: int
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
    admin_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    # Remote mode: operator supplies `host` (hostname or IP — no scheme, no
    # port) + `admin_port`; server derives admin_url = http://{host}:{admin_port}.
    # `admin_url` kept for back-compat with older clients that still send it
    # directly; ignored when `host` is also provided.
    host: Optional[str] = Field(default=None, max_length=255)
    admin_url: Optional[str] = Field(default=None, max_length=512)


class EnvoyInstanceUpdate(BaseModel):
    # mode + node_id are immutable. Everything else is optional; only fields
    # the caller supplies are applied.
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    listen_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    admin_port: Optional[int] = Field(default=None, ge=1024, le=65535)
    host: Optional[str] = Field(default=None, max_length=255)
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
        """Where clients point /v1/* traffic. For both modes listen_port is
        already the externally reachable port — local binds directly; remote
        gets the operator-supplied value (NodePort for k8s, host port for
        docker --network=host) post-deploy. No translation needed."""
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


class EnvoyManifestsOut(BaseModel):
    bootstrap_yaml: str
    k8s_yaml: str
    docker_run: str
    node_id: str
    control_plane_host: str
    xds_port: int
    als_port: int
    # NodePort values used in the k8s manifest. Distinct from the envoy bind
    # ports (which stay at whatever the operator configured) because k8s
    # requires NodePort be in 30000-32767. Surfaced so the UI can tell the
    # operator which external port to connect on.
    k8s_listen_nodeport: int
    k8s_admin_nodeport: int


class EnvoyManifestsPreviewIn(BaseModel):
    # Used by the create dialog to render deploy artifacts BEFORE persisting
    # the row, so the operator can deploy first and then come back with the
    # real host:port. The server derives node_id deterministically from name
    # (same rule as create), so the manifest copied here matches the one
    # eventually stored against this instance. Ports are NOT taken from the
    # caller — the manifest uses fixed envoy/NodePort constants so deploys
    # are uniform; the form's port fields are filled in post-deploy with the
    # actual reachable values.
    name: str = Field(min_length=1, max_length=64)


class EnvoyTestConnIn(BaseModel):
    admin_url: str


class EnvoyTestConnOut(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class EnvoyInstallStep(BaseModel):
    label: str
    command: str


class EnvoyLocalPrecheckOut(BaseModel):
    # Tells the UI whether local-mode envoy can actually be spawned on THIS
    # api host before the operator submits the create form. Anything other
    # than ok=true (mode disabled / wrong OS / binary missing) is surfaced
    # with a human reason + copy-pasteable install steps so the operator
    # isn't left guessing at the 400 from POST /instances.
    ok: bool
    mode_enabled: bool
    os: str  # "linux" | "darwin" | "windows" | other platform.system().lower()
    arch: str
    supported_os: bool
    installed: bool
    envoy_bin: str  # raw setting (may be a name on PATH or absolute path)
    resolved_path: Optional[str] = None
    version: Optional[str] = None
    reason: Optional[str] = None
    install_hint: Optional[str] = None
    install_steps: list[EnvoyInstallStep] = []
