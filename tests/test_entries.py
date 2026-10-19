from __future__ import annotations

import pytest
from sqlalchemy import select

from ledgerline.errors import ImmutableRecord
from ledgerline.models import JournalEntry, OutboxEvent


def _entry(client, cash, revenue, amount="100.00", date="2026-01-15"):
    return client.post(
        "/entries",
        json={
            "description": "Invoice paid",
            "effective_date": date,
            "postings": [
                {"account_id": cash["id"], "amount": amount},
                {"account_id": revenue["id"], "amount": f"-{amount}"},
            ],
        },
    )


def test_balanced_entry_is_posted_and_updates_balances(client, make_account) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")

    resp = _entry(client, cash, revenue)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [p["amount"] for p in body["postings"]] == ["100.00", "-100.00"]

    # Both accounts show a positive balance in their normal direction.
    assert client.get(f"/accounts/{cash['id']}/balance").json()["balance"] == "100.00"
    assert client.get(f"/accounts/{revenue['id']}/balance").json()["balance"] == "100.00"


def test_unbalanced_entry_is_rejected(client, make_account) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    resp = client.post(
        "/entries",
        json={
            "description": "Typo",
            "postings": [
                {"account_id": cash["id"], "amount": "100.00"},
                {"account_id": revenue["id"], "amount": "-99.99"},
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "unbalanced_entry"


def test_entry_must_balance_per_currency(client, make_account) -> None:
    usd = make_account("cash-usd")
    eur = make_account("cash-eur", currency="EUR")
    resp = client.post(
        "/entries",
        json={
            "description": "Not an FX trade",
            "postings": [
                {"account_id": usd["id"], "amount": "10.00"},
                {"account_id": eur["id"], "amount": "-10.00"},
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "unbalanced_entry"


def test_amount_with_too_many_decimals_is_rejected(client, make_account) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    resp = _entry(client, cash, revenue, amount="1.005")
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_failed"


def test_balance_as_of_date_ignores_later_entries(client, make_account) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    assert _entry(client, cash, revenue, "100.00", "2026-01-15").status_code == 201
    assert _entry(client, cash, revenue, "50.00", "2026-02-15").status_code == 201

    url = f"/accounts/{cash['id']}/balance"
    assert client.get(url, params={"as_of": "2026-01-01"}).json()["balance"] == "0.00"
    assert client.get(url, params={"as_of": "2026-01-31"}).json()["balance"] == "100.00"
    assert client.get(url).json()["balance"] == "150.00"


def test_reversal_posts_mirror_entry_and_only_once(client, make_account) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    original = _entry(client, cash, revenue).json()

    resp = client.post(f"/entries/{original['id']}/reverse")
    assert resp.status_code == 201, resp.text
    reversal = resp.json()
    assert reversal["reverses_id"] == original["id"]
    assert [p["amount"] for p in reversal["postings"]] == ["-100.00", "100.00"]
    assert client.get(f"/accounts/{cash['id']}/balance").json()["balance"] == "0.00"

    again = client.post(f"/entries/{original['id']}/reverse")
    assert again.status_code == 409
    assert again.json()["error"] == "already_reversed"


def test_posted_entries_cannot_be_edited(client, make_account, session_factory) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    _entry(client, cash, revenue)

    with session_factory() as session:
        entry = session.scalars(select(JournalEntry)).one()
        entry.description = "rewritten history"
        with pytest.raises(ImmutableRecord):
            session.flush()


def test_every_entry_writes_an_outbox_event(client, make_account, session_factory) -> None:
    cash = make_account("cash")
    revenue = make_account("revenue", type="income")
    entry = _entry(client, cash, revenue).json()

    with session_factory() as session:
        events = session.scalars(select(OutboxEvent)).all()
    assert len(events) == 1
    assert events[0].event_type == "entry.posted"
    assert events[0].aggregate_id == entry["id"]
    assert events[0].published_at is None
