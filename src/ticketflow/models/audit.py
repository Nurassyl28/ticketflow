from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from ticketflow.models.base import Base, UUIDPrimaryKey


class AuditLog(UUIDPrimaryKey, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity", "entity_id", "timestamp"),
        Index("ix_audit_logs_user_time", "user_id", "timestamp"),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[UUID]
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
