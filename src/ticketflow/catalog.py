from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from pydantic import AwareDatetime
from sqlalchemy import case, delete, exists, func, select
from sqlalchemy.orm import Session

from ticketflow.access import EventRead, Limit, Offset, VenueRead, managed_events, managed_venues
from ticketflow.auth.dependencies import Manager
from ticketflow.catalog_schemas import (
    AssignTariff,
    EventCreate,
    EventPatch,
    EventSeatRead,
    Money,
    Name,
    SeatCreate,
    SeatRead,
    SectionCreate,
    SectionRead,
    TariffCreate,
    TariffPatch,
    TariffRead,
    VenueCreate,
    VenuePatch,
)
from ticketflow.common import audit, commit
from ticketflow.database import SessionDependency
from ticketflow.models import (
    Event,
    EventCategory,
    EventSeat,
    EventStatus,
    ReservationGroup,
    ReservationStatus,
    Seat,
    Section,
    Ticket,
    TicketReservation,
    TicketStatus,
    TicketType,
    User,
    Venue,
)

router = APIRouter(tags=["Catalog"])


def lock_venue(session: Session, user: User, venue_id: UUID) -> Venue:
    venue = session.scalar(managed_venues(user).where(Venue.id == venue_id).with_for_update())
    if venue is None:
        raise HTTPException(404, "Venue not found")
    return venue


def lock_managed_event(session: Session, user: User, event_id: UUID) -> Event:
    venue_id = session.scalar(
        managed_events(user).where(Event.id == event_id).with_only_columns(Event.venue_id)
    )
    if venue_id is None:
        raise HTTPException(404, "Event not found")
    # Catalog writes always lock venue before event; checkout only locks event.
    lock_venue(session, user, venue_id)
    event = session.scalar(managed_events(user).where(Event.id == event_id).with_for_update())
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def editable_venue(session: Session, venue: Venue) -> None:
    if session.scalar(
        select(exists().where(Event.venue_id == venue.id, Event.status != EventStatus.DRAFT))
    ):
        raise HTTPException(409, "Venue layout is frozen after event publication")


def draft(event: Event) -> None:
    if event.status != EventStatus.DRAFT:
        raise HTTPException(409, "Only a draft can change its layout, date or prices")


def public_event(session: Session, event_id: UUID) -> Event:
    event = session.scalar(
        select(Event).where(
            Event.id == event_id,
            Event.status == EventStatus.PUBLISHED,
            Event.event_date > func.clock_timestamp(),
        )
    )
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def section_in_venue(session: Session, venue_id: UUID, section_id: UUID) -> Section:
    section = session.scalar(
        select(Section).where(Section.id == section_id, Section.venue_id == venue_id)
    )
    if section is None:
        raise HTTPException(404, "Section not found")
    return section


@router.post("/venues", response_model=VenueRead, status_code=201)
def create_venue(body: VenueCreate, user: Manager, session: SessionDependency, request: Request):
    venue = Venue(**body.model_dump(), organizer_id=user.id)
    session.add(venue)
    session.flush()
    audit(session, user, request, "ORGANIZER_CREATED_VENUE", "venue", venue.id)
    commit(session)
    return venue


@router.patch("/venues/{venue_id}", response_model=VenueRead)
def update_venue(
    venue_id: UUID, body: VenuePatch, user: Manager, session: SessionDependency, request: Request
):
    venue = lock_venue(session, user, venue_id)
    editable_venue(session, venue)
    count = session.scalar(
        select(func.count()).select_from(Seat).join(Section).where(Section.venue_id == venue_id)
    )
    if body.capacity is not None and body.capacity < count:
        raise HTTPException(409, "Capacity cannot be below the number of seats")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(venue, key, value)
    audit(session, user, request, "ORGANIZER_UPDATED_VENUE", "venue", venue.id)
    commit(session)
    return venue


