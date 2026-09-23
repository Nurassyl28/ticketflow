from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ticketflow.access import OrderRead, get_owned_order
from ticketflow.auth.dependencies import CurrentUser
from ticketflow.commerce_schemas import ItemRead, OrderCreate, PaymentRead, PayRequest, TicketRead
from ticketflow.common import audit, commit
from ticketflow.database import SessionDependency
from ticketflow.models import (
    EventSeat,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    ReservationGroup,
    ReservationStatus,
    Ticket,
    TicketReservation,
    TicketType,
)
from ticketflow.reservations import (
    db_now,
    expire_for_event,
    lock_event,
    lock_inventory,
    require_on_sale,
)

router = APIRouter(tags=["Checkout"])


def group_rows(session: Session, group_id: UUID):
    return list(
        session.scalars(
            select(TicketReservation)
            .where(TicketReservation.group_id == group_id)
            .order_by(TicketReservation.seat_id)
        )
    )


@router.post("/orders", response_model=OrderRead, status_code=201)
def create_order(
    body: OrderCreate,
    user: CurrentUser,
    session: SessionDependency,
    request: Request,
    response: Response,
):
    group = session.scalar(
        select(ReservationGroup).where(
            ReservationGroup.id == body.reservation_id, ReservationGroup.user_id == user.id
        )
    )
    if group is None:
        raise HTTPException(404, "Reservation not found")
    event = lock_event(session, group.event_id)
    existing = session.scalar(select(Order).where(Order.reservation_group_id == group.id))
    now = db_now(session)
    expire_for_event(session, event, now)
    if existing is not None:
        session.commit()
        response.status_code = 200
        return existing
    require_on_sale(event, now)
    rows = group_rows(session, group.id)
    if (
        group.expires_at <= now
        or not rows
        or any(row.status != ReservationStatus.ACTIVE for row in rows)
    ):
        session.commit()
        raise HTTPException(409, "Reservation expired or inactive")
    lock_inventory(session, event.id, [row.seat_id for row in rows])
    prices = dict(
        session.execute(
            select(EventSeat.seat_id, TicketType)
            .join(TicketType, TicketType.id == EventSeat.ticket_type_id)
            .where(
                EventSeat.event_id == event.id, EventSeat.seat_id.in_([row.seat_id for row in rows])
            )
        ).all()
    )
    total = sum((prices[row.seat_id].price for row in rows), Decimal("0.00"))
    if total > Decimal("9999999999.99"):
        raise HTTPException(409, "Order exceeds the supported total")
    order = Order(
        user_id=user.id, event_id=event.id, reservation_group_id=group.id, total_amount=total
    )
    session.add(order)
    session.flush()
    for row in rows:
        tariff = prices[row.seat_id]
        session.add(
            OrderItem(
                order_id=order.id,
                reservation_id=row.id,
                reservation_group_id=group.id,
                event_id=event.id,
                seat_id=row.seat_id,
                ticket_type_id=tariff.id,
                ticket_type_name=tariff.name,
                unit_price=tariff.price,
            )
        )
    audit(session, user, request, "CUSTOMER_CREATED_ORDER", "order", order.id)
    session.commit()
    return order


@router.get("/orders/{order_id}/items", response_model=list[ItemRead])
def order_items(order: Annotated[Order, Depends(get_owned_order)], session: SessionDependency):
    return session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.seat_id)
    ).all()


def payment_read(session: Session, payment: Payment) -> PaymentRead:
    tickets = session.scalars(
        select(Ticket).where(Ticket.order_id == payment.order_id).order_by(Ticket.seat_id)
    ).all()
    return PaymentRead(
        id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        created_at=payment.created_at,
        refunded_at=payment.refunded_at,
        tickets=[TicketRead.model_validate(ticket) for ticket in tickets],
    )


def locked_owned_order(session: Session, user_id: UUID, order_id: UUID):
    event_id = session.scalar(
        select(Order.event_id).where(Order.id == order_id, Order.user_id == user_id)
    )
    if event_id is None:
        raise HTTPException(404, "Order not found")
    event = lock_event(session, event_id)
    order = session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return event, order


@router.post("/payments/mock", response_model=PaymentRead)
def pay(
    body: PayRequest,
    user: CurrentUser,
    session: SessionDependency,
    request: Request,
    response: Response,
):
    event, order = locked_owned_order(session, user.id, body.order_id)
    response.headers["Cache-Control"] = "no-store"
    existing = session.scalar(
        select(Payment).where(
            Payment.user_id == user.id, Payment.idempotency_key == body.idempotency_key
        )
    )
    if existing is not None:
        if existing.order_id != order.id:
            raise HTTPException(409, "Idempotency key belongs to another order")
        return payment_read(session, existing)
    if order.status != OrderStatus.PENDING:
        raise HTTPException(409, "Order is not pending or was paid with another key")
    group = session.get(ReservationGroup, order.reservation_group_id)
    rows = group_rows(session, group.id)
    lock_inventory(session, event.id, [row.seat_id for row in rows])
    now = db_now(session)
    require_on_sale(event, now)
    expire_for_event(session, event, now)
    if (
        group.expires_at <= now
        or not rows
        or any(row.status != ReservationStatus.ACTIVE for row in rows)
    ):
        session.commit()
        raise HTTPException(409, "Reservation expired or inactive")
    items = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    if (
        len(items) != len(rows)
        or sum((item.unit_price for item in items), Decimal("0")) != order.total_amount
    ):
        raise HTTPException(409, "Order items do not match the reservation")
    payment = Payment(
        order_id=order.id,
        user_id=user.id,
        idempotency_key=body.idempotency_key,
        amount=order.total_amount,
        currency=order.currency,
        created_at=now,
    )
    session.add(payment)
    order.status = OrderStatus.PAID
    for row in rows:
        row.status = ReservationStatus.COMPLETED
    for item in items:
        session.add(
            Ticket(
                order_id=order.id, order_item_id=item.id, event_id=event.id, seat_id=item.seat_id
            )
        )
    audit(session, user, request, "CUSTOMER_PURCHASED_TICKET", "order", order.id)
    commit(session)
    return payment_read(session, payment)
