"""Semantic fallback (Path A: keyword checks) and semantic rerank (Path B:
listing ranking) — deterministic tests that monkeypatch the embedding layer,
so they run fast and identically with or without the model cached."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from procurement import embeddings
from procurement.catalog import OTHER_CATEGORY
from procurement.rules import validate_offer
from procurement.schemas import OfferEvidence, PurchaseRequest, RequestRequirements, ValidationStatus
from procurement.tools.suppliers import _ListingProduct


def _request(keywords: list[str]) -> PurchaseRequest:
    return PurchaseRequest(
        request_id="R",
        requester="t",
        department="d",
        category=OTHER_CATEGORY,
        quantity=1,
        max_total_cost_minor=1_000_000,
        deadline=None,
        mandatory=RequestRequirements(
            category=OTHER_CATEGORY,
            attributes={"item": "noise cancelling headphones", "keywords": keywords},
            warranty_months_min=None,
        ),
        created_at=datetime.now(timezone.utc),
    )


def _offer(specifications: str, **kwargs) -> OfferEvidence:
    defaults = dict(
        offer_id="SUP1:item--shop",
        supplier_id="SUP1",
        product_id="item--shop",
        category=OTHER_CATEGORY,
        model_name="Some Product",
        unit_price_minor=1_500,
        shipping_minor=None,
        available_quantity=None,
        attributes={"shop": "Tech Store", "specifications": specifications},
        warranty_months=None,
        delivery_by=None,
        retrieved_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return OfferEvidence(**defaults)


def _check_for(result, field: str):
    return next(c for c in result.checks if c.field == field)


# --- Path A: keyword checks ----------------------------------------------------


def test_semantic_fallback_pass_above_threshold(monkeypatch):
    monkeypatch.setattr(
        embeddings, "semantic_keyword_scores", lambda ks, text: {k: 0.72 for k in ks}
    )
    result = validate_offer(_request(["noise cancelling"]), _offer("Industry-leading ANC headphones"))
    check = _check_for(result, "noise cancelling")
    assert check.status is ValidationStatus.PASS
    assert "semantic match" in check.reason
    assert "0.72" in check.reason


def test_semantic_fallback_unknown_in_uncertain_band(monkeypatch):
    monkeypatch.setattr(
        embeddings, "semantic_keyword_scores", lambda ks, text: {k: 0.45 for k in ks}
    )
    result = validate_offer(_request(["noise cancelling"]), _offer("Bluetooth over-ear headphones"))
    check = _check_for(result, "noise cancelling")
    assert check.status is ValidationStatus.UNKNOWN
    assert "possible semantic match" in check.reason
    assert "0.45" in check.reason


def test_semantic_fallback_fail_below_threshold(monkeypatch):
    monkeypatch.setattr(
        embeddings, "semantic_keyword_scores", lambda ks, text: {k: 0.21 for k in ks}
    )
    result = validate_offer(_request(["usb transmitter"]), _offer("Sony WH-1000XM5 noise cancelling headphones"))
    check = _check_for(result, "usb transmitter")
    assert check.status is ValidationStatus.FAIL
    assert "cosine 0.21" in check.reason


def test_semantic_unavailable_fails_like_before(monkeypatch):
    monkeypatch.setattr(embeddings, "semantic_keyword_scores", lambda ks, text: None)
    result = validate_offer(_request(["noise cancelling"]), _offer("Bluetooth over-ear headphones"))
    check = _check_for(result, "noise cancelling")
    assert check.status is ValidationStatus.FAIL
    assert "cosine" not in check.reason


def test_substring_pass_skips_semantics(monkeypatch):
    """A literal hit never pays for (or depends on) the embedding layer."""
    def explode(ks, text):
        raise AssertionError("semantic layer must not be called on substring hits")

    monkeypatch.setattr(embeddings, "semantic_keyword_scores", explode)
    result = validate_offer(_request(["noise cancelling"]), _offer("noise cancelling over-ear headphones"))
    check = _check_for(result, "noise cancelling")
    assert check.status is ValidationStatus.PASS
    assert "appears in the product specifications" in check.reason


def test_semantic_scores_only_the_missing_keywords(monkeypatch):
    seen = {}

    def spy(ks, text):
        seen["keywords"] = ks
        return {k: 0.9 for k in ks}

    monkeypatch.setattr(embeddings, "semantic_keyword_scores", spy)
    request = _request(["noise cancelling", "usb transmitter"])
    result = validate_offer(
        request, _offer("noise cancelling over-ear headphones")
    )
    assert seen["keywords"] == ["usb transmitter"]
    assert _check_for(result, "noise cancelling").status is ValidationStatus.PASS
    assert _check_for(result, "usb transmitter").status is ValidationStatus.PASS


def test_semantic_fallback_cross_language(monkeypatch):
    """A Chinese keyword scored against English evidence (the translation
    dependency is dropped in the fallback path)."""
    monkeypatch.setattr(
        embeddings, "semantic_keyword_scores", lambda ks, text: {"降噪": 0.79}
    )
    result = validate_offer(_request(["降噪"]), _offer("Active noise cancelling wireless headphones"))
    check = _check_for(result, "降噪")
    assert check.status is ValidationStatus.PASS
    assert "semantic match" in check.reason


# --- Path B: listing ranking ---------------------------------------------------


def _listing_products():
    from procurement.tools.suppliers import _ListingProduct

    return [
        _ListingProduct(product_name="Cheap Earbuds", price_hkd="HK$100", description="basic earbuds"),
        _ListingProduct(
            product_name="Sony WH-1000XM4", price_hkd="HK$2,899", description="noise cancelling"
        ),
        _ListingProduct(
            product_name="Bose QC45", price_hkd="HK$1,999", description="noise cancelling"
        ),
        _ListingProduct(product_name="Studio Mic", price_hkd="HK$800", description="condenser microphone"),
    ]


def test_semantic_tiebreak_ranks_relevant_first(monkeypatch):
    """Equal substring hits order by semantic relevance, then price: the more
    expensive product wins when it is the semantically stronger match."""
    from procurement.tools.suppliers import _rank_listing

    # Sony (HK$2,899) scores higher semantically than Bose (HK$1,999), so
    # semantic relevance — not price — puts Sony first.
    monkeypatch.setattr(
        embeddings, "semantic_scores", lambda query, texts: [0.4, 0.9, 0.5, 0.1]
    )
    ranked = _rank_listing(_listing_products(), _request(["noise cancelling"]))
    assert [p.product_name for p in ranked] == [
        "Sony WH-1000XM4",
        "Bose QC45",
    ]
    # Cheap Earbuds / Studio Mic stay outside the matching filter.
    assert "Studio Mic" not in [p.product_name for p in ranked]


def test_semantic_hit_widens_match_filter(monkeypatch):
    """A paraphrased listing stays inside the deep-scrape fence."""
    from procurement.tools.suppliers import _rank_listing

    products = [
        _ListingProduct(product_name="ANC Flagship", price_hkd="HK$3,000", description="industry-leading ANC"),
        _ListingProduct(product_name="Desk Lamp", price_hkd="HK$400", description="warm LED lamp"),
    ]
    monkeypatch.setattr(
        embeddings, "semantic_scores", lambda query, texts: [0.85, 0.1]
    )
    ranked = _rank_listing(products, _request(["noise cancelling"]))
    assert [p.product_name for p in ranked] == ["ANC Flagship"]
    # Below the pass threshold the filter does not widen.
    monkeypatch.setattr(
        embeddings, "semantic_scores", lambda query, texts: [0.45, 0.1]
    )
    ranked = _rank_listing(products, _request(["noise cancelling"]))
    assert ranked == products  # no matching products: unfiltered, price order


def test_substring_matches_beat_semantics(monkeypatch):
    """Semantic relevance never overrides a literal keyword hit."""
    from procurement.tools.suppliers import _rank_listing

    products = [
        _ListingProduct(product_name="Literal Match", price_hkd="HK$900", description="noise cancelling"),
        _ListingProduct(product_name="Vague Gadget", price_hkd="HK$100", description="mystery audio device"),
    ]
    monkeypatch.setattr(
        embeddings, "semantic_scores", lambda query, texts: [0.1, 0.95]
    )
    ranked = _rank_listing(products, _request(["noise cancelling"]))
    assert ranked[0].product_name == "Literal Match"


def test_ranking_degrades_without_model(monkeypatch):
    from procurement.tools.suppliers import _rank_listing

    monkeypatch.setattr(embeddings, "semantic_scores", lambda query, texts: None)
    ranked = _rank_listing(_listing_products(), _request(["noise cancelling"]))
    assert [p.product_name for p in ranked] == [
        "Bose QC45",
        "Sony WH-1000XM4",
    ]