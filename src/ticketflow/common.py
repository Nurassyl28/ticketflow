from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticketflow.auth.router import client_ip
from ticketflow.models import AuditLog, User


def audit(
    session: Session, user: User, request: Request, action: str, entity: str, entity_id: UUID
):
    session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            ip=client_ip(request),
        )
    )


def commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Resource conflicts with existing data") from None
