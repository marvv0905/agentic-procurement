"""Integer minor-unit money handling for HKD.

PurchasePilot uses integer *minor units* (cents) for all money arithmetic so
that no floating-point representation errors can leak into eligibility or
budget decisions. One HKD dollar == 100 minor units.

Every conversion into minor units is explicit and raises on fractional cents,
so a silent rounding error cannot be introduced downstream.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Minor units per major currency unit (HKD cents).
MINOR_UNITS_PER_MAJOR = 100

#: The only currency supported by the MVP.
SUPPORTED_CURRENCY = "HKD"


class CurrencyError(ValueError):
    """Raised when a currency or amount cannot be represented safely."""


def parse_minor(value: str | int | Decimal) -> int:
    """Parse an amount into integer minor units.

    Accepts an integer (interpreted as minor units) or a Decimal/string that
    represents a major-unit amount. Fractional cents raise :class:`CurrencyError`
    rather than silently rounding.
    """
    if isinstance(value, bool):
        raise CurrencyError(f"invalid money value: {value!r}")
    if isinstance(value, int):
        return value
    try:
        major = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise CurrencyError(f"invalid money value: {value!r}") from exc
    minor = major * MINOR_UNITS_PER_MAJOR
    if minor != minor.to_integral_value():
        raise CurrencyError(f"fractional cent not allowed: {value!r}")
    return int(minor)


def to_major(minor: int) -> Decimal:
    """Convert integer minor units back to a major-unit :class:`Decimal`."""
    if not isinstance(minor, int):
        raise CurrencyError(f"minor units must be an integer, got {minor!r}")
    return Decimal(minor) / MINOR_UNITS_PER_MAJOR


def parse_hkd_minor(value: str | int | Decimal | None) -> int | None:
    """Best-effort parse of a human/LLM-written HKD amount into minor units.

    Handles ``"HK$1,800.00"``, ``"1,800.00"``, ``"1800"``, ``1800``, and
    ``Decimal``. Returns ``None`` (rather than raising) when the value is not a
    clean amount, so a garbled extraction is recorded as unknown instead of
    crashing persistence.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    s = (
        str(value)
        .strip()
        .replace("HK$", "")
        .replace("HKD", "")
        .replace("$", "")
        .replace(",", "")
        .strip()
    )
    try:
        major = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    minor = major * MINOR_UNITS_PER_MAJOR
    if minor != minor.to_integral_value():
        return None
    return int(minor)


def format_hkd(minor: int) -> str:
    """Format minor units as a human-readable HKD string."""
    return f"HK${to_major(minor):,.2f}"


def add(*amounts: int) -> int:
    """Sum integer minor-unit amounts."""
    return sum(amounts)


def verified_total(unit_price_minor: int, quantity: int, *charges_minor: int) -> int:
    """Compute a verified total in minor units.

    ``unit_price_minor * quantity`` plus any additional charges (e.g. shipping).
    ``quantity`` must be a positive integer.
    """
    if not isinstance(quantity, int) or quantity <= 0:
        raise CurrencyError(f"quantity must be a positive integer, got {quantity!r}")
    return unit_price_minor * quantity + sum(charges_minor)
