"""Ledger use cases. Functions flush but never commit, except ``transfer``,
which owns its transaction because idempotency needs to react to a commit-time
unique violation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import (
    AlreadyReversed,
    Conflict,
    IdempotencyKeyReused,
    NotFound,
    UnbalancedEntry,
    ValidationFailed,
)
from .models import (
    Account,
    AccountType,
    IdempotencyRecord,
    JournalEntry,
    OutboxEvent,
    Posting,
    utcnow,
)
from .money import MoneyError, from_minor, minor_units, to_minor

ENTRY_POSTED = "entry.posted"


@dataclass(frozen=True)
class PostingInput:
    account_id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True)
class Balance:
    account_id: uuid.UUID
    currency: str
    balance: Decimal
    as_of: Optional[date]


@dataclass(frozen=True)
class TransferResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


# --- accounts ---------------------------------------------------------------


def create_account(
    session: Session, *, code: str, name: str, type: AccountType, currency: str
) -> Account:
    try:
        minor_units(currency)
    except MoneyError as exc:
        raise ValidationFailed(str(exc)) from None
    if session.scalar(select(Account.id).where(Account.code == code)) is not None:
        raise Conflict(f"account code {code!r} already exists")
    account = Account(code=code, name=name, type=type, currency=currency)
    session.add(account)
    session.flush()
    return account


def get_account(session: Session, account_id: uuid.UUID) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise NotFound(f"account {account_id} not found")
    return account


def list_accounts(session: Session) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.code)))


def account_balance(
    session: Session, account_id: uuid.UUID, as_of: Optional[date] = None
) -> Balance:
    """Balance in the account's normal direction, including entries effective on ``as_of``."""
    account = get_account(session, account_id)
    stmt = (
        select(func.coalesce(func.sum(Posting.amount_minor), 0))
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .where(Posting.account_id == account_id)
    )
    if as_of is not None:
        stmt = stmt.where(JournalEntry.effective_date <= as_of)
    net_debit = int(session.scalar(stmt) or 0)
    signed = net_debit if account.type.is_debit_normal else -net_debit
    return Balance(
        account_id=account.id,
        currency=account.currency,
        balance=from_minor(signed, account.currency),
        as_of=as_of,
    )


# --- journal entries ----------------------------------------------------------


def entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "description": entry.description,
        "effective_date": entry.effective_date.isoformat(),
        "created_at": entry.created_at.isoformat(),
        "reverses_id": str(entry.reverses_id) if entry.reverses_id else None,
        "postings": [
            {
                "account_id": str(p.account_id),
                "amount": str(from_minor(p.amount_minor, p.currency)),
                "currency": p.currency,
            }
            for p in entry.postings
        ],
    }


def post_entry(
    session: Session,
    *,
    description: str,
    postings: Sequence[PostingInput],
    effective_date: Optional[date] = None,
    reverses_id: Optional[uuid.UUID] = None,
) -> JournalEntry:
    """Validate and persist a balanced entry plus its ``entry.posted`` outbox event."""
    if len(postings) < 2:
        raise UnbalancedEntry("an entry needs at least two postings")

    account_ids = {p.account_id for p in postings}
    accounts = {
        a.id: a for a in session.scalars(select(Account).where(Account.id.in_(account_ids)))
    }
    missing = account_ids - accounts.keys()
    if missing:
        raise NotFound(f"unknown account(s): {', '.join(sorted(str(m) for m in missing))}")

    rows: list[Posting] = []
    totals: dict[str, int] = defaultdict(int)
    for item in postings:
        account = accounts[item.account_id]
        try:
            amount_minor = to_minor(item.amount, account.currency)
        except MoneyError as exc:
            raise ValidationFailed(str(exc)) from None
        if amount_minor == 0:
            raise ValidationFailed("posting amounts must be non-zero")
        totals[account.currency] += amount_minor
        rows.append(
            Posting(account_id=account.id, amount_minor=amount_minor, currency=account.currency)
        )

    unbalanced = {cur: total for cur, total in totals.items() if total != 0}
    if unbalanced:
        detail = ", ".join(f"{cur} {from_minor(t, cur)}" for cur, t in sorted(unbalanced.items()))
        raise UnbalancedEntry(f"postings must sum to zero per currency; off by {detail}")

    entry = JournalEntry(
        description=description,
        effective_date=effective_date or utcnow().date(),
        reverses_id=reverses_id,
        created_at=utcnow(),
        postings=rows,
    )
    session.add(entry)
    session.flush()
    session.add(
        OutboxEvent(
            event_type=ENTRY_POSTED, aggregate_id=str(entry.id), payload=entry_to_dict(entry)
        )
    )
    session.flush()
    return entry