@router.delete("/venues/{venue_id}", status_code=204)
def delete_venue(venue_id: UUID, user: Manager, session: SessionDependency, request: Request):
    venue = lock_venue(session, user, venue_id)
    if session.scalar(select(exists().where(Event.venue_id == venue_id))):
        raise HTTPException(409, "Delete draft events first")
    sections = select(Section.id).where(Section.venue_id == venue_id)
    session.execute(delete(Seat).where(Seat.section_id.in_(sections)))
    session.execute(delete(Section).where(Section.venue_id == venue_id))
    session.delete(venue)
    audit(session, user, request, "ORGANIZER_DELETED_VENUE", "venue", venue.id)
    commit(session)
    return Response(status_code=204)


@router.get("/venues/{venue_id}/sections", response_model=list[SectionRead])
def list_sections(venue_id: UUID, user: Manager, session: SessionDependency):
    lock_venue(session, user, venue_id)
    return session.scalars(
        select(Section).where(Section.venue_id == venue_id).order_by(Section.name)
    ).all()


@router.post("/venues/{venue_id}/sections", response_model=SectionRead, status_code=201)
def create_section(
    venue_id: UUID, body: SectionCreate, user: Manager, session: SessionDependency, request: Request
):
    venue = lock_venue(session, user, venue_id)
    editable_venue(session, venue)
    section = Section(venue_id=venue_id, **body.model_dump())
    session.add(section)
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return section


@router.patch("/venues/{venue_id}/sections/{section_id}", response_model=SectionRead)
def update_section(
    venue_id: UUID,
    section_id: UUID,
    body: SectionCreate,
    user: Manager,
    session: SessionDependency,
    request: Request,
):
    editable_venue(session, lock_venue(session, user, venue_id))
    section = section_in_venue(session, venue_id, section_id)
    section.name = body.name
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return section


@router.delete("/venues/{venue_id}/sections/{section_id}", status_code=204)
def delete_section(
    venue_id: UUID, section_id: UUID, user: Manager, session: SessionDependency, request: Request
):
    editable_venue(session, lock_venue(session, user, venue_id))
    section = section_in_venue(session, venue_id, section_id)
    if session.scalar(select(exists().where(TicketType.section_id == section_id))):
        raise HTTPException(409, "Delete section tariffs first")
    session.execute(delete(Seat).where(Seat.section_id == section_id))
    session.delete(section)
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return Response(status_code=204)


@router.get("/venues/{venue_id}/sections/{section_id}/seats", response_model=list[SeatRead])
def list_seats(
    venue_id: UUID,
    section_id: UUID,
    user: Manager,
    session: SessionDependency,
    limit: Limit = 50,
    offset: Offset = 0,
):
    lock_venue(session, user, venue_id)
    section_in_venue(session, venue_id, section_id)
    return session.scalars(
        select(Seat)
        .where(Seat.section_id == section_id)
        .order_by(Seat.row_number, Seat.seat_number)
        .limit(limit)
        .offset(offset)
    ).all()


@router.post(
    "/venues/{venue_id}/sections/{section_id}/seats", response_model=list[SeatRead], status_code=201
)
def create_seats(
    venue_id: UUID,
    section_id: UUID,
    body: Annotated[list[SeatCreate], Body(min_length=1, max_length=500)],
    user: Manager,
    session: SessionDependency,
    request: Request,
):
    venue = lock_venue(session, user, venue_id)
    editable_venue(session, venue)
    section_in_venue(session, venue_id, section_id)
    count = session.scalar(
        select(func.count()).select_from(Seat).join(Section).where(Section.venue_id == venue_id)
    )
    if count + len(body) > venue.capacity:
        raise HTTPException(409, "Venue capacity exceeded")
    seats = [Seat(section_id=section_id, **item.model_dump()) for item in body]
    session.add_all(seats)
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return seats


