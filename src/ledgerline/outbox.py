"""Transactional outbox relay.

Delivery is at-least-once: an event is marked published only after the
publisher returns, so a crash in between causes a redelivery. Consumers
deduplicate on ``event_id``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import OutboxEvent, utcnow

logger = logging.getLogger("ledgerline.outbox")


@dataclass(frozen=True)
class OutboxMessage:
    event_id: int
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "event_type": self.event_type,
                "aggregate_id": self.aggregate_id,
                "created_at": self.created_at.isoformat(),
                "payload": self.payload,
            },
            sort_keys=True,
        )


class Publisher(Protocol):
    def publish(self, message: OutboxMessage) -> None: ...


class LoggingPublisher:
    """Default publisher: writes each event as one JSON log line."""

    def publish(self, message: OutboxMessage) -> None:
        logger.info("%s", message.to_json())


@dataclass
class InMemoryPublisher:
    """Collects messages; useful in tests and local experiments."""

    messages: list[OutboxMessage] = field(default_factory=list)

    def publish(self, message: OutboxMessage) -> None:
        self.messages.append(message)


@dataclass(frozen=True)
class RelayResult:
    published: int
    failed: int


def relay_once(
    session_factory: sessionmaker[Session],
    publisher: Publisher,
    *,
    batch_size: int = 100,
    max_attempts: int = 10,
) -> RelayResult:
    """Publish one batch of pending events in id order.

    Rows are locked with ``FOR UPDATE SKIP LOCKED`` on PostgreSQL so several
    relays can run side by side. On the first failure the batch stops, which
    keeps events in order; the failed row is retried on the next run until
    ``max_attempts`` is reached.
    """
    published = failed = 0
    with session_factory() as session, session.begin():
        rows = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.attempts < max_attempts)
            .order_by(OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            message = OutboxMessage(
                event_id=row.id,
                event_type=row.event_type,
                aggregate_id=row.aggregate_id,
                payload=row.payload,
                created_at=row.created_at,
            )
            try:
                publisher.publish(message)
            except Exception as exc:
                row.attempts += 1
                row.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                failed += 1
                logger.warning("outbox event %s failed: %s", row.id, row.last_error)
                break
            row.published_at = utcnow()
            row.attempts += 1
            published += 1
    return RelayResult(published=published, failed=failed)


def run_forever(
    session_factory: sessionmaker[Session],
    publisher: Publisher,
    *,
    interval: float = 1.0,
    batch_size: int = 100,
    stop: Optional[threading.Event] = None,
) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        result = relay_once(session_factory, publisher, batch_size=batch_size)
        # Drain quickly while there is a backlog; otherwise sleep.
        if result.published < batch_size or result.failed:
            stop.wait(interval)


def main() -> None:
    from .config import load_settings
    from .db import create_schema, make_engine, make_session_factory

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    engine = make_engine(settings.database_url)
    create_schema(engine)
    logger.info("outbox relay started")
    try:
        run_forever(
            make_session_factory(engine),
            LoggingPublisher(),
            interval=settings.relay_interval_seconds,
            batch_size=settings.relay_batch_size,
        )
    except KeyboardInterrupt:
        logger.info("outbox relay stopped")


if __name__ == "__main__":
    main()
