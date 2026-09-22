from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select

from ticketflow.auth.dependencies import CurrentUser, Manager
from ticketflow.database import SessionDependency
from ticketflow.models import (
    Event,
    EventCategory,
    EventStatus,
    Order,
    OrderStatus,
    User,
    UserRole,
    Venue,
)

orders_router = APIRouter(prefix="/orders", tags=["Orders"])
organizer_router = APIRouter(prefix="/organizer", tags=["Organizer"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    total_amount: Decimal
    currency: str
    status: OrderStatus
    created_at: datetime


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organizer_id: UUID
    venue_id: UUID
    title: str
    description: str
    category: EventCategory
    event_date: datetime
    status: EventStatus


class VenueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organizer_id: UUID
    name: str
    address: str
    city: str
    capacity: int


def owned_orders(user: User) -> Select:
    return select(Order).where(Order.user_id == user.id)


def managed_events(user: User) -> Select:
    query = select(Event)
    return query if user.role == UserRole.ADMIN else query.where(Event.organizer_id == user.id)


def managed_venues(user: User) -> Select:
    query = select(Venue)
    return query if user.role == UserRole.ADMIN else query.where(Venue.organizer_id == user.id)


def get_owned_order(order_id: UUID, user: CurrentUser, session: SessionDependency) -> Order:
    order = session.scalar(owned_orders(user).where(Order.id == order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def get_managed_event(event_id: UUID, user: Manager, session: SessionDependency) -> Event:
    event = session.scalar(managed_events(user).where(Event.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def get_managed_venue(venue_id: UUID, user: Manager, session: SessionDependency) -> Venue:
    venue = session.scalar(managed_venues(user).where(Venue.id == venue_id))
    if venue is None:
        raise HTTPException(status_code=404, detail="Venue not found")
    return venue


@orders_router.get("/me", response_model=list[OrderRead])
def my_orders(user: CurrentUser, session: SessionDependency, limit: Limit = 50, offset: Offset = 0):
    return session.scalars(
        owned_orders(user).order_by(Order.created_at.desc(), Order.id).limit(limit).offset(offset)
    ).all()


@orders_router.get("/{order_id}", response_model=OrderRead)
def order_detail(order: Annotated[Order, Depends(get_owned_order)]) -> Order:
    return order


@organizer_router.get("/events", response_model=list[EventRead])
def my_events(user: Manager, session: SessionDependency, limit: Limit = 50, offset: Offset = 0):
    return session.scalars(
        managed_events(user).order_by(Event.event_date, Event.id).limit(limit).offset(offset)
    ).all()


@organizer_router.get("/events/{event_id}", response_model=EventRead)
def event_detail(event: Annotated[Event, Depends(get_managed_event)]) -> Event:
    return event


@organizer_router.get("/venues", response_model=list[VenueRead])
def my_venues(user: Manager, session: SessionDependency, limit: Limit = 50, offset: Offset = 0):
    return session.scalars(
        managed_venues(user).order_by(Venue.name, Venue.id).limit(limit).offset(offset)
    ).all()


@organizer_router.get("/venues/{venue_id}", response_model=VenueRead)
def venue_detail(venue: Annotated[Venue, Depends(get_managed_venue)]) -> Venue:
    return venue
