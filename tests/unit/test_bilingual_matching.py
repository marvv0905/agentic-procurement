"""Bilingual (Traditional Chinese) keyword and search parity."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from procurement.catalog import CATEGORIES, OTHER_CATEGORY
from procurement.rules import validate_offer
from procurement.schemas import PurchaseRequest, RequestRequirements, ValidationStatus


def _request(category: str, attributes: dict, **overrides) -> PurchaseRequest:
    max_total = overrides.pop("max_total_minor", 1_000_000)
    quantity = overrides.pop("quantity", 1)
    deadline = overrides.pop("deadline", None)
    return PurchaseRequest(
        request_id="R",
        requester="t",
        department="d",
        category=category,
        quantity=quantity,
        deadline=deadline,
        max_total_cost_minor=max_total,
        mandatory=RequestRequirements(
            category=category, attributes=attributes, warranty_months_min=None
        ),
        created_at=datetime.now(timezone.utc),
        **overrides,
    )


def _offer(attributes: dict, **kwargs):
    from procurement.schemas import OfferEvidence

    defaults = dict(
        offer_id="SUP1:item--shop",
        supplier_id="SUP1",
        product_id="item--shop",
        category=OTHER_CATEGORY,
        model_name="Sony WH-1000XM5",
        unit_price_minor=1_500,
        shipping_minor=None,
        available_quantity=None,
        attributes=attributes,
        warranty_months=None,
        delivery_by=None,
    )
    defaults.update(kwargs)
    defaults["retrieved_at"] = datetime.now(timezone.utc)
    return OfferEvidence(**defaults)


# --- Chinese keywords match the untranslated evidence ------------------------------


def test_chinese_keyword_matches_original_specs():
    request = _request(OTHER_CATEGORY, {"item": "降噪藍牙耳機", "keywords": ["降噪", "藍牙"]})
    offer = _offer(
        {
            "shop": "Shop",
            "specifications": "wireless noise cancelling bluetooth headphones",
            "specifications_original": "無線降噪藍牙耳機 主動式降噪",
        }
    )
    result = validate_offer(request, offer)
    for check in result.checks:
        if check.field in ("降噪", "藍牙"):
            assert check.status is ValidationStatus.PASS, (check.field, check.reason)


def test_english_keyword_still_matches_translated_specs():
    request = _request(OTHER_CATEGORY, {"item": "headphones", "keywords": ["noise cancelling"]})
    offer = _offer(
        {
            "shop": "Shop",
            "specifications": "wireless noise cancelling headphones",
            "specifications_original": "無線降噪藍牙耳機",
        }
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "noise cancelling")
    assert check.status is ValidationStatus.PASS


def test_mixed_language_keywords_each_match_their_own_text():
    request = _request(
        OTHER_CATEGORY, {"item": "headphones", "keywords": ["noise cancelling", "降噪"]}
    )
    offer = _offer(
        {
            "shop": "Shop",
            # English-only specs would fail 降噪; the original text covers it.
            "specifications": "wireless noise cancelling headphones",
            "specifications_original": "無線降噪耳機",
        }
    )
    result = validate_offer(request, offer)
    statuses = {c.field: c.status for c in result.checks if c.field in ("noise cancelling", "降噪")}
    assert statuses == {"noise cancelling": ValidationStatus.PASS, "降噪": ValidationStatus.PASS}


def test_chinese_keyword_absent_everywhere_still_fails():
    request = _request(OTHER_CATEGORY, {"item": "耳機", "keywords": ["防水"]})
    offer = _offer(
        {
            "shop": "Shop",
            "specifications": "wireless noise cancelling headphones",
            "specifications_original": "無線降噪藍牙耳機",
        }
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "防水")
    assert check.status is ValidationStatus.FAIL


# --- search phrase fallback chain ----------------------------------------------------


def test_typed_phrases_fall_back_to_chinese_then_bare_label():
    from procurement.tools.suppliers import _search_phrases

    request = _request(
        "monitor",
        {"screen_inches_min": 27, "resolution": "2560x1440", "connectors": ["HDMI"]},
    )
    phrases = _search_phrases(request)
    assert phrases[0].startswith("Monitor 27")
    assert any("顯示器" in p for p in phrases)
    assert phrases[-1] == "Monitor"


def test_free_text_phrase_is_verbatim_and_single():
    from procurement.tools.suppliers import _search_phrases

    request = _request(
        OTHER_CATEGORY, {"item": "降噪藍牙耳機", "keywords": ["降噪"], "search_query": "降噪藍牙耳機"}
    )
    assert _search_phrases(request) == ["降噪藍牙耳機"]


def test_catalog_carries_chinese_labels():
    assert CATEGORIES["monitor"].label_zh == "顯示器"
    assert CATEGORIES["keyboard"].label_zh == "鍵盤"
    assert CATEGORIES["chair"].label_zh == "辦公椅"


# --- listing ranking matches both languages -------------------------------------------


def test_rank_listing_matches_via_description_english():
    from procurement.tools.suppliers import _ListingProduct, _rank_listing

    request = _request(OTHER_CATEGORY, {"item": "耳機", "keywords": ["noise cancelling"]})
    products = [
        _ListingProduct(
            product_name="Sony WH-1000XM5",
            price_hkd="HK$1,999",
            description="無線降噪耳機",
            description_english="wireless noise cancelling headphones",
        ),
        _ListingProduct(product_name="Cheap earbuds", price_hkd="HK$100", description=""),
    ]
    ranked = _rank_listing(products, request)
    assert ranked[0].product_name == "Sony WH-1000XM5"


def test_rank_listing_matches_via_original_chinese():
    from procurement.tools.suppliers import _ListingProduct, _rank_listing

    request = _request(OTHER_CATEGORY, {"item": "耳機", "keywords": ["降噪"]})
    products = [
        _ListingProduct(
            product_name="Sony WH-1000XM5",
            price_hkd="HK$1,999",
            description="無線降噪耳機",
            description_english="wireless noise cancelling headphones",
        ),
        _ListingProduct(product_name="Cheap earbuds", price_hkd="HK$100", description=""),
    ]
    ranked = _rank_listing(products, request)
    assert ranked[0].product_name == "Sony WH-1000XM5"


# --- offer evidence keeps the original language ----------------------------------------


def test_shop_offer_keeps_original_language_specs():
    from procurement.tools.suppliers import (
        _ListingProduct,
        _ProductDetails,
        _ShopOffer,
        _shop_offer_to_evidence,
    )

    class _Sup:
        id = 5

    request = _request(OTHER_CATEGORY, {"item": "耳機", "keywords": ["降噪"]})
    offer = _ShopOffer(shop_name="Star Tech", price_hkd="HK$1,480", stock_state="in_stock")
    details = _ProductDetails(
        specifications="wireless active noise cancelling headphones",
        specifications_original="無線主動降噪耳機",
        offers=[offer],
    )
    listing = _ListingProduct(
        product_name="Sony WH-1000XM5 頭戴式降噪耳機",
        price_hkd="HK$1,480",
        description="無線降噪耳機",
        description_english="wireless noise cancelling headphones",
    )

    evidence = _shop_offer_to_evidence(offer, details, listing, request, _Sup(), datetime.now(timezone.utc))
    assert "無線主動降噪耳機" in evidence.attributes["specifications_original"]
    assert "wireless active noise cancelling" in evidence.attributes["specifications"]
    # Chinese keywords validate against the original text end-to-end.
    result = validate_offer(request, evidence)
    check = next(c for c in result.checks if c.field == "降噪")
    assert check.status is ValidationStatus.PASS, check.reason