def get_entry(session: Session, entry_id: uuid.UUID) -> JournalEntry:
    entry = session.get(JournalEntry, entry_id)
    if entry is None:
        raise NotFound(f"entry {entry_id} not found")
    return entry


def reverse_entry(
    session: Session,
    entry_id: uuid.UUID,
    *,
    description: Optional[str] = None,
    effective_date: Optional[date] = None,
) -> JournalEntry:
    """Post the mirror image of an entry. The original is never touched."""
    original = get_entry(session, entry_id)
    if session.scalar(select(JournalEntry.id).where(JournalEntry.reverses_id == entry_id)):
        raise AlreadyReversed(f"entry {entry_id} has already been reversed")
    legs = [
        PostingInput(p.account_id, -from_minor(p.amount_minor, p.currency))
        for p in original.postings
    ]
    try:
        with session.begin_nested():
            return post_entry(
                session,
                description=description or f"Reversal of {original.id}",
                postings=legs,
                effective_date=effective_date,
                reverses_id=original.id,
            )
    except IntegrityError:
        # A concurrent request reversed it first; the unique index caught it.
        raise AlreadyReversed(f"entry {entry_id} has already been reversed") from None


# --- idempotent transfers ---------------------------------------------------


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(record: IdempotencyRecord, fingerprint: str) -> TransferResult:
    if record.request_hash != fingerprint:
        raise IdempotencyKeyReused(
            "this Idempotency-Key was already used with a different request body"
        )
    return TransferResult(record.status_code, record.response_body, replayed=True)


def transfer(
    session: Session,
    *,
    idempotency_key: str,
    from_account_id: uuid.UUID,
    to_account_id: uuid.UUID,
    amount: Decimal,
    currency: str,
    description: str,
    effective_date: Optional[date] = None,
) -> TransferResult:
    """Move ``amount`` from one account to another exactly once per key.

    The entry, its outbox event and the idempotency record commit together.
    Failed validations are not recorded, so the client may fix and retry.
    """
    fingerprint = request_fingerprint(
        {
            "from_account_id": str(from_account_id),
            "to_account_id": str(to_account_id),
            "amount": str(amount),
            "currency": currency,
            "description": description,
            "effective_date": effective_date.isoformat() if effective_date else None,
        }
    )
    existing = session.get(IdempotencyRecord, idempotency_key)
    if existing is not None:
        return _replay(existing, fingerprint)

    if from_account_id == to_account_id:
        raise ValidationFailed("cannot transfer to the same account")
    if amount <= 0:
        raise ValidationFailed("transfer amount must be positive")
    for account_id in (from_account_id, to_account_id):
        account = get_account(session, account_id)
        if account.currency != currency:
            raise ValidationFailed(
                f"account {account.code} is in {account.currency}, transfer is in {currency}"
            )

    try:
        entry = post_entry(
            session,
            description=description,
            postings=[
                PostingInput(from_account_id, -amount),
                PostingInput(to_account_id, amount),
            ],
            effective_date=effective_date,
        )
        body = entry_to_dict(entry)
        session.add(
            IdempotencyRecord(
                key=idempotency_key, request_hash=fingerprint, status_code=201, response_body=body
            )
        )
        session.commit()
    except IntegrityError:
        # Another request with the same key committed between our read and write.
        session.rollback()
        winner = session.get(IdempotencyRecord, idempotency_key)
        if winner is None:
            raise
        return _replay(winner, fingerprint)
    except Exception:
        session.rollback()
        raise
    return TransferResult(201, body, replayed=False)
