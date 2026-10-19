from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .models import AccountType

CurrencyCode = Field(pattern=r"^[A-Z]{3}$", examples=["USD"])


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32, examples=["1000-cash"])
    name: str = Field(min_length=1, max_length=200)
    type: AccountType
    currency: str = CurrencyCode


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    type: AccountType
    currency: str
    created_at: datetime


class BalanceOut(BaseModel):
    account_id: uuid.UUID
    currency: str
    balance: Decimal
    as_of: Optional[date] = None


class PostingIn(BaseModel):
    account_id: uuid.UUID
    amount: Decimal = Field(description="Signed amount: positive debits, negative credits.")


class EntryCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    effective_date: Optional[date] = None
    postings: list[PostingIn] = Field(min_length=2)


class PostingOut(BaseModel):
    account_id: uuid.UUID
    amount: Decimal
    currency: str


class EntryOut(BaseModel):
    id: uuid.UUID
    description: str
    effective_date: date
    created_at: datetime
    reverses_id: Optional[uuid.UUID] = None
    postings: list[PostingOut]


class ReverseRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=500)
    effective_date: Optional[date] = None


class TransferCreate(BaseModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    currency: str = CurrencyCode
    description: str = Field(default="Transfer", min_length=1, max_length=500)
    effective_date: Optional[date] = None


class ErrorOut(BaseModel):
    error: str
    message: str
