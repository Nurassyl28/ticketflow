from io import BytesIO
from uuid import UUID

import segno
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ticketflow.access import Limit, Offset
from ticketflow.auth.dependencies import CurrentUser, Manager
from ticketflow.catalog_schemas import Input
from ticketflow.commerce_schemas import TicketRead
from ticketflow.common import audit
from ticketflow.database import SessionDependency
from ticketflow.models import EventStatus, Order, OrderStatus, Ticket, TicketStatus, UserRole
from ticketflow.reservations import lock_event

router = APIRouter(prefix="/tickets", tags=["Tickets"])


class ValidateTicket(Input):
    event_id: UUID
    qr_code: UUID


def owned_ticket(session: Session, user_id: UUID, ticket_id: UUID) -> Ticket:
    ticket = session.scalar(
        select(Ticket)
        .join(Order, Order.id == Ticket.order_id)
        .where(Ticket.id == ticket_id, Order.user_id == user_id)
    )
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.get("/me", response_model=list[TicketRead])
def my_tickets(
    user: CurrentUser,
    session: SessionDependency,
    response: Response,
    limit: Limit = 50,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    return session.scalars(
        select(Ticket)
        .join(Order, Order.id == Ticket.order_id)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc(), Ticket.id)
        .limit(limit)
        .offset(offset)
    ).all()


@router.post("/validate", response_model=TicketRead)
def validate_ticket(
    body: ValidateTicket,
    user: Manager,
    session: SessionDependency,
    request: Request,
    response: Response,
):
    event = lock_event(session, body.event_id)
    if user.role != UserRole.ADMIN and event.organizer_id != user.id:
        raise HTTPException(404, "Event not found")
    if event.status != EventStatus.PUBLISHED:
        raise HTTPException(403, "Event is not open for admission")
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.qr_code == body.qr_code, Ticket.event_id == event.id)
        .with_for_update()
    )
    if ticket is None:
        raise HTTPException(404, "Ticket not found for this event")
    if ticket.status == TicketStatus.USED:
        raise HTTPException(403, "Ticket already used")
    order = session.get(Order, ticket.order_id)
    if ticket.status != TicketStatus.VALID or order.status != OrderStatus.PAID:
        raise HTTPException(403, "Ticket is not valid")
    ticket.status = TicketStatus.USED
    audit(session, user, request, "ORGANIZER_VALIDATED_TICKET", "ticket", ticket.id)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return ticket


@router.get("/{ticket_id}", response_model=TicketRead)
def ticket_detail(
    ticket_id: UUID, user: CurrentUser, session: SessionDependency, response: Response
):
    response.headers["Cache-Control"] = "no-store"
    return owned_ticket(session, user.id, ticket_id)


@router.get(
    "/{ticket_id}/qr", responses={200: {"content": {"image/svg+xml": {}}}}, response_class=Response
)
def ticket_qr(ticket_id: UUID, user: CurrentUser, session: SessionDependency):
    ticket = owned_ticket(session, user.id, ticket_id)
    if ticket.status != TicketStatus.VALID:
        raise HTTPException(409, "Only valid tickets have an admission QR")
    buffer = BytesIO()
    segno.make_qr(str(ticket.qr_code), error="m").save(
        buffer, kind="svg", scale=6, border=4, light="white"
    )
    return Response(
        content=buffer.getvalue(),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="ticket-{ticket.id}.svg"',
        },
    )
