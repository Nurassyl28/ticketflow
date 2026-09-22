import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select

from ticketflow.auth.security import token_digest
from ticketflow.database import SessionDependency
from ticketflow.models import AuthSession, User, UserRole

bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: SessionDependency,
) -> AuthContext:
    if credentials is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", credentials.credentials) is None:
        raise unauthorized()
    row = session.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == token_digest(credentials.credentials),
            AuthSession.expires_at > func.now(),
        )
    ).one_or_none()
    if row is None or row[1].password_hash.startswith("!"):
        raise unauthorized()
    return AuthContext(user=row[1], session=row[0])


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def get_current_user(auth: CurrentAuth) -> User:
    return auth.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., User]:
    def guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return guard


Manager = Annotated[User, Depends(require_roles(UserRole.ORGANIZER, UserRole.ADMIN))]
Administrator = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
