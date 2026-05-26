"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("user", "admin", name="userrole"), nullable=False, server_default="user"),
        sa.Column("balance_cents", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("status", sa.Enum("active", "disabled", name="userstatus"), nullable=False, server_default="active"),
        sa.Column("oauth_provider", sa.String(32)),
        sa.Column("oauth_uid", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("status", sa.Enum("active", "disabled", name="keystatus"), nullable=False, server_default="active"),
        sa.Column("quota_cents", sa.BigInteger, server_default="0"),
        sa.Column("used_cents", sa.BigInteger, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(64), unique=True, index=True),
        sa.Column("name", sa.String(128)),
        sa.Column("description", sa.Text),
        sa.Column("plan_type", sa.String(16), nullable=False, server_default="recurring"),
        sa.Column("price_cents", sa.Integer, server_default="0"),
        sa.Column("quota_cents", sa.BigInteger, server_default="0"),
        sa.Column("duration_days", sa.Integer, server_default="30"),
        sa.Column("models_jsonb", sa.JSON),
        sa.Column("rate_limit_jsonb", sa.JSON),
        sa.Column("max_purchases_per_user", sa.Integer, nullable=True),
        sa.Column("active", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id")),
        sa.Column("start_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("current_period_start", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), server_default="active"),
        sa.Column("remaining_cents", sa.BigInteger, server_default="0"),
        sa.Column("cancel_at_period_end", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("last_renewal_at", sa.DateTime(timezone=True)),
        sa.Column("last_renewal_error", sa.String(256)),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("plans.id")),
        sa.Column("amount_cents", sa.Integer),
        sa.Column("channel", sa.Enum("alipay", "wechat", "stripe", "manual", name="paymentchannel")),
        sa.Column("status", sa.Enum("pending", "paid", "canceled", "refunded", name="orderstatus"), server_default="pending"),
        sa.Column("provider_order_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128)),
        sa.Column("provider_type", sa.String(32), server_default="openai"),
        sa.Column("base_url", sa.String(512)),
        sa.Column("api_key_enc", sa.String(512)),
        sa.Column("enabled", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "models",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(128), index=True),
        sa.Column("display_name", sa.String(128)),
        sa.Column("channel_id", sa.Integer, sa.ForeignKey("channels.id")),
        sa.Column("upstream_model", sa.String(128)),
        sa.Column("prompt_rate", sa.BigInteger, server_default="0"),
        sa.Column("completion_rate", sa.BigInteger, server_default="0"),
        sa.Column("enabled", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "route_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_facing_model", sa.String(128), unique=True, index=True),
        sa.Column("strategy", sa.Enum("weighted", "smart", "fallback", name="routestrategy"), server_default="weighted"),
        sa.Column("targets_jsonb", sa.JSON),
        sa.Column("smart_classifier_model_id", sa.Integer, sa.ForeignKey("models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("smart_rules_jsonb", sa.JSON, server_default=sa.text("'[]'")),
        sa.Column("smart_default_label", sa.String(64), nullable=True),
        sa.Column("smart_classifier_hint", sa.Text, nullable=True),
        sa.Column("scope", sa.Enum("public", "private", name="routescope"), server_default="public"),
        sa.Column("enabled", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "usage_logs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("api_key_id", sa.BigInteger, sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("model_id", sa.Integer, sa.ForeignKey("models.id")),
        sa.Column("user_facing_model", sa.String(128)),
        sa.Column("upstream_model", sa.String(128)),
        sa.Column("prompt_tokens", sa.Integer, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, server_default="0"),
        sa.Column("cost_cents", sa.Integer, server_default="0"),
        sa.Column("latency_ms", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(32), server_default="ok"),
        sa.Column("request_id", sa.String(64)),
        sa.Column("kind", sa.String(16), nullable=False, server_default="relay"),
        sa.Column("resolved_label", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    op.create_table(
        "balance_tx",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("type", sa.Enum("topup", "consume", "refund", "grant", name="balancetxtype")),
        sa.Column("amount_cents", sa.BigInteger),
        sa.Column("balance_after", sa.BigInteger),
        sa.Column("ref_id", sa.String(64)),
        sa.Column("note", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # envoy_instances: status / mode stored as plain VARCHAR (not PG ENUM) because
    # asyncpg's ENUM creation interacts badly with repeated migration attempts
    # and the Python-side enum (str-based) round-trips cleanly through text.
    # `admin_port` / `config_dir` / `log_dir` are nullable so remote-mode
    # instances (managed via gRPC ADS) don't need to allocate them.
    op.create_table(
        "envoy_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="local"),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("listen_port", sa.Integer(), nullable=False),
        sa.Column("admin_port", sa.Integer(), nullable=True),
        sa.Column("admin_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="stopped"),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config_dir", sa.String(512), nullable=True),
        sa.Column("log_dir", sa.String(512), nullable=True),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_xds_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_envoy_instances_name", "envoy_instances", ["name"], unique=True)
    op.create_index("ix_envoy_instances_node_id", "envoy_instances", ["node_id"], unique=True)

    # hot-path indexes
    op.create_index(
        "ix_usage_logs_user_created",
        "usage_logs",
        ["user_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index("ix_usage_logs_created", "usage_logs", ["created_at"])
    op.create_index("ix_balance_tx_user_created", "balance_tx", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_balance_tx_user_created", table_name="balance_tx")
    op.drop_index("ix_usage_logs_created", table_name="usage_logs")
    op.drop_index("ix_usage_logs_user_created", table_name="usage_logs")
    op.drop_index("ix_envoy_instances_node_id", table_name="envoy_instances")
    op.drop_index("ix_envoy_instances_name", table_name="envoy_instances")
    for t in [
        "envoy_instances", "balance_tx", "usage_logs", "route_policies",
        "models", "channels", "orders", "subscriptions", "plans",
        "api_keys", "users",
    ]:
        op.drop_table(t)
    for e in ["balancetxtype", "routescope", "routestrategy", "orderstatus", "paymentchannel", "keystatus", "userstatus", "userrole"]:
        op.execute(f"DROP TYPE IF EXISTS {e}")
