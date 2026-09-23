from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ticketflow.catalog_schemas import Input
from ticketflow.models import TicketStatus


class OrderCreate(Input):
    reservation_id: UUID


class PayRequest(Input):
    order_id: UUID
    idempotency_key: UUID


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    seat_id: UUID
    ticket_type_id: UUID
    ticket_type_name: str
    unit_price: Decimal
    currency: str


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    order_id: UUID
    event_id: UUID
    seat_id: UUID
    qr_code: UUID
    status: TicketStatus


class PaymentRead(BaseModel):
    id: UUID
    order_id: UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    refunded_at: datetime | None
    tickets: list[TicketRead]
