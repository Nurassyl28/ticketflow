from enum import StrEnum


class UserRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    ORGANIZER = "ORGANIZER"
    ADMIN = "ADMIN"


class EventCategory(StrEnum):
    CONCERT = "concert"
    CINEMA = "cinema"
    CONFERENCE = "conference"
    FOOTBALL = "football"
    STAND_UP = "stand_up"
    OTHER = "other"


class EventStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    FINISHED = "FINISHED"


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class TicketStatus(StrEnum):
    VALID = "VALID"
    USED = "USED"
    CANCELLED = "CANCELLED"
