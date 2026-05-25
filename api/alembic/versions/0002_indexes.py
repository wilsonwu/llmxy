"""hot-path indexes

Revision ID: 0002_indexes
Revises: 0001_initial
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_indexes"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_usage_logs_user_created",
        "usage_logs",
        ["user_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_usage_logs_created",
        "usage_logs",
        ["created_at"],
    )
    op.create_index(
        "ix_balance_tx_user_created",
        "balance_tx",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_balance_tx_user_created", table_name="balance_tx")
    op.drop_index("ix_usage_logs_created", table_name="usage_logs")
    op.drop_index("ix_usage_logs_user_created", table_name="usage_logs")