@router.patch("/venues/{venue_id}/seats/{seat_id}", response_model=SeatRead)
def update_seat(
    venue_id: UUID,
    seat_id: UUID,
    body: SeatCreate,
    user: Manager,
    session: SessionDependency,
    request: Request,
):
    editable_venue(session, lock_venue(session, user, venue_id))
    seat = session.scalar(
        select(Seat).join(Section).where(Seat.id == seat_id, Section.venue_id == venue_id)
    )
    if seat is None:
        raise HTTPException(404, "Seat not found")
    seat.row_number, seat.seat_number = body.row_number, body.seat_number
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return seat


@router.delete("/venues/{venue_id}/seats/{seat_id}", status_code=204)
def delete_seat(
    venue_id: UUID, seat_id: UUID, user: Manager, session: SessionDependency, request: Request
):
    editable_venue(session, lock_venue(session, user, venue_id))
    seat = session.scalar(
        select(Seat).join(Section).where(Seat.id == seat_id, Section.venue_id == venue_id)
    )
    if seat is None:
        raise HTTPException(404, "Seat not found")
    session.execute(delete(EventSeat).where(EventSeat.seat_id == seat_id))
    session.delete(seat)
    audit(session, user, request, "ORGANIZER_UPDATED_LAYOUT", "venue", venue_id)
    commit(session)
    return Response(status_code=204)


@router.post("/events", response_model=EventRead, status_code=201)
def create_event(body: EventCreate, user: Manager, session: SessionDependency, request: Request):
    venue = lock_venue(session, user, body.venue_id)
    if body.event_date <= session.scalar(select(func.clock_timestamp())):
        raise HTTPException(422, "Event date must be in the future")
    event = Event(**body.model_dump(), organizer_id=venue.organizer_id)
    session.add(event)
    session.flush()
    audit(session, user, request, "ORGANIZER_CREATED_EVENT", "event", event.id)
    commit(session)
    return event


def publish(session: Session, event: Event) -> None:
    draft(event)
    seats = session.scalars(
        select(Seat).join(Section).where(Section.venue_id == event.venue_id)
    ).all()
    if not seats or event.event_date <= session.scalar(select(func.clock_timestamp())):
        raise HTTPException(409, "Publication requires seats and a future date")
    existing = set(session.scalars(select(EventSeat.seat_id).where(EventSeat.event_id == event.id)))
    tariffs = session.scalars(
        select(TicketType).where(TicketType.event_id == event.id).order_by(TicketType.id)
    ).all()
    for seat in seats:
        if seat.id in existing:
            continue
        options = [tariff for tariff in tariffs if tariff.section_id == seat.section_id]
        if len(options) != 1:
            raise HTTPException(409, "Assign a tariff to every seat before publication")
        session.add(
            EventSeat(
                event_id=event.id,
                seat_id=seat.id,
                section_id=seat.section_id,
                ticket_type_id=options[0].id,
            )
        )
    event.status = EventStatus.PUBLISHED


@router.patch("/events/{event_id}", response_model=EventRead)
def update_event(
    event_id: UUID, body: EventPatch, user: Manager, session: SessionDependency, request: Request
):
    event = lock_managed_event(session, user, event_id)
    changes = body.model_dump(exclude_unset=True)
    status = changes.pop("status", None)
    if event.status in (EventStatus.CANCELLED, EventStatus.FINISHED):
        raise HTTPException(409, "Event is closed")
    if "event_date" in changes or "category" in changes:
        draft(event)
    if body.event_date is not None and body.event_date <= session.scalar(
        select(func.clock_timestamp())
    ):
        raise HTTPException(422, "Event date must be in the future")
    for key, value in changes.items():
        setattr(event, key, value)
    if status == EventStatus.PUBLISHED:
        publish(session, event)
    elif status == EventStatus.FINISHED:
        if event.status != EventStatus.PUBLISHED or event.event_date > session.scalar(
            select(func.clock_timestamp())
        ):
            raise HTTPException(409, "Only a started published event can finish")
        event.status = status
    audit(session, user, request, "ORGANIZER_UPDATED_EVENT", "event", event.id)
    commit(session)
    return event


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: UUID, user: Manager, session: SessionDependency, request: Request):
    event = lock_managed_event(session, user, event_id)
    draft(event)
    if session.scalar(select(exists().where(ReservationGroup.event_id == event_id))):
        raise HTTPException(409, "Event has booking history")
    session.execute(delete(EventSeat).where(EventSeat.event_id == event_id))
    session.execute(delete(TicketType).where(TicketType.event_id == event_id))
    session.delete(event)
    audit(session, user, request, "ORGANIZER_DELETED_EVENT", "event", event_id)
    commit(session)
    return Response(status_code=204)


