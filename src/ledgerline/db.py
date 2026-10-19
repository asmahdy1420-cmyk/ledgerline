from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .errors import ImmutableRecord
from .models import Base, JournalEntry, Posting


def make_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def _reject_ledger_mutation(session: Session, _flush_context: Any, _instances: Any) -> None:
    """Journal entries and postings are append-only."""
    for obj in session.deleted:
        if isinstance(obj, (JournalEntry, Posting)):
            raise ImmutableRecord("posted ledger records cannot be deleted; post a reversal")
    for obj in session.dirty:
        if isinstance(obj, (JournalEntry, Posting)) and session.is_modified(
            obj, include_collections=False
        ):
            raise ImmutableRecord("posted ledger records cannot be edited; post a reversal")


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(factory, "before_flush", _reject_ledger_mutation)
    return factory


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
