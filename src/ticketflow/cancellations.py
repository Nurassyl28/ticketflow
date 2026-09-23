from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ticketflow.access import EventRead, OrderRead
from ticketflow.auth.dependencies import Administrator, CurrentUser
from ticketflow.checkout import locked_owned_order
from ticketflow.common import audit
from ticketflow.database import SessionDependency
from ticketflow.models import (
    EventStatus,
    Order,
    OrderStatus,
    Payment,
    ReservationStatus,
    Ticket,
    TicketReservation,
    TicketStatus,
)
from ticketflow.reservations import db_now, expire_for_event, lock_event

router = APIRouter(tags=["Cancellations"])


def refund_or_cancel(session: Session, order: Order, now: datetime, force: bool = False) -> bool:
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        return False
    tickets = session.scalars(
        select(Ticket).where(Ticket.order_id == order.id).order_by(Ticket.id).with_for_update()
    ).all()
    if not force and any(ticket.status == TicketStatus.USED for ticket in tickets):
        raise HTTPException(409, "An order with used tickets cannot be cancelled")
    if order.status == OrderStatus.PAID:
        payment = session.scalar(
            select(Payment).where(Payment.order_id == order.id).with_for_update()
        )
        if payment is None or payment.status != "SUCCEEDED":
            raise HTTPException(409, "Order payment is inconsistent")
        payment.status = "REFUNDED"
        payment.refunded_at = now
        order.status = OrderStatus.REFUNDED
    else:
        order.status = OrderStatus.CANCELLED
    for ticket in tickets:
        ticket.status = TicketStatus.CANCELLED
    session.execute(
        update(TicketReservation)
        .where(
            TicketReservation.group_id == order.reservation_group_id,
            TicketReservation.status == ReservationStatus.ACTIVE,
        )
        .values(status=ReservationStatus.EXPIRED)
    )
    return True


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_order(order_id: UUID, user: CurrentUser, session: SessionDependency, request: Request):
    event, order = locked_owned_order(session, user.id, order_id)
    now = db_now(session)
    expire_for_event(session, event, now)
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        session.commit()
        return order
    if event.event_date <= now:
        raise HTTPException(409, "Cancellation is only allowed before the event starts")
    if refund_or_cancel(session, order, now):
        audit(session, user, request, "CUSTOMER_CANCELLED_ORDER", "order", order.id)
    session.commit()
    return order


@router.post("/events/{event_id}/cancel", response_model=EventRead)
def cancel_event(event_id: UUID, user: Administrator, session: SessionDependency, request: Request):
    event = lock_event(session, event_id)
    if event.status == EventStatus.CANCELLED:
        return event
    if event.status == EventStatus.FINISHED:
        raise HTTPException(409, "A finished event cannot be cancelled")
    now = db_now(session)
    orders = session.scalars(
        select(Order).where(Order.event_id == event.id).order_by(Order.id).with_for_update()
    ).all()
    for order in orders:
        if refund_or_cancel(session, order, now, force=True):
            audit(session, user, request, "ADMIN_CANCELLED_ORDER", "order", order.id)
    event.status = EventStatus.CANCELLED
    expire_for_event(session, event, now)
    audit(session, user, request, "ADMIN_CANCELLED_EVENT", "event", event.id)
    session.commit()
    return event
