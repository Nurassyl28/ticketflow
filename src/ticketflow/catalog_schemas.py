from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ticketflow.models import EventCategory, EventStatus

Name = Annotated[str, Field(min_length=1, max_length=100)]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Patch(Input):
    @model_validator(mode="after")
    def no_nulls(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Explicit null is not allowed")
        return self


class VenueCreate(Input):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=500)
    city: Name
    capacity: int = Field(ge=1, le=100000)


class VenuePatch(Patch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    city: Name | None = None
    capacity: int | None = Field(default=None, ge=1, le=100000)


class SectionCreate(Input):
    name: Name


class SectionRead(SectionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    venue_id: UUID


class SeatCreate(Input):
    row_number: int = Field(ge=1, le=100000)
    seat_number: int = Field(ge=1, le=100000)


class SeatRead(SeatCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    section_id: UUID


class EventCreate(Input):
    venue_id: UUID
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    category: EventCategory
    event_date: AwareDatetime


class EventPatch(Patch):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    category: EventCategory | None = None
    event_date: AwareDatetime | None = None
    status: Literal[EventStatus.PUBLISHED, EventStatus.FINISHED] | None = None


class TariffCreate(Input):
    section_id: UUID
    name: Name
    price: Money


class TariffPatch(Patch):
    name: Name | None = None
    price: Money | None = None


class TariffRead(TariffCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    currency: str


class AssignTariff(Input):
    ticket_type_id: UUID


class EventSeatRead(BaseModel):
    seat_id: UUID
    section_id: UUID
    section_name: str
    row_number: int
    seat_number: int
    ticket_type_id: UUID
    ticket_type_name: str
    price: Decimal
    currency: str
    status: Literal["available", "sold", "reserved"]
