"""Money helpers.

Amounts are stored as integer minor units (cents, fils, yen) so that no
floating point or database-specific decimal behaviour can leak into the
ledger. Conversion to and from ``Decimal`` happens only at the edges.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# ISO 4217 minor units for the currencies the ledger accepts.
MINOR_UNITS: dict[str, int] = {
    "AED": 2,
    "BHD": 3,
    "EGP": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "KWD": 3,
    "QAR": 2,
    "SAR": 2,
    "TRY": 2,
    "USD": 2,
}


class MoneyError(ValueError):
    """Raised when an amount or currency cannot be represented exactly."""


def minor_units(currency: str) -> int:
    try:
        return MINOR_UNITS[currency]
    except KeyError:
        raise MoneyError(f"unsupported currency: {currency}") from None


def to_minor(amount: Decimal, currency: str) -> int:
    """Convert a decimal amount to integer minor units without rounding.

    ``Decimal("10.505")`` in USD is rejected rather than silently rounded:
    a ledger must never invent or lose a fraction of a cent.
    """
    units = minor_units(currency)
    try:
        amount = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise MoneyError(f"invalid amount: {amount!r}") from None
    if not amount.is_finite():
        raise MoneyError(f"invalid amount: {amount!r}")
    scaled = amount.scaleb(units)
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"{amount} has more than {units} decimal places for {currency}")
    return int(scaled)


def from_minor(value: int, currency: str) -> Decimal:
    units = minor_units(currency)
    return Decimal(value).scaleb(-units).quantize(Decimal(1).scaleb(-units))
