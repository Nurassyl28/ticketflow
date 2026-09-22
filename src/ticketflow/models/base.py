from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True), UUID: Uuid}


class UUIDPrimaryKey:
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def enum_type(enum: type[StrEnum]) -> Enum:
    return Enum(
        enum,
        name=enum.__name__.lower(),
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


def money_check(column: str) -> CheckConstraint:
    # The upper bound also rejects PostgreSQL's special numeric NaN value.
    return CheckConstraint(f"{column} BETWEEN 0 AND 9999999999.99", name=f"{column}_range")
