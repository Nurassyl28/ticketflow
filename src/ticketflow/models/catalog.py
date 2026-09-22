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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ticketflow.models.base import Base, CreatedAt, UUIDPrimaryKey, enum_type, money_check
from ticketflow.models.enums import EventCategory, EventStatus, UserRole


class User(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = btrim(email) AND email <> ''", name="email_trimmed"),
    )

    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        enum_type(UserRole), server_default=UserRole.CUSTOMER.value
    )


Index("uq_users_email_lower", func.lower(User.email), unique=True)


class Venue(UUIDPrimaryKey, Base):
    __tablename__ = "venues"
    __table_args__ = (
        UniqueConstraint("id", "organizer_id", name="uq_venues_id_organizer"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
    )

    organizer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    city: Mapped[str] = mapped_column(String(100), index=True)
    capacity: Mapped[int]


class Section(UUIDPrimaryKey, Base):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("venue_id", "name", name="uq_sections_venue_name"),
        UniqueConstraint("id", "venue_id", name="uq_sections_id_venue"),
    )

    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(100))


class Seat(UUIDPrimaryKey, Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("section_id", "row_number", "seat_number", name="uq_seats_position"),
        UniqueConstraint("id", "section_id", name="uq_seats_id_section"),
        CheckConstraint("row_number > 0", name="row_positive"),
        CheckConstraint("seat_number > 0", name="number_positive"),
    )

    section_id: Mapped[UUID] = mapped_column(ForeignKey("sections.id"))
    row_number: Mapped[int]
    seat_number: Mapped[int]


class Event(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "events"
    __table_args__ = (
        ForeignKeyConstraint(["venue_id", "organizer_id"], ["venues.id", "venues.organizer_id"]),
        UniqueConstraint("id", "venue_id", name="uq_events_id_venue"),
        Index("ix_events_status_date", "status", "event_date"),
        Index("ix_events_category_date", "category", "event_date"),
    )

    organizer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    venue_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="")
    category: Mapped[EventCategory] = mapped_column(enum_type(EventCategory))
    event_date: Mapped[datetime]
    status: Mapped[EventStatus] = mapped_column(
        enum_type(EventStatus), server_default=EventStatus.DRAFT.value
    )


class TicketType(UUIDPrimaryKey, Base):
    __tablename__ = "ticket_types"
    __table_args__ = (
        ForeignKeyConstraint(["event_id", "venue_id"], ["events.id", "events.venue_id"]),
        ForeignKeyConstraint(["section_id", "venue_id"], ["sections.id", "sections.venue_id"]),
        UniqueConstraint("id", "event_id", "section_id", name="uq_ticket_types_id_event_section"),
        UniqueConstraint("event_id", "name", name="uq_ticket_types_event_name"),
        money_check("price"),
        CheckConstraint("currency = 'KZT'", name="currency_kzt"),
    )

    event_id: Mapped[UUID]
    venue_id: Mapped[UUID]
    section_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="KZT")


class EventSeat(Base):
    __tablename__ = "event_seats"
    __table_args__ = (
        ForeignKeyConstraint(["seat_id", "section_id"], ["seats.id", "seats.section_id"]),
        ForeignKeyConstraint(
            ["ticket_type_id", "event_id", "section_id"],
            ["ticket_types.id", "ticket_types.event_id", "ticket_types.section_id"],
        ),
        UniqueConstraint("event_id", "seat_id", "ticket_type_id", name="uq_event_seats_tariff"),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    seat_id: Mapped[UUID] = mapped_column(primary_key=True, index=True)
    section_id: Mapped[UUID]
    ticket_type_id: Mapped[UUID] = mapped_column(index=True)