@router.post("/events/{event_id}/ticket-types", response_model=TariffRead, status_code=201)
def create_tariff(
    event_id: UUID, body: TariffCreate, user: Manager, session: SessionDependency, request: Request
):
    event = lock_managed_event(session, user, event_id)
    draft(event)
    section_in_venue(session, event.venue_id, body.section_id)
    if session.scalar(
        select(exists().where(TicketType.event_id == event_id, TicketType.name == body.name))
    ):
        raise HTTPException(409, "Tariff name already exists")
    tariff = TicketType(event_id=event_id, venue_id=event.venue_id, **body.model_dump())
    session.add(tariff)
    session.flush()
    assigned = select(EventSeat.seat_id).where(EventSeat.event_id == event_id)
    for seat in session.scalars(
        select(Seat).where(Seat.section_id == body.section_id, ~Seat.id.in_(assigned))
    ):
        session.add(
            EventSeat(
                event_id=event_id,
                seat_id=seat.id,
                section_id=seat.section_id,
                ticket_type_id=tariff.id,
            )
        )
    audit(session, user, request, "ORGANIZER_UPDATED_TARIFF", "event", event_id)
    commit(session)
    return tariff


@router.get("/organizer/events/{event_id}/ticket-types", response_model=list[TariffRead])
def list_tariffs(event_id: UUID, user: Manager, session: SessionDependency):
    lock_managed_event(session, user, event_id)
    return session.scalars(
        select(TicketType).where(TicketType.event_id == event_id).order_by(TicketType.name)
    ).all()


