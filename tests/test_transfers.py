from __future__ import annotations

from sqlalchemy import func, select

from ledgerline.models import JournalEntry


def _transfer(client, src, dst, amount="25.00", key="key-1", **extra):
    payload = {
        "from_account_id": src["id"],
        "to_account_id": dst["id"],
        "amount": amount,
        "currency": "USD",
        **extra,
    }
    return client.post("/transfers", json=payload, headers={"Idempotency-Key": key})


def test_transfer_moves_money_between_accounts(client, make_account) -> None:
    alice = make_account("alice")
    bob = make_account("bob")

    resp = _transfer(client, alice, bob)
    assert resp.status_code == 201, resp.text
    assert resp.headers["Idempotent-Replayed"] == "false"
    assert client.get(f"/accounts/{alice['id']}/balance").json()["balance"] == "-25.00"
    assert client.get(f"/accounts/{bob['id']}/balance").json()["balance"] == "25.00"


def test_retry_with_same_key_replays_without_double_posting(
    client, make_account, session_factory
) -> None:
    alice = make_account("alice")
    bob = make_account("bob")

    first = _transfer(client, alice, bob)
    second = _transfer(client, alice, bob)
    assert second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalEntry)) == 1
    assert client.get(f"/accounts/{bob['id']}/balance").json()["balance"] == "25.00"


def test_same_key_with_different_body_is_rejected(client, make_account) -> None:
    alice = make_account("alice")
    bob = make_account("bob")
    assert _transfer(client, alice, bob, amount="25.00").status_code == 201

    resp = _transfer(client, alice, bob, amount="30.00")
    assert resp.status_code == 422
    assert resp.json()["error"] == "idempotency_key_reused"


def test_transfer_requires_idempotency_key(client, make_account) -> None:
    alice = make_account("alice")
    bob = make_account("bob")
    resp = client.post(
        "/transfers",
        json={
            "from_account_id": alice["id"],
            "to_account_id": bob["id"],
            "amount": "1.00",
            "currency": "USD",
        },
    )
    assert resp.status_code == 422


def test_currency_mismatch_is_rejected_and_key_stays_usable(client, make_account) -> None:
    alice = make_account("alice")
    euro = make_account("euro", currency="EUR")
    bob = make_account("bob")

    bad = _transfer(client, alice, euro, key="retry-me")
    assert bad.status_code == 422
    # Failed validation is not stored, so the client can correct and reuse the key.
    good = _transfer(client, alice, bob, key="retry-me")
    assert good.status_code == 201
    assert good.headers["Idempotent-Replayed"] == "false"
