from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ticketflow.models.base import Base, CreatedAt, UUIDPrimaryKey, enum_type, money_check
from ticketflow.models.enums import OrderStatus, ReservationStatus, TicketStatus


class ReservationGroup(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "reservation_groups"
    __table_args__ = (
        UniqueConstraint("id", "user_id", "event_id", name="uq_reservation_groups_owner_event"),
        UniqueConstraint(
            "id", "user_id", "event_id", "expires_at", name="uq_reservation_groups_shared_expiry"
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(
        server_default=text("now() + interval '10 minutes'")
    )


class TicketReservation(UUIDPrimaryKey, Base):
    __tablename__ = "ticket_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "user_id", "event_id", "expires_at"],
            [
                "reservation_groups.id",
                "reservation_groups.user_id",
                "reservation_groups.event_id",
                "reservation_groups.expires_at",
            ],
        ),
        ForeignKeyConstraint(
            ["event_id", "seat_id"], ["event_seats.event_id", "event_seats.seat_id"]
        ),
        UniqueConstraint("group_id", "seat_id", name="uq_ticket_reservations_group_seat"),
        UniqueConstraint(
            "id", "group_id", "event_id", "seat_id", name="uq_ticket_reservations_item_reference"
        ),
        Index(
            "uq_ticket_reservations_active_seat",
            "event_id",
            "seat_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_ticket_reservations_expiry",
            "expires_at",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    group_id: Mapped[UUID]
    user_id: Mapped[UUID] = mapped_column(index=True)
    event_id: Mapped[UUID]
    seat_id: Mapped[UUID]
    expires_at: Mapped[datetime]
    status: Mapped[ReservationStatus] = mapped_column(
        enum_type(ReservationStatus), server_default=ReservationStatus.ACTIVE.value
    )


class Order(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reservation_group_id", "user_id", "event_id"],
            ["reservation_groups.id", "reservation_groups.user_id", "reservation_groups.event_id"],
        ),
        UniqueConstraint("reservation_group_id"),
        UniqueConstraint("id", "reservation_group_id", "event_id", name="uq_orders_item_reference"),
        money_check("total_amount"),
        CheckConstraint("currency = 'KZT'", name="currency_kzt"),
    )

    user_id: Mapped[UUID] = mapped_column(index=True)
    event_id: Mapped[UUID] = mapped_column(index=True)
    reservation_group_id: Mapped[UUID]
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="KZT")
    status: Mapped[OrderStatus] = mapped_column(
        enum_type(OrderStatus), server_default=OrderStatus.PENDING.value
    )


class OrderItem(UUIDPrimaryKey, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_id", "reservation_group_id", "event_id"],
            ["orders.id", "orders.reservation_group_id", "orders.event_id"],
        ),
        ForeignKeyConstraint(
            ["reservation_id", "reservation_group_id", "event_id", "seat_id"],
            [
                "ticket_reservations.id",
                "ticket_reservations.group_id",
                "ticket_reservations.event_id",
                "ticket_reservations.seat_id",
            ],
        ),
        ForeignKeyConstraint(
            ["event_id", "seat_id", "ticket_type_id"],
            ["event_seats.event_id", "event_seats.seat_id", "event_seats.ticket_type_id"],
        ),
        UniqueConstraint("order_id", "seat_id", name="uq_order_items_order_seat"),
        UniqueConstraint("reservation_id"),
        UniqueConstraint(
            "id", "order_id", "event_id", "seat_id", name="uq_order_items_ticket_reference"
        ),
        money_check("unit_price"),
        CheckConstraint("currency = 'KZT'", name="currency_kzt"),
    )

    order_id: Mapped[UUID]
    reservation_id: Mapped[UUID]
    reservation_group_id: Mapped[UUID]
    event_id: Mapped[UUID]
    seat_id: Mapped[UUID]
    ticket_type_id: Mapped[UUID] = mapped_column(index=True)
    ticket_type_name: Mapped[str] = mapped_column(String(100))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="KZT")


class Ticket(UUIDPrimaryKey, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_item_id", "order_id", "event_id", "seat_id"],
            [
                "order_items.id",
                "order_items.order_id",
                "order_items.event_id",
                "order_items.seat_id",
            ],
        ),
        UniqueConstraint("order_item_id"),
        UniqueConstraint("qr_code"),
        Index(
            "uq_tickets_live_seat",
            "event_id",
            "seat_id",
            unique=True,
            postgresql_where=text("status IN ('VALID', 'USED')"),
        ),
    )

    order_id: Mapped[UUID] = mapped_column(index=True)
    order_item_id: Mapped[UUID]
    event_id: Mapped[UUID]
    seat_id: Mapped[UUID]
    qr_code: Mapped[UUID] = mapped_column(server_default=text("gen_random_uuid()"))
    status: Mapped[TicketStatus] = mapped_column(
        enum_type(TicketStatus), server_default=TicketStatus.VALID.value
    )