@router.patch("/events/{event_id}/ticket-types/{tariff_id}", response_model=TariffRead)
def update_tariff(
    event_id: UUID,
    tariff_id: UUID,
    body: TariffPatch,
    user: Manager,
    session: SessionDependency,
    request: Request,
):
    draft(lock_managed_event(session, user, event_id))
    tariff = session.scalar(
        select(TicketType).where(TicketType.id == tariff_id, TicketType.event_id == event_id)
    )
    if tariff is None:
        raise HTTPException(404, "Tariff not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tariff, key, value)
    audit(session, user, request, "ORGANIZER_UPDATED_TARIFF", "event", event_id)
    commit(session)
    return tariff


@router.delete("/events/{event_id}/ticket-types/{tariff_id}", status_code=204)
def delete_tariff(
    event_id: UUID, tariff_id: UUID, user: Manager, session: SessionDependency, request: Request
):
    draft(lock_managed_event(session, user, event_id))
    tariff = session.scalar(
        select(TicketType).where(TicketType.id == tariff_id, TicketType.event_id == event_id)
    )
    if tariff is None:
        raise HTTPException(404, "Tariff not found")
    session.execute(delete(EventSeat).where(EventSeat.ticket_type_id == tariff_id))
    session.delete(tariff)
    audit(session, user, request, "ORGANIZER_UPDATED_TARIFF", "event", event_id)
    commit(session)
    return Response(status_code=204)


@router.put("/events/{event_id}/seats/{seat_id}/tariff", status_code=204)
def assign_tariff(
    event_id: UUID,
    seat_id: UUID,
    body: AssignTariff,
    user: Manager,
    session: SessionDependency,
    request: Request,
):
    event = lock_managed_event(session, user, event_id)
    draft(event)
    seat = session.scalar(
        select(Seat).join(Section).where(Seat.id == seat_id, Section.venue_id == event.venue_id)
    )
    tariff = session.scalar(
        select(TicketType).where(
            TicketType.id == body.ticket_type_id, TicketType.event_id == event_id
        )
    )
    if seat is None or tariff is None or seat.section_id != tariff.section_id:
        raise HTTPException(404, "Seat or matching tariff not found")
    inventory = session.get(EventSeat, (event_id, seat_id))
    if inventory:
        inventory.ticket_type_id = tariff.id
    else:
        session.add(
            EventSeat(
                event_id=event_id,
                seat_id=seat_id,
                section_id=seat.section_id,
                ticket_type_id=tariff.id,
            )
        )
    audit(session, user, request, "ORGANIZER_UPDATED_TARIFF", "event", event_id)
    commit(session)
    return Response(status_code=204)


@router.get("/events", response_model=list[EventRead])
def events(
    session: SessionDependency,
    city: Name | None = None,
    category: EventCategory | None = None,
    date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
    min_price: Money | None = None,
    max_price: Money | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    if (date_from and date_to and date_from > date_to) or (
        min_price is not None and max_price is not None and min_price > max_price
    ):
        raise HTTPException(422, "Filter range is reversed")
    query = (
        select(Event)
        .join(Venue)
        .where(Event.status == EventStatus.PUBLISHED, Event.event_date > func.clock_timestamp())
    )
    if city:
        query = query.where(func.lower(Venue.city) == city.lower())
    if category:
        query = query.where(Event.category == category)
    if date_from:
        query = query.where(Event.event_date >= date_from)
    if date_to:
        query = query.where(Event.event_date <= date_to)
    if q:
        query = query.where(Event.title.icontains(q, autoescape=True))
    if min_price is not None or max_price is not None:
        priced = select(EventSeat.event_id).join(
            TicketType, EventSeat.ticket_type_id == TicketType.id
        )
        if min_price is not None:
            priced = priced.where(TicketType.price >= min_price)
        if max_price is not None:
            priced = priced.where(TicketType.price <= max_price)
        query = query.where(Event.id.in_(priced))
    return session.scalars(
        query.order_by(Event.event_date, Event.id).limit(limit).offset(offset)
    ).all()


@router.get("/events/{event_id}", response_model=EventRead)
def event_detail(event_id: UUID, session: SessionDependency):
    return public_event(session, event_id)


@router.get("/events/{event_id}/seats", response_model=list[EventSeatRead])
def event_seats(
    event_id: UUID,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Offset = 0,
):
    public_event(session, event_id)
    sold = exists().where(
        Ticket.event_id == EventSeat.event_id,
        Ticket.seat_id == EventSeat.seat_id,
        Ticket.status.in_([TicketStatus.VALID, TicketStatus.USED]),
    )
    reserved = exists().where(
        TicketReservation.event_id == EventSeat.event_id,
        TicketReservation.seat_id == EventSeat.seat_id,
        TicketReservation.status == ReservationStatus.ACTIVE,
        TicketReservation.expires_at > func.clock_timestamp(),
    )
    query = (
        select(
            EventSeat.seat_id,
            EventSeat.section_id,
            Section.name.label("section_name"),
            Seat.row_number,
            Seat.seat_number,
            TicketType.id.label("ticket_type_id"),
            TicketType.name.label("ticket_type_name"),
            TicketType.price,
            TicketType.currency,
            case((sold, "sold"), (reserved, "reserved"), else_="available").label("status"),
        )
        .select_from(EventSeat)
        .join(Seat, Seat.id == EventSeat.seat_id)
        .join(Section, Section.id == EventSeat.section_id)
        .join(TicketType, TicketType.id == EventSeat.ticket_type_id)
        .where(EventSeat.event_id == event_id)
        .order_by(Section.name, Seat.row_number, Seat.seat_number)
        .limit(limit)
        .offset(offset)
    )
    return session.execute(query).mappings().all()
