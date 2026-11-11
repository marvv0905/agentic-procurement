import pytest

from procurement.money import (
    CurrencyError,
    format_hkd,
    parse_hkd_minor,
    parse_minor,
    to_major,
    verified_total,
)


def test_parse_major_string():
    assert parse_minor("10.50") == 1050


def test_parse_integer_is_minor_units():
    assert parse_minor(1050) == 1050


def test_parse_rejects_fractional_cent():
    with pytest.raises(CurrencyError):
        parse_minor("1.005")


def test_to_major():
    assert to_major(1050) == pytest.approx(10.50)


def test_format_hkd():
    assert format_hkd(1050) == "HK$10.50"


def test_verified_total_matches_proposal_example():
    # §9: six units at HK$1,800 with HK$150 shipping -> HK$10,950.
    assert verified_total(180000, 6, 15000) == 1_095_000


def test_verified_total_rejects_nonpositive_quantity():
    with pytest.raises(CurrencyError):
        verified_total(180000, 0)


def test_parse_hkd_minor_handles_formats():
    assert parse_hkd_minor("HK$3,300.33") == 330033
    assert parse_hkd_minor("1,800.00") == 180000
    assert parse_hkd_minor("1800") == 180000
    assert parse_hkd_minor(180000) == 180000


def test_parse_hkd_minor_returns_none_for_garbage():
    assert parse_hkd_minor("not a price") is None
    assert parse_hkd_minor("HK$1.005") is None
    assert parse_hkd_minor(None) is None
