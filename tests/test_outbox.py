from __future__ import annotations

from sqlalchemy import select

from ledgerline.models import OutboxEvent
from ledgerline.outbox import InMemoryPublisher, relay_once


def _post_transfers(client, make_account, count: int) -> None:
    alice = make_account("alice")
    bob = make_account("bob")
    for i in range(count):
        resp = client.post(
            "/transfers",
            json={
                "from_account_id": alice["id"],
                "to_account_id": bob["id"],
                "amount": "1.00",
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"k-{i}"},
        )
        assert resp.status_code == 201


def test_relay_publishes_pending_events_in_order_once(
    client, make_account, session_factory
) -> None:
    _post_transfers(client, make_account, 3)
    publisher = InMemoryPublisher()

    result = relay_once(session_factory, publisher)
    assert result.published == 3
    ids = [m.event_id for m in publisher.messages]
    assert ids == sorted(ids)
    assert {m.event_type for m in publisher.messages} == {"entry.posted"}

    # Nothing left to publish on the next run.
    assert relay_once(session_factory, publisher).published == 0
    assert len(publisher.messages) == 3


class FlakyPublisher:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.delivered: list[int] = []

    def publish(self, message) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker unavailable")
        self.delivered.append(message.event_id)


def test_relay_keeps_failed_events_for_retry(client, make_account, session_factory) -> None:
    _post_transfers(client, make_account, 2)
    publisher = FlakyPublisher(fail_times=1)

    first = relay_once(session_factory, publisher)
    assert (first.published, first.failed) == (0, 1)
    with session_factory() as session:
        head = session.scalars(select(OutboxEvent).order_by(OutboxEvent.id)).first()
        assert head.attempts == 1
        assert "broker unavailable" in head.last_error

    second = relay_once(session_factory, publisher)
    assert second.published == 2
    assert len(publisher.delivered) == 2
