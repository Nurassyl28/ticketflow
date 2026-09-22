"""Local administrative commands; never exposed over HTTP."""

import argparse

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ticketflow.config import Settings
from ticketflow.database import build_engine
from ticketflow.models import AuditLog, User, UserRole


def promote_admin(session: Session, email: str) -> User:
    email = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    user = session.scalar(select(User).where(func.lower(User.email) == email).with_for_update())
    if user is None:
        raise ValueError("Сначала зарегистрируй пользователя через /auth/register.")
    if user.password_hash.startswith("!"):
        raise ValueError(
            "Учётная запись отключена; demo-пользователей нельзя активировать этой командой."
        )
    if user.role != UserRole.ADMIN:
        user.role = UserRole.ADMIN
        session.add(
            AuditLog(user_id=None, action="CLI_GRANTED_ADMIN", entity="user", entity_id=user.id)
        )
        session.commit()
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальное управление TicketFlow")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser(
        "promote-admin", help="Назначить зарегистрированного пользователя ADMIN"
    )
    promote.add_argument("email")
    args = parser.parse_args()
    engine = build_engine(Settings())
    try:
        with Session(engine, expire_on_commit=False) as session:
            user = promote_admin(session, args.email)
            print(f"ADMIN: {user.email}")
    except (ValueError, ValidationError) as error:
        parser.exit(1, f"{error}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
