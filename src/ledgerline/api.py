from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import __version__, service
from .config import load_settings
from .db import create_schema, make_engine, make_session_factory
from .errors import LedgerError
from .schemas import (
    AccountCreate,
    AccountOut,
    BalanceOut,
    EntryCreate,
    EntryOut,
    ReverseRequest,
    TransferCreate,
)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(database_url: Optional[str] = None) -> FastAPI:
    url = database_url or load_settings().database_url
    engine = make_engine(url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        create_schema(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="ledgerline",
        version=__version__,
        summary="Double-entry ledger API",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory

    @app.exception_handler(LedgerError)
    async def _ledger_error(_request: Request, exc: LedgerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.code, "message": exc.message}
        )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/accounts", status_code=201, response_model=AccountOut, tags=["accounts"])
    def create_account(body: AccountCreate, session: SessionDep):
        account = service.create_account(
            session, code=body.code, name=body.name, type=body.type, currency=body.currency
        )
        session.commit()
        return account

    @app.get("/accounts", response_model=list[AccountOut], tags=["accounts"])
    def list_accounts(session: SessionDep):
        return service.list_accounts(session)

    @app.get("/accounts/{account_id}", response_model=AccountOut, tags=["accounts"])
    def get_account(account_id: uuid.UUID, session: SessionDep):
        return service.get_account(session, account_id)

    @app.get("/accounts/{account_id}/balance", response_model=BalanceOut, tags=["accounts"])
    def get_balance(
        account_id: uuid.UUID,
        session: SessionDep,
        as_of: Annotated[
            Optional[date], Query(description="Include entries up to this date")
        ] = None,
    ):
        return service.account_balance(session, account_id, as_of)

    @app.post("/entries", status_code=201, response_model=EntryOut, tags=["entries"])
    def post_entry(body: EntryCreate, session: SessionDep):
        entry = service.post_entry(
            session,
            description=body.description,
            effective_date=body.effective_date,
            postings=[service.PostingInput(p.account_id, p.amount) for p in body.postings],
        )
        session.commit()
        return service.entry_to_dict(entry)

    @app.get("/entries/{entry_id}", response_model=EntryOut, tags=["entries"])
    def get_entry(entry_id: uuid.UUID, session: SessionDep):
        return service.entry_to_dict(service.get_entry(session, entry_id))

    @app.post(
        "/entries/{entry_id}/reverse", status_code=201, response_model=EntryOut, tags=["entries"]
    )
    def reverse_entry(
        entry_id: uuid.UUID, session: SessionDep, body: Optional[ReverseRequest] = None
    ):
        body = body or ReverseRequest()
        entry = service.reverse_entry(
            session, entry_id, description=body.description, effective_date=body.effective_date
        )
        session.commit()
        return service.entry_to_dict(entry)

    @app.post("/transfers", status_code=201, response_model=EntryOut, tags=["transfers"])
    def create_transfer(
        body: TransferCreate,
        response: Response,
        session: SessionDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
    ):
        result = service.transfer(
            session,
            idempotency_key=idempotency_key,
            from_account_id=body.from_account_id,
            to_account_id=body.to_account_id,
            amount=body.amount,
            currency=body.currency,
            description=body.description,
            effective_date=body.effective_date,
        )
        response.status_code = result.status_code
        response.headers["Idempotent-Replayed"] = "true" if result.replayed else "false"
        return result.body

    return app
