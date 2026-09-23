from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine, exists, func, select, update
from sqlalchemy.orm import Session

from ticketflow.auth.dependencies import CurrentUser
from ticketflow.catalog_schemas import Input
from ticketflow.common import audit
from ticketflow.database import SessionDependency
from ticketflow.models import (
    Event,
    EventSeat,
    EventStatus,
    Order,
    OrderStatus,
    ReservationGroup,
    ReservationStatus,
    Ticket,
    TicketReservation,
    TicketStatus,
)

router = APIRouter(prefix="/reservations", tags=["Reservations"])


class ReservationCreate(Input):
    event_id: UUID
    seat_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("seat_ids")
    @classmethod
    def unique_seats(cls, seats):
        if len(set(seats)) != len(seats):
            raise ValueError("Seat IDs must be unique")
        return seats


class ReservationRead(BaseModel):
    id: UUID
    event_id: UUID
    seat_ids: list[UUID]
    expires_at: datetime
    status: ReservationStatus


def lock_event(session: Session, event_id: UUID) -> Event:
    # Shared order across reservation, payment, expiry, scanning and cancellation.
    event = session.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def db_now(session: Session) -> datetime:
    # now() is transaction start time, which may predate a long lock wait.
    return session.scalar(select(func.clock_timestamp()))


def require_on_sale(event: Event, now: datetime) -> None:
    if event.status != EventStatus.PUBLISHED or event.event_date <= now:
        raise HTTPException(409, "Event is not open for booking")


def lock_inventory(session: Session, event_id: UUID, seat_ids: list[UUID]) -> list[EventSeat]:
    return list(
        session.scalars(
            select(EventSeat)
            .where(EventSeat.event_id == event_id, EventSeat.seat_id.in_(seat_ids))
            .order_by(EventSeat.seat_id)
            .with_for_update()
        )
    )


def expire_for_event(session: Session, event: Event, now: datetime) -> int:
    active = [
        TicketReservation.event_id == event.id,
        TicketReservation.status == ReservationStatus.ACTIVE,
    ]
    if event.status == EventStatus.PUBLISHED and event.event_date > now:
        active.append(TicketReservation.expires_at <= now)
    groups = session.scalars(
        update(TicketReservation)
        .where(*active)
        .values(status=ReservationStatus.EXPIRED)
        .returning(TicketReservation.group_id)
    ).all()
    if groups:
        session.execute(
            update(Order)
            .where(Order.reservation_group_id.in_(set(groups)), Order.status == OrderStatus.PENDING)
            .values(status=OrderStatus.CANCELLED)
        )
    return len(groups)


def reservation_read(session: Session, group: ReservationGroup) -> ReservationRead:
    rows = session.scalars(
        select(TicketReservation)
        .where(TicketReservation.group_id == group.id)
        .order_by(TicketReservation.seat_id)
    ).all()
    status = ReservationStatus.ACTIVE
    if not rows or any(row.status == ReservationStatus.EXPIRED for row in rows):
        status = ReservationStatus.EXPIRED
    elif all(row.status == ReservationStatus.COMPLETED for row in rows):
        status = ReservationStatus.COMPLETED
    return ReservationRead(
        id=group.id,
        event_id=group.event_id,
        seat_ids=[row.seat_id for row in rows],
        expires_at=group.expires_at,
        status=status,
    )


@router.post("", response_model=ReservationRead, status_code=201)
def reserve(
    body: ReservationCreate, user: CurrentUser, session: SessionDependency, request: Request
):
    event = lock_event(session, body.event_id)
    inventory = lock_inventory(session, event.id, body.seat_ids)
    now = db_now(session)
    require_on_sale(event, now)
    if len(inventory) != len(body.seat_ids):
        raise HTTPException(404, "Seat not found in this event")
    expire_for_event(session, event, now)
    held = session.scalar(
        select(
            exists().where(
                TicketReservation.event_id == event.id,
                TicketReservation.seat_id.in_(body.seat_ids),
                TicketReservation.status == ReservationStatus.ACTIVE,
            )
        )
    )
    sold = session.scalar(
        select(
            exists().where(
                Ticket.event_id == event.id,
                Ticket.seat_id.in_(body.seat_ids),
                Ticket.status.in_([TicketStatus.VALID, TicketStatus.USED]),
            )
        )
    )
    if held or sold:
        raise HTTPException(409, "One or more seats are unavailable")
    group = ReservationGroup(
        user_id=user.id, event_id=event.id, created_at=now, expires_at=now + timedelta(minutes=10)
    )
    session.add(group)
    session.flush()
    session.add_all(
        [
            TicketReservation(
                group_id=group.id,
                user_id=user.id,
                event_id=event.id,
                seat_id=seat_id,
                expires_at=group.expires_at,
            )
            for seat_id in sorted(body.seat_ids)
        ]
    )
    audit(session, user, request, "CUSTOMER_RESERVED_SEATS", "reservation", group.id)
    session.flush()
    result = reservation_read(session, group)
    session.commit()
    return result


@router.get("/{reservation_id}", response_model=ReservationRead)
def reservation_detail(reservation_id: UUID, user: CurrentUser, session: SessionDependency):
    group = session.scalar(
        select(ReservationGroup).where(
            ReservationGroup.id == reservation_id, ReservationGroup.user_id == user.id
        )
    )
    if group is None:
        raise HTTPException(404, "Reservation not found")
    event = lock_event(session, group.event_id)
    expire_for_event(session, event, db_now(session))
    result = reservation_read(session, group)
    session.commit()
    return result


def expire_reservations(engine: Engine, batch_size: int = 100) -> int:
    with Session(engine) as session:
        event_ids = session.scalars(
            select(Event.id)
            .join(TicketReservation, TicketReservation.event_id == Event.id)
            .where(
                TicketReservation.status == ReservationStatus.ACTIVE,
                (TicketReservation.expires_at <= func.clock_timestamp())
                | (Event.status != EventStatus.PUBLISHED)
                | (Event.event_date <= func.clock_timestamp()),
            )
            .distinct()
            .order_by(Event.id)
            .limit(batch_size)
        ).all()
    expired = 0
    for event_id in event_ids:
        with Session(engine) as session, session.begin():
            event = lock_event(session, event_id)
            expired += expire_for_event(session, event, db_now(session))
    return expired
