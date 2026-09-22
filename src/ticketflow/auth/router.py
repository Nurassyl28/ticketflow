from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ticketflow.auth.dependencies import Administrator, CurrentAuth, CurrentUser, unauthorized
from ticketflow.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    RoleUpdate,
    TokenResponse,
    UserRead,
)
from ticketflow.auth.security import hash_password, new_token, token_digest, verify_password
from ticketflow.database import SessionDependency
from ticketflow.models import AuditLog, AuthSession, User, UserRole

router = APIRouter(prefix="/auth", tags=["Auth"])
admin_router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/register", response_model=UserRead, status_code=201)
def register(body: RegisterRequest, session: SessionDependency) -> User:
    user = User(
        email=body.email,
        password_hash=hash_password(body.password.get_secret_value()),
        role=UserRole.CUSTOMER,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if (
            getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            == "uq_users_email_lower"
        ):
            raise HTTPException(status_code=409, detail="Email is already registered") from None
        raise
    return user


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest, request: Request, response: Response, session: SessionDependency
) -> TokenResponse:
    user = session.scalar(select(User).where(func.lower(User.email) == body.email))
    valid, updated_hash = verify_password(
        body.password.get_secret_value(), user.password_hash if user else None
    )
    if not valid or user is None:
        raise unauthorized("Invalid email or password")
    if updated_hash is not None:
        user.password_hash = updated_hash

    ttl = request.app.state.settings.auth_token_ttl_minutes * 60
    now = session.scalar(select(func.now()))
    token = new_token()
    session.add(
        AuthSession(
            token_hash=token_digest(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=token, expires_in=ttl)


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> User:
    return user


@router.post("/logout", status_code=204)
def logout(auth: CurrentAuth, session: SessionDependency) -> Response:
    session.delete(auth.session)
    session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def client_ip(request: Request) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(request.client.host) if request.client else None
    except ValueError:
        return None


@admin_router.patch("/users/{user_id}/role", response_model=UserRead)
def change_role(
    user_id: UUID,
    body: RoleUpdate,
    request: Request,
    admin: Administrator,
    session: SessionDependency,
) -> User:
    if user_id == admin.id:
        raise HTTPException(status_code=403, detail="Cannot change your own role")
    target = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=403, detail="Cannot change an administrator through this API"
        )
    if target.role != body.role:
        target.role = body.role
        session.add(
            AuditLog(
                user_id=admin.id,
                action=f"ADMIN_SET_ROLE_{body.role.value}",
                entity="user",
                entity_id=target.id,
                ip=client_ip(request),
            )
        )
        session.commit()
    return target
