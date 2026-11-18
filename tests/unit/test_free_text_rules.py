"""Free-text ("other") category: rules, parsing, and research helpers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from procurement.catalog import OTHER_CATEGORY
from procurement.money import parse_hkd_minor
from procurement.parsing import ClarificationNeeded, draft_to_request
from procurement.rules import (
    compare_offers,
    validate_offer,
)
from procurement.schemas import OfferEvidence, RequestDraft, ValidationStatus
from tests.conftest import MONITOR_ATTRIBUTES  # noqa: F401 - fixture module


def _other_request(make_request, keywords: list[str], item: str = "noise cancelling headphones", **overrides):
    return make_request(
        category=OTHER_CATEGORY,
        attributes={"item": item, "keywords": keywords},
        **overrides,
    )


def _offer(**kwargs) -> OfferEvidence:
    defaults = dict(
        offer_id="SUP1:item--shop",
        supplier_id="SUP1",
        product_id="item--shop",
        category=OTHER_CATEGORY,
        model_name="Sony WH-1000XM5",
        unit_price_minor=1_500,
        shipping_minor=None,
        available_quantity=None,
        attributes={
            "shop": "Tech Store",
            "specifications": "Sony WH-1000XM5 wireless noise cancelling headphones",
        },
        warranty_months=None,
        delivery_by=None,
    )
    defaults.update(kwargs)
    from datetime import datetime, timezone

    defaults.setdefault("retrieved_at", datetime.now(timezone.utc))
    return OfferEvidence(**defaults)


# --- rules: keyword validation -------------------------------------------------


def test_keyword_present_passes(make_request):
    request = _other_request(make_request, ["noise cancelling", "wireless"])
    offer = _offer(
        attributes={"specifications": "Wireless over-ear headphones with noise cancelling, bluetooth"}
    )
    result = validate_offer(request, offer)
    keyword_checks = [c for c in result.checks if c.field in ("noise cancelling", "wireless")]
    assert all(c.status is ValidationStatus.PASS for c in keyword_checks)


def test_keyword_missing_fails(make_request):
    # A keyword from another product domain: below the semantic UNKNOWN
    # floor too, so it hard-fails even with the embedding fallback active.
    request = _other_request(make_request, ["noise cancelling", "standing desk"])
    offer = _offer()
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "standing desk")
    assert check.status is ValidationStatus.FAIL


def test_typed_categories_still_validate(make_request, make_offer):
    request = make_request()  # monitor with typed attributes
    result = validate_offer(request, make_offer())
    assert result.status is ValidationStatus.PASS


# --- rules: textual stock evidence --------------------------------------------


def test_stock_state_labels_pass(make_request):
    """The extractor's availability classification feeds rule 3.

    With no availability information at all, a product listed for sale is
    assumed in stock — the check passes, never UNKNOWN.
    """
    request = _other_request(make_request, ["noise cancelling"], quantity=1)
    for state, expected in (
        ("in_stock", ValidationStatus.PASS),
        ("low_stock", ValidationStatus.PASS),
        ("preorder", ValidationStatus.PASS),
        ("out_of_stock", ValidationStatus.FAIL),
        (None, ValidationStatus.PASS),
    ):
        attrs = {"specifications": "noise cancelling headphones"}
        if state:
            attrs["stock_state"] = state
        result = validate_offer(request, _offer(attributes=attrs))
        check = next(c for c in result.checks if c.field == "available_quantity")
        assert check.status is expected, (state, check.reason)


def test_in_stock_text_passes_quantity(make_request):
    request = _other_request(make_request, ["noise cancelling"], quantity=2)
    for status in ("現貨 In stock", "有存貨"):
        offer = _offer(
            attributes={"specifications": "noise cancelling", "stock_status": status}
        )
        result = validate_offer(request, offer)
        check = next(c for c in result.checks if c.field == "available_quantity")
        assert check.status is ValidationStatus.PASS
        assert "count not verified" in check.reason  # assumed-in-stock caveat


def test_while_stocks_last_is_not_out_of_stock(make_request):
    """'售完即止' (while stocks last) must not read as out of stock."""
    request = _other_request(make_request, ["noise cancelling"], quantity=2)
    offer = _offer(
        attributes={
            "specifications": "noise cancelling",
            "stock_status": "限時優惠 – 售完即止",
        }
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "available_quantity")
    assert check.status is ValidationStatus.PASS  # assumed in stock


def test_out_of_stock_text_fails_quantity(make_request):
    request = _other_request(make_request, ["noise cancelling"], quantity=2)
    offer = _offer(
        attributes={"specifications": "noise cancelling", "stock_status": "缺貨 out of stock"}
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "available_quantity")
    assert check.status is ValidationStatus.FAIL


def test_no_stock_evidence_assumes_in_stock(make_request):
    """A product listed for sale on the site is assumed available — the
    absence of stock information must not block the offer as UNKNOWN."""
    request = _other_request(make_request, ["noise cancelling"], quantity=2)
    offer = _offer(attributes={"specifications": "noise cancelling"})
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "available_quantity")
    assert check.status is ValidationStatus.PASS
    assert "assumed in stock" in check.reason
    assert result.status is ValidationStatus.PASS


def test_numeric_stock_still_works(make_request):
    request = _other_request(make_request, ["noise cancelling"], quantity=2)
    offer = _offer(available_quantity=1, attributes={"specifications": "noise cancelling"})
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "available_quantity")
    assert check.status is ValidationStatus.FAIL


# --- rules: shipping-unknown budget caveat ------------------------------------


def test_unknown_shipping_budgets_without_shipping(make_request):
    request = _other_request(
        make_request, ["noise cancelling"], quantity=2, max_total_minor=5_000
    )
    offer = _offer(
        unit_price_minor=2_000,
        available_quantity=10,
        attributes={"specifications": "noise cancelling", "stock_status": "in stock"},
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "verified_total")
    assert check.status is ValidationStatus.PASS
    assert "shipping unverified" in check.reason
    assert result.verified_total_minor == 4_000


def test_unknown_shipping_over_budget_fails(make_request):
    request = _other_request(
        make_request, ["noise cancelling"], quantity=2, max_total_minor=1_000
    )
    offer = _offer(
        unit_price_minor=2_000,
        available_quantity=10,
        attributes={"specifications": "noise cancelling", "stock_status": "in stock"},
    )
    result = validate_offer(request, offer)
    assert result.status is ValidationStatus.FAIL


def test_known_shipping_still_verified_exactly(make_request, make_offer):
    request = make_request()
    result = validate_offer(request, make_offer(shipping_minor=15_000))
    check = next(c for c in result.checks if c.field == "verified_total")
    assert check.status is ValidationStatus.PASS
    assert "shipping" not in check.reason


# --- parsing: "other" drafts ----------------------------------------------------


def _other_draft(**overrides) -> RequestDraft:
    defaults = dict(
        category=OTHER_CATEGORY,
        quantity=1,
        attributes={"item": "standing desk", "keywords": ["electric", "160cm"]},
        warranty_months_min=None,
        max_total_hkd="5000",
        deadline="2026-12-01",
        department="engineering",
    )
    defaults.update(overrides)
    return RequestDraft(**defaults)


def test_draft_other_becomes_request():
    request = draft_to_request(_other_draft(), request_id="R", requester="u")
    assert request.mandatory.category == OTHER_CATEGORY
    assert request.mandatory.attributes["item"] == "standing desk"
    assert request.mandatory.attributes["keywords"] == ["electric", "160cm"]


def test_draft_other_requires_item():
    with pytest.raises(ClarificationNeeded):
        draft_to_request(_other_draft(attributes={"keywords": ["x"]}), request_id="R", requester="u")


def test_draft_other_requires_keywords():
    with pytest.raises(ClarificationNeeded):
        draft_to_request(_other_draft(attributes={"item": "desk"}), request_id="R", requester="u")


def test_department_auto_assigned_from_requester_account():
    request = draft_to_request(
        _other_draft(department=None),
        request_id="R",
        requester="u",
        requester_department="Operations",
    )
    assert request.department == "Operations"


def test_stated_department_beats_requester_account():
    request = draft_to_request(
        _other_draft(department="Legal"),
        request_id="R",
        requester="u",
        requester_department="Operations",
    )
    assert request.department == "Legal"


def test_unknown_category_still_rejected():
    with pytest.raises(ClarificationNeeded):
        draft_to_request(_other_draft(category="spaceship"), request_id="R", requester="u")


# --- research helpers (pure) ----------------------------------------------------


def test_search_phrase_other():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "noise cancelling headphones", "keywords": ["bluetooth"]},
    )
    assert _search_phrase(request) == "noise cancelling headphones bluetooth"


def test_search_phrase_other_includes_brand_keyword():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "Laptop", "keywords": ["Thinkpad"]},
    )
    assert _search_phrase(request) == "Laptop Thinkpad"


def test_search_phrase_other_skips_empty_parts():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "laptop", "keywords": ["", "  "]},
    )
    assert _search_phrase(request) == "laptop"


def _make_request_helper(
    quantity: int = 1,
    max_total_minor: int = 1_000_000,
    category: str = OTHER_CATEGORY,
    attributes: dict | None = None,
    warranty_months_min: int | None = None,
):
    from datetime import date

    from procurement.schemas import PurchaseRequest, RequestRequirements

    return PurchaseRequest(
        request_id="R",
        requester="t",
        department="d",
        category=category,
        quantity=quantity,
        max_total_cost_minor=max_total_minor,
        deadline=date(2026, 12, 1),
        mandatory=RequestRequirements(
            category=category, attributes=attributes or {}, warranty_months_min=warranty_months_min
        ),
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


def test_search_phrase_typed():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category="monitor",
        attributes=MONITOR_ATTRIBUTES,
    )
    phrase = _search_phrase(request)
    assert "Monitor" in phrase
    assert "27" in phrase
    assert "2560x1440" in phrase


def test_rank_listing_prefers_keyword_matches_and_price(monkeypatch):
    from procurement import embeddings
    from procurement.tools.suppliers import _ListingProduct, _rank_listing

    # No semantic evidence (model unavailable): pure substring-then-price.
    monkeypatch.setattr(embeddings, "semantic_scores", lambda query, texts: None)
    request = _other_request(_make_request_helper, ["noise cancelling"])
    products = [
        _ListingProduct(product_name="Cheap Earbuds", price_hkd="HK$100", description=""),
        _ListingProduct(product_name="Sony WH-1000XM4", price_hkd="HK$2,899", description="noise cancelling"),
        _ListingProduct(product_name="Bose QC45", price_hkd="HK$1,999", description="noise cancelling"),
        _ListingProduct(product_name="Studio Mic", price_hkd="HK$800", description=""),
    ]
    ranked = _rank_listing(products, request)
    assert ranked[0].product_name == "Bose QC45"
    assert ranked[1].product_name == "Sony WH-1000XM4"
    assert "Studio Mic" not in [p.product_name for p in ranked]


def test_extract_months_english_and_chinese():
    from procurement.tools.suppliers import _extract_months

    assert _extract_months("12 months warranty") == 12
    assert _extract_months("保養36個月") == 36
    assert _extract_months("行貨") is None
    assert _extract_months(None) is None


def test_extract_stock_count():
    from procurement.tools.suppliers import _extract_stock_count

    assert _extract_stock_count("3件") == 3
    assert _extract_stock_count("5 units") == 5
    assert _extract_stock_count("in stock") is None
    assert _extract_stock_count(None) is None


def test_shop_offer_evidence_shape():
    from datetime import datetime, timezone

    from procurement.tools.suppliers import (
        _ListingProduct,
        _ProductDetails,
        _ShopOffer,
        _shop_offer_to_evidence,
    )

    class _Sup:
        id = 7

    offer = _ShopOffer(shop_name="Star Tech", price_hkd="HK$1,480", warranty_text="12個月保養", stock_text="現貨")
    details = _ProductDetails(specifications="Sony WH-1000XM5 noise cancelling headphones")
    listing = _ListingProduct(product_name="Sony WH-1000XM5", price_hkd="HK$1,480", description="")
    request = _other_request(_make_request_helper, ["noise cancelling"])

    evidence = _shop_offer_to_evidence(
        offer, details, listing, request, _Sup(), datetime.now(timezone.utc)
    )
    assert evidence.unit_price_minor == 148_000
    assert evidence.warranty_months == 12
    assert evidence.available_quantity is None
    assert evidence.attributes["shop"] == "Star Tech"
    assert evidence.attributes["stock_status"] == "現貨"
    assert evidence.product_key if hasattr(evidence, "product_key") else True
    assert "sony-wh-1000xm5" in evidence.product_id
    assert "--star-tech" in evidence.product_id
    assert evidence.offer_id == "7:sony-wh-1000xm5--star-tech"


# --- item-type integrity (PO-2026-0051: ThinkPad backpack) -----------------------


def test_item_type_check_fails_accessory(make_request):
    """PO-2026-0051: a 'ThinkPad Laptop Backpack' passes the 'Thinkpad'
    keyword but must fail item-type integrity — the offer is not a laptop."""
    request = _other_request(make_request, ["Thinkpad"], item="laptop")
    offer = _offer(
        model_name="Lenovo ThinkPad Laptop Backpack 商務雙肩背包 BP100",
        attributes={
            "specifications": "Lenovo ThinkPad Laptop Backpack BP100",
        },
    )
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "item_type")
    assert check.status is ValidationStatus.FAIL
    assert "accessory" in check.reason
    # The keyword itself still passes; the offer fails on item type.
    keyword = next(c for c in result.checks if c.field == "Thinkpad")
    assert keyword.status is ValidationStatus.PASS
    assert result.status is ValidationStatus.FAIL


def test_item_type_check_allows_requested_accessory(make_request):
    """Requesting a bag: 'bag' is part of the item, never a conflict."""
    request = _other_request(make_request, ["Thinkpad"], item="laptop bag")
    offer = _offer(model_name="Lenovo ThinkPad Laptop Backpack 商務雙肩背包 BP100")
    result = validate_offer(request, offer)
    assert not any(c.field == "item_type" for c in result.checks)


def test_item_type_check_genuine_product_untouched(make_request):
    """A genuine ThinkPad title never says 'laptop' — absence of the item
    word is deliberately NOT judged (three-valued logic must not block real
    products); only accessory conflicts are decidable."""
    request = _other_request(make_request, ["Thinkpad"], item="laptop")
    offer = _offer(model_name="Lenovo ThinkPad T480s 20L7S02000")
    result = validate_offer(request, offer)
    assert not any(c.field == "item_type" for c in result.checks)


def test_item_type_check_chinese_noun(make_request):
    request = _other_request(make_request, ["Thinkpad"], item="laptop")
    offer = _offer(model_name="ThinkPad 商務背包 L15")
    result = validate_offer(request, offer)
    check = next(c for c in result.checks if c.field == "item_type")
    assert check.status is ValidationStatus.FAIL


def test_is_accessory_conflict_direct():
    from procurement.rules import is_accessory_conflict

    assert is_accessory_conflict("Lenovo ThinkPad Laptop Backpack BP100", "laptop") == "backpack"
    assert is_accessory_conflict("ThinkPad 商務背包 L15", "laptop") == "背包"
    # The noun is part of the requested item: no conflict.
    assert is_accessory_conflict("ThinkPad Wireless Mouse", "wireless mouse") is None
    assert is_accessory_conflict("Laptop Sleeve 14\"", "laptop sleeve") is None
    # Nothing to compare.
    assert is_accessory_conflict("", "laptop") is None
    assert is_accessory_conflict("Anything", "") is None


def test_drop_accessories_guards_deep_scrape_fence():
    from procurement.tools.suppliers import _ListingProduct, _drop_accessories, _rank_listing

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "laptop", "keywords": ["Thinkpad"]},
    )
    candidates = [
        # The backpack matches the 'Thinkpad' keyword and is cheapest: without
        # the filter it would take the first deep-scrape slot.
        _ListingProduct(
            product_name="Lenovo ThinkPad Laptop Backpack 商務雙肩背包 BP100",
            price_hkd="HK$125",
            description="ThinkPad backpack",
        ),
        _ListingProduct(
            product_name="Lenovo ThinkPad T480s 20L7S02000",
            price_hkd="HK$5,500",
            description="ThinkPad T480s",
        ),
    ]
    ranked = _rank_listing(candidates, request)
    # Whatever the semantic tiebreak orders, the backpack is a candidate…
    assert len(ranked) == 2
    # …but it never reaches the deep-scrape fence.
    kept = _drop_accessories(ranked, request)
    assert [c.product_name for c in kept] == ["Lenovo ThinkPad T480s 20L7S02000"]


def test_drop_accessories_no_item_noop():
    from procurement.tools.suppliers import _ListingProduct, _drop_accessories

    # Typed categories carry no 'item' attribute: the filter is a no-op.
    request = _make_request_helper(category="monitor", attributes=MONITOR_ATTRIBUTES)
    candidates = [
        _ListingProduct(product_name="Laptop Backpack for Monitors", price_hkd="HK$10"),
    ]
    assert _drop_accessories(candidates, request) == candidates


# --- search phrase: LLM query + deduplicated fallback --------------------------


def test_search_phrase_prefers_llm_search_query():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={
            "item": "Apple macbook",
            "keywords": ["Apple", "Macbook Air", "512gb"],
            "search_query": "MacBook Air 512gb",
        },
    )
    assert _search_phrase(request) == "MacBook Air 512gb"


def test_search_phrase_other_dedupes_repeated_tokens():
    from procurement.tools.suppliers import _search_phrase

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "Apple macbook", "keywords": ["Apple", "Macbook Air", "512gb"]},
    )
    # The old join produced "Apple macbook Apple Macbook Air 512gb", which
    # breaks site search — duplicates must collapse, keeping first casing.
    assert _search_phrase(request) == "Apple macbook Air 512gb"


def test_draft_search_query_carried_into_request():
    draft = _other_draft(
        attributes={
            "item": "Apple macbook",
            "keywords": ["Macbook Air", "512gb"],
            "search_query": "MacBook Air 512gb",
        }
    )
    request = draft_to_request(draft, request_id="R", requester="u")
    assert request.mandatory.attributes["search_query"] == "MacBook Air 512gb"


def test_draft_without_search_query_omits_key():
    request = draft_to_request(_other_draft(), request_id="R", requester="u")
    assert "search_query" not in request.mandatory.attributes


def test_dedupe_tokens_keeps_first_casing():
    from procurement.tools.suppliers import _dedupe_tokens

    assert _dedupe_tokens("Laptop laptop Thinkpad") == "Laptop Thinkpad"
    assert _dedupe_tokens("  one  two  one ") == "one two"
    assert _dedupe_tokens("") == ""


# --- listing schema and harvest guards (PO-2026-0045) ---------------------------


def test_listing_extraction_schema_excludes_href():
    """Stagehand builds the LLM schema from the model, so an LM-extracted
    href must not even be offered: a model-invented URL reached goto and
    crashed the Price.com.hk run with 'Cannot navigate to invalid URL'."""
    from procurement.tools.suppliers import _ListingProductLLM

    schema = _ListingProductLLM.model_json_schema()
    assert "href" not in schema.get("properties", {})
    assert set(schema["properties"]) == {
        "product_name",
        "price_hkd",
        "description",
        "description_english",
    }


def test_usable_href_only_absolute_http():
    from procurement.tools.suppliers import _usable_href

    assert _usable_href("https://example.com/p/1") == "https://example.com/p/1"
    assert _usable_href("http://example.com/p/1") == "http://example.com/p/1"
    assert _usable_href("/product/ipad-air/p/123") is None  # relative
    assert _usable_href("product.php?p=123") is None
    assert _usable_href("javascript:void(0)") is None
    assert _usable_href(None) is None
    assert _usable_href("") is None


def test_harvest_js_is_a_string_expression():
    """Stagehand's page.evaluate silently returns {} for arrow-function
    literals — the harvest must be an immediately-invoked function."""
    from procurement.tools.suppliers import _PRODUCT_LINK_JS

    assert _PRODUCT_LINK_JS.startswith("(function()")
    assert _PRODUCT_LINK_JS.rstrip().endswith("})()")


def test_harvest_parses_json_string_and_slug_names(monkeypatch):
    from procurement.tools.suppliers import _harvest_product_links

    class _FakeBrowser:
        async def evaluate(self, expression):
            # Anchor text empty (fortress image tiles) — the slug is the name.
            return (
                '[{"name": "ipad air 11 m4 2026", '
                '"href": "https://www.fortress.com.hk/zh-hk/product/ipad-air-11-m4-2026/p/14077261"}]'
            )

    links = asyncio.run(_harvest_product_links(_FakeBrowser()))
    assert len(links) == 1
    assert links[0].product_name == "ipad air 11 m4 2026"
    assert links[0].href.endswith("/p/14077261")

    class _BrokenBrowser:
        async def evaluate(self, expression):
            return None

    assert asyncio.run(_harvest_product_links(_BrokenBrowser())) == []

    class _GarbageBrowser:
        async def evaluate(self, expression):
            return "not json"

    assert asyncio.run(_harvest_product_links(_GarbageBrowser())) == []


def test_match_harvest_href_disambiguates_sizes():
    """Digit-bearing tokens weigh double: an extracted 'iPad Air 11"' row
    must match the 11-inch harvested URL, never the 13-inch one."""
    from procurement.tools.suppliers import _ListingProduct, _match_harvest_href

    harvested = [
        _ListingProduct(
            product_name="ipad air 13 m4 2026",
            href="https://shop.example/p/13",
        ),
        _ListingProduct(
            product_name="ipad air 11 m4 2026",
            href="https://shop.example/p/11",
        ),
    ]
    candidate = _ListingProduct(product_name='Apple iPad Air 11" M4 Wi-Fi 256GB')
    assert _match_harvest_href(candidate, harvested) == "https://shop.example/p/11"


def test_match_harvest_href_requires_real_overlap():
    from procurement.tools.suppliers import _ListingProduct, _match_harvest_href

    harvested = [_ListingProduct(product_name="laptop", href="https://s/p/1")]
    assert _match_harvest_href(_ListingProduct(product_name="laptop"), harvested) is None
    assert _match_harvest_href(_ListingProduct(product_name="desk lamp"), harvested) is None
    assert _match_harvest_href(_ListingProduct(product_name="x"), harvested) is None


# --- retailer product pages (fortress.com.hk shape) -----------------------------


def test_retailer_product_instruction_reads_single_offer():
    from procurement.tools.suppliers import _retailer_product_instruction

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "laptop", "keywords": ["512gb"]},
    )
    instruction = _retailer_product_instruction("MacBook Air M3", request)
    assert "single retailer" in instruction
    assert "exactly one entry" in instruction
    assert "price_hkd" in instruction
    assert "stock_state" in instruction


def test_marketplace_instruction_unchanged_for_typed_categories():
    from procurement.tools.suppliers import _product_instruction

    typed = _make_request_helper(
        category="monitor",
        attributes=MONITOR_ATTRIBUTES,
    )
    instruction = _product_instruction("Dell P2725D", typed)
    assert "price-comparison" in instruction
    assert "seller offers" in instruction


def test_instructions_warn_against_sponsored_panels():
    from procurement.tools.suppliers import (
        _product_instruction,
        _retailer_product_instruction,
    )

    request = _make_request_helper(
        category=OTHER_CATEGORY,
        attributes={"item": "laptop", "keywords": ["512gb"]},
    )
    for instruction in (
        _product_instruction("ThinkPad T480s", request),
        _retailer_product_instruction("ThinkPad T480s", request),
    ):
        assert "sponsored" in instruction
        assert "different product" in instruction


# --- page-read integrity guards (PO-2026-0036 failure) -------------------------


def test_listing_model_tokens_filter_generic_sizes():
    from procurement.tools.suppliers import _listing_model_tokens

    assert _listing_model_tokens("Lenovo ThinkPad T480s 20L7S02000") == [
        "t480s",
        "20l7s02000",
    ]
    # Size words appear in any title and prove nothing.
    assert _listing_model_tokens("laptop 16GB 512GB") == []
    assert _listing_model_tokens("laptop") == []


def test_details_mention_listing_guard():
    from procurement.tools.suppliers import (
        _ListingProduct,
        _ProductDetails,
        _details_mention_listing,
    )

    listing = _ListingProduct(product_name="Lenovo ThinkPad T480s 20L7S02000")
    matching = _ProductDetails(
        specifications="Intel i7-8550U ThinkPad T480s 8GB LPDDR3"
    )
    # The panel that poisoned PO-2026-0036: a different product's spec block.
    foreign = _ProductDetails(
        specifications='AMD Ryzen 5 7430U 16GB DDR4 512GB SSD 15.6" IPS'
    )
    assert _details_mention_listing(matching, listing) is True
    assert _details_mention_listing(foreign, listing) is False
    # No model tokens to check: cannot judge, so allow.
    plain = _ListingProduct(product_name="laptop")
    assert _details_mention_listing(matching, plain) is True


def test_details_mention_listing_checks_original_language_too():
    from procurement.tools.suppliers import (
        _ListingProduct,
        _ProductDetails,
        _details_mention_listing,
    )

    listing = _ListingProduct(product_name="Lenovo ThinkPad X270 20HN0041HH")
    details = _ProductDetails(
        specifications="12.5-inch business laptop",
        specifications_original="Lenovo ThinkPad X270 (20HN0041HH) 商用筆記簿型電腦",
    )
    assert _details_mention_listing(details, listing) is True


def test_drop_cross_product_duplicates():
    from procurement.tools.suppliers import _drop_cross_product_duplicates

    poisoned_a = _offer(offer_id="a", product_id="a")
    poisoned_b = _offer(offer_id="b", product_id="b")
    genuine = _offer(offer_id="g", product_id="g")

    entries = [
        # The same panel read for two different products: drop both.
        (("shop", "4299", "specs"), "Lenovo ThinkPad T480s 20L7S02000", poisoned_a),
        (("shop", "4299", "specs"), "Lenovo ThinkPad X270 20HN0041HH", poisoned_b),
        # Unique page content for one product: kept.
        (("other-shop", "100", "other specs"), "Lenovo ThinkPad T480s 20L7S02000", genuine),
    ]
    kept = _drop_cross_product_duplicates(entries)
    assert [e.product_id for e in kept] == ["g"]


def test_drop_cross_product_duplicates_keeps_same_product_repeats():
    from procurement.tools.suppliers import _drop_cross_product_duplicates

    first = _offer(offer_id="s1", product_id="s1")
    repeat = _offer(offer_id="s2", product_id="s2")
    # Duplicate search listings for the SAME product collapse at upsert;
    # the guard must not treat them as cross-product contamination.
    entries = [
        (("sig",), "Lenovo ThinkPad T480s 20L7S02000", first),
        (("sig",), "Lenovo ThinkPad T480s 20L7S02000", repeat),
    ]
    assert len(_drop_cross_product_duplicates(entries)) == 2


def test_mentions_listing_tokens():
    from procurement.tools.suppliers import _ListingProduct, _mentions_listing_tokens

    listing = _ListingProduct(product_name="Lenovo ThinkPad T480s 20L7S02000")
    assert _mentions_listing_tokens("Lenovo ThinkPad T480s 價格比較", listing) is True
    assert _mentions_listing_tokens("ASUS Vivobook 網上商店", listing) is False
    # No model tokens in the listing: cannot judge, so anything passes.
    plain = _ListingProduct(product_name="laptop")
    assert _mentions_listing_tokens("anything at all", plain) is True


def test_shop_offer_evidence_drops_unverified_specs():
    from datetime import datetime, timezone

    from procurement.tools.suppliers import (
        _ListingProduct,
        _ProductDetails,
        _ShopOffer,
        _shop_offer_to_evidence,
    )

    class _Sup:
        id = 7

    offer = _ShopOffer(shop_name="Star Tech", price_hkd="HK$1,480", stock_text="現貨")
    # A sponsored panel's spec block — mentions nothing of this listing.
    details = _ProductDetails(
        specifications="AMD Ryzen 5 7430U 16GB DDR4 512GB SSD 15.6\""
    )
    listing = _ListingProduct(
        product_name="Lenovo ThinkPad T480s 20L7S02000",
        description="business laptop",
    )
    request = _other_request(_make_request_helper, ["thinkpad"])

    evidence = _shop_offer_to_evidence(
        offer,
        details,
        listing,
        request,
        _Sup(),
        datetime.now(timezone.utc),
        trust_specs=False,
    )
    # The offer stands (landing verified), but the panel's specs never reach
    # the evidence — only the listing's own text does.
    assert evidence.unit_price_minor == 148_000
    specs = evidence.attributes["specifications"]
    assert "AMD Ryzen" not in specs
    assert "ThinkPad T480s" in specs
    assert "business laptop" in specs
    assert evidence.attributes["spec_source"].startswith("unverified")