"""add mock payments

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-22 11:06:25.581043

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq_orders_id_user", "orders", ["id", "user_id"])
    op.create_table(
        "payments",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND refunded_at IS NULL) OR "
            "(status = 'REFUNDED' AND refunded_at IS NOT NULL AND refunded_at >= created_at)",
            name=op.f("ck_payments_refund_state"),
        ),
        sa.CheckConstraint("currency = 'KZT'", name=op.f("ck_payments_currency_kzt")),
        sa.CheckConstraint(
            "amount BETWEEN 0 AND 9999999999.99", name=op.f("ck_payments_amount_range")
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "user_id"],
            ["orders.id", "orders.user_id"],
            name=op.f("fk_payments_order_id_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("order_id", name="uq_payments_order"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("payments")
    op.drop_constraint("uq_orders_id_user", "orders", type_="unique")
