from __future__ import annotations

from decimal import Decimal

import pytest

from ledgerline.money import MoneyError, from_minor, to_minor


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (Decimal("10.50"), "USD", 1050),
        (Decimal("-0.01"), "EUR", -1),
        (Decimal("1500"), "JPY", 1500),
        (Decimal("1.234"), "KWD", 1234),
    ],
)
def test_to_minor_converts_exactly(amount: Decimal, currency: str, expected: int) -> None:
    assert to_minor(amount, currency) == expected


def test_to_minor_refuses_to_round_sub_cent_amounts() -> None:
    with pytest.raises(MoneyError):
        to_minor(Decimal("10.505"), "USD")


def test_unknown_currency_is_rejected() -> None:
    with pytest.raises(MoneyError):
        to_minor(Decimal("1"), "XXX")


def test_from_minor_round_trips_with_currency_scale() -> None:
    assert str(from_minor(1050, "USD")) == "10.50"
    assert str(from_minor(1500, "JPY")) == "1500"
    assert str(from_minor(-5, "BHD")) == "-0.005"
