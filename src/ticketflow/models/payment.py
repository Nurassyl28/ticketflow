from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ticketflow.models.base import Base, CreatedAt, UUIDPrimaryKey, money_check


class Payment(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "payments"
    __table_args__ = (
        ForeignKeyConstraint(["order_id", "user_id"], ["orders.id", "orders.user_id"]),
        UniqueConstraint("order_id", name="uq_payments_order"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_key"),
        money_check("amount"),
        CheckConstraint("currency = 'KZT'", name="currency_kzt"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND refunded_at IS NULL) OR "
            "(status = 'REFUNDED' AND refunded_at IS NOT NULL AND refunded_at >= created_at)",
            name="refund_state",
        ),
    )

    order_id: Mapped[UUID]
    user_id: Mapped[UUID]
    idempotency_key: Mapped[UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    status: Mapped[str] = mapped_column(String(20), default="SUCCEEDED")
    refunded_at: Mapped[datetime | None]
