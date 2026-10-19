from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AccountType(str, enum.Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def is_debit_normal(self) -> bool:
        """Assets and expenses grow with debits; the rest grow with credits."""
        return self in (AccountType.ASSET, AccountType.EXPENSE)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[AccountType] = mapped_column(
        Enum(
            AccountType,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JournalEntry(Base):
    """A posted, immutable journal entry. Corrections are new reversing entries."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    description: Mapped[str] = mapped_column(String(500))
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Unique: an entry can be reversed at most once, enforced by the database.
    reverses_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("journal_entries.id"), unique=True, nullable=True
    )

    postings: Mapped[list[Posting]] = relationship(
        back_populates="entry", order_by="Posting.id", lazy="selectin"
    )


class Posting(Base):
    """One leg of an entry. Positive ``amount_minor`` is a debit, negative a credit."""

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount_minor <> 0", name="ck_postings_nonzero"),
        Index("ix_postings_account_entry", "account_id", "entry_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))

    entry: Mapped[JournalEntry] = relationship(back_populates="postings")


class IdempotencyRecord(Base):
    """Stored outcome of a request made with an ``Idempotency-Key`` header."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    """Event written in the same transaction as the state change it describes."""

    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "id"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_type: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
