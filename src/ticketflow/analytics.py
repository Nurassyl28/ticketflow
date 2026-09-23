from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, IPvAnyAddress
from sqlalchemy import Date, String, cast, exists, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from ticketflow.access import Limit, Offset, get_managed_event, managed_events
from ticketflow.auth.dependencies import Administrator, Manager
from ticketflow.database import SessionDependency
from ticketflow.models import (
    AuditLog,
    Event,
    EventSeat,
    EventStatus,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    ReservationStatus,
    Ticket,
    TicketReservation,
    TicketStatus,
)

router = APIRouter(tags=["Analytics and audit"])


class TypeSales(BaseModel):
    ticket_type_id: UUID
    name: str
    tickets_sold: int
    revenue: Decimal


class DateSales(BaseModel):
    date: date
    tickets_sold: int
    revenue: Decimal


class Dashboard(BaseModel):
    tickets_sold: int
    tickets_available: int
    total_revenue: Decimal
    sales_today: int
    sales_by_ticket_type: list[TypeSales]
    sales_by_date: list[DateSales]
    currency: str = "KZT"
    timezone: str = "UTC"


class SaleRead(BaseModel):
    ticket_id: UUID
    order_id: UUID
    seat_id: UUID
    ticket_type_name: str
    unit_price: Decimal
    ticket_status: TicketStatus
    order_status: OrderStatus
    paid_at: datetime


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID | None
    action: str
    entity: str
    entity_id: UUID
    timestamp: datetime
    ip: IPvAnyAddress | None


@router.get("/organizer/dashboard", response_model=Dashboard)
def dashboard(user: Manager, session: SessionDependency, event_id: UUID | None = None):
    scope = managed_events(user).with_only_columns(Event.id)
    if event_id is not None:
        scope = scope.where(Event.id == event_id)
        if session.scalar(scope) is None:
            raise HTTPException(404, "Event not found")
    scope = scope.cte("event_scope")
    payment_day = cast(func.timezone("UTC", Payment.created_at), Date)
    sales = (
        select(
            Ticket.id,
            OrderItem.ticket_type_id,
            OrderItem.ticket_type_name,
            OrderItem.unit_price,
            payment_day.label("day"),
        )
        .join(OrderItem, OrderItem.id == Ticket.order_item_id)
        .join(Order, Order.id == Ticket.order_id)
        .join(Payment, Payment.order_id == Order.id)
        .where(
            Ticket.event_id.in_(select(scope.c.id)),
            Ticket.status.in_([TicketStatus.VALID, TicketStatus.USED]),
            Order.status == OrderStatus.PAID,
            Payment.status == "SUCCEEDED",
        )
        .cte("retained_sales")
    )
    by_type = (
        select(
            sales.c.ticket_type_id,
            sales.c.ticket_type_name,
            func.count().label("count"),
            func.sum(sales.c.unit_price).label("revenue"),
        )
        .group_by(sales.c.ticket_type_id, sales.c.ticket_type_name)
        .cte("by_type")
    )
    by_day = (
        select(
            sales.c.day, func.count().label("count"), func.sum(sales.c.unit_price).label("revenue")
        )
        .group_by(sales.c.day)
        .cte("by_day")
    )
    type_json = func.jsonb_build_object(
        "ticket_type_id",
        by_type.c.ticket_type_id,
        "name",
        by_type.c.ticket_type_name,
        "tickets_sold",
        by_type.c.count,
        "revenue",
        cast(by_type.c.revenue, String),
    )
    day_json = func.jsonb_build_object(
        "date",
        by_day.c.day,
        "tickets_sold",
        by_day.c.count,
        "revenue",
        cast(by_day.c.revenue, String),
    )
    sold = exists().where(
        Ticket.event_id == EventSeat.event_id,
        Ticket.seat_id == EventSeat.seat_id,
        Ticket.status.in_([TicketStatus.VALID, TicketStatus.USED]),
    )
    held = exists().where(
        TicketReservation.event_id == EventSeat.event_id,
        TicketReservation.seat_id == EventSeat.seat_id,
        TicketReservation.status == ReservationStatus.ACTIVE,
        TicketReservation.expires_at > func.statement_timestamp(),
    )
    available = (
        select(func.count())
        .select_from(EventSeat)
        .join(Event, Event.id == EventSeat.event_id)
        .where(
            Event.id.in_(select(scope.c.id)),
            Event.status == EventStatus.PUBLISHED,
            Event.event_date > func.statement_timestamp(),
            ~sold,
            ~held,
        )
    )
    today = cast(func.timezone("UTC", func.statement_timestamp()), Date)
    # One SQL statement: all six metrics see the same PostgreSQL snapshot.
    query = select(
        select(func.count()).select_from(sales).scalar_subquery().label("tickets_sold"),
        available.scalar_subquery().label("tickets_available"),
        select(func.coalesce(func.sum(sales.c.unit_price), 0))
        .scalar_subquery()
        .label("total_revenue"),
        select(func.count())
        .select_from(sales)
        .where(sales.c.day == today)
        .scalar_subquery()
        .label("sales_today"),
        select(
            func.coalesce(
                func.jsonb_agg(
                    aggregate_order_by(
                        type_json, by_type.c.ticket_type_name, by_type.c.ticket_type_id
                    )
                ),
                func.jsonb_build_array(),
            )
        )
        .scalar_subquery()
        .label("sales_by_ticket_type"),
        select(
            func.coalesce(
                func.jsonb_agg(aggregate_order_by(day_json, by_day.c.day)), func.jsonb_build_array()
            )
        )
        .scalar_subquery()
        .label("sales_by_date"),
    )
    return session.execute(query).mappings().one()


@router.get("/organizer/events/{event_id}/sales", response_model=list[SaleRead])
def event_sales(
    event: Annotated[Event, Depends(get_managed_event)],
    session: SessionDependency,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return (
        session.execute(
            select(
                Ticket.id.label("ticket_id"),
                Ticket.order_id,
                Ticket.seat_id,
                OrderItem.ticket_type_name,
                OrderItem.unit_price,
                Ticket.status.label("ticket_status"),
                Order.status.label("order_status"),
                Payment.created_at.label("paid_at"),
            )
            .join(OrderItem, OrderItem.id == Ticket.order_item_id)
            .join(Order, Order.id == Ticket.order_id)
            .join(Payment, Payment.order_id == Order.id)
            .where(Ticket.event_id == event.id)
            .order_by(Payment.created_at.desc(), Ticket.id)
            .limit(limit)
            .offset(offset)
        )
        .mappings()
        .all()
    )


@router.get("/admin/audit-logs", response_model=list[AuditRead])
def audit_logs(
    user: Administrator,
    session: SessionDependency,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity: Annotated[str | None, Query(max_length=80)] = None,
    entity_id: UUID | None = None,
    user_id: UUID | None = None,
    date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Filter range is reversed")
    query = select(AuditLog)
    for column, value in (
        (AuditLog.action, action),
        (AuditLog.entity, entity),
        (AuditLog.entity_id, entity_id),
        (AuditLog.user_id, user_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if date_from:
        query = query.where(AuditLog.timestamp >= date_from)
    if date_to:
        query = query.where(AuditLog.timestamp <= date_to)
    return session.scalars(
        query.order_by(AuditLog.timestamp.desc(), AuditLog.id).limit(limit).offset(offset)
    ).all()
