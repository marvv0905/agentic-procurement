"""Supplier research tool — Stagehand-driven browsing (agent blackbox).

Two supplier shapes are supported:

- **Catalog sites** (no ``catalog_url``): one page lists every product with
  its commercial terms (the mock world). Extract once, fill shipping gaps from
  the terms page.
- **Aggregator sites** (``catalog_url`` set, e.g. price.com.hk): search for
  the request, then deep-scrape a *bounded* set of product pages for per-shop
  offers. The fence: at most ``MAX_PRODUCT_PAGES`` product pages, at most
  ``MAX_OFFERS_PER_PRODUCT`` offers each, ``MAX_RESEARCH_SECONDS`` and
  ``MAX_ACTIONS_PER_SUPPLIER`` overall. Within the fence, the deterministic
  rules engine picks the best compliant offer.

Unknown facts stay ``None`` (three-valued logic) — never zero or invented
defaults. Navigation is deterministic (DOM link clicks); the LLM only reads
pages. Research can never access portal submission.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from .. import embeddings
from ..catalog import BOOL, CATEGORIES, INT, LIST, OTHER_CATEGORY
from ..money import parse_hkd_minor
from ..schemas import (
    ComparisonResult,
    EligibilityResult,
    OfferEvidence,
    PurchaseRequest,
)

#: Deep-scrape fence per supplier.
MAX_PRODUCT_PAGES = 6
MAX_OFFERS_PER_PRODUCT = 5
MAX_RESEARCH_SECONDS = 120
MAX_ACTIONS_PER_SUPPLIER = 25


# ---------------------------------------------------------------------------
# Extraction schemas
# ---------------------------------------------------------------------------


class _ScrapedProduct(BaseModel):
    """One catalog row. ``None`` means "not extractable", never a default."""

    product_id: str | None = None
    model_name: str | None = None
    unit_price_hkd: str | None = None
    available_units: int | None = None
    shipping_hkd: str | None = None
    warranty_months: int | None = None
    delivery_business_days: int | None = None
    attributes: dict[str, Any] | None = None
    unknown_fields: dict[str, str] = Field(default_factory=dict)


class _CatalogPage(BaseModel):
    products: list[_ScrapedProduct] = Field(default_factory=list)


class _ListingProduct(BaseModel):
    product_name: str
    price_hkd: str | None = None
    description: str | None = None
    #: The description translated into English when the site is in another
    #: language — ranking matches request keywords against both texts.
    description_english: str | None = None
    #: Set only for DOM-harvested candidates (sites whose search results the
    #: listing extraction cannot read): the product page's URL, opened with
    #: ``goto`` instead of a title-based link click. NEVER set by the listing
    #: extraction — an LLM-invented URL is not a navigation target.
    href: str | None = None


class _ListingPage(BaseModel):
    products: list[_ListingProduct] = Field(default_factory=list)


class _ListingProductLLM(BaseModel):
    """LLM-facing listing row — deliberately excludes ``href``.

    Stagehand derives the extraction schema from this model, so any field
    declared here is a field the model is invited to fill. A model-invented
    ``href`` once reached ``goto`` and crashed the whole supplier run
    (PO-2026-0045: 'Cannot navigate to invalid URL') — navigation targets
    come only from the deterministic DOM harvest.
    """

    product_name: str
    price_hkd: str | None = None
    description: str | None = None
    description_english: str | None = None


class _ListingPageLLM(BaseModel):
    products: list[_ListingProductLLM] = Field(default_factory=list)


class _ShopOffer(BaseModel):
    shop_name: str | None = None
    price_hkd: str | None = None
    warranty_text: str | None = None
    stock_text: str | None = None
    #: Availability classified by the extractor into a fixed vocabulary; the
    #: rules engine consumes the label, the raw text is kept for humans.
    stock_state: str | None = None


class _ProductDetails(BaseModel):
    specifications: str | None = None
    #: The same spec text verbatim in the page's original language — kept so
    #: Chinese keywords (降噪) can match untranslated evidence.
    specifications_original: str | None = None
    #: Typed category attributes extracted from the spec text, normalized to
    #: the catalog's canonical values ("FHD"/"1920X1080" -> "1920x1080").
    attributes: dict[str, Any] | None = None
    offers: list[_ShopOffer] = Field(default_factory=list)


class _ShippingTerms(BaseModel):
    standard_shipping_fee_hkd: str | None = None
    delivery_business_days: int | None = None


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or "item"


def _dedupe_tokens(phrase: str) -> str:
    """Case-insensitive token dedup, keeping each word's first occurrence.

    Item descriptions and keyword lists overlap ("Apple macbook" +
    "Macbook Air"), and naive joins produce queries like "Apple macbook Apple
    Macbook Air 512gb" that break site search.
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in phrase.split():
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return " ".join(out)


def _search_phrase(request: PurchaseRequest) -> str:
    """The natural-language phrase used to search an aggregator site.

    For free-text requests the intake LLM distills a ready-to-type
    ``search_query``; it is used verbatim. Without one (wizard-created
    orders) the item description and required keywords join, with duplicate
    words removed — brand/model terms ("Thinkpad") still reach the site's
    search box, and the keyword rules still reject non-matching offers
    downstream, so the query only decides what gets *found*.
    """
    req = request.mandatory
    if req.category == OTHER_CATEGORY:
        query = str(req.attributes.get("search_query") or "").strip()
        if query:
            return query
        parts = [str(req.attributes.get("item") or "").strip()]
        parts.extend(
            str(k).strip()
            for k in (req.attributes.get("keywords") or [])
            if str(k).strip()
        )
        return _dedupe_tokens(" ".join(p for p in parts if p))
    spec = CATEGORIES.get(req.category)
    parts = [spec.label] if spec else [req.category]
    for key, value in (req.attributes or {}).items():
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value)
        elif value is not None:
            parts.append(str(value))
    return _dedupe_tokens(" ".join(parts))


def _search_phrases(request: PurchaseRequest) -> list[str]:
    """Search phrases in fallback order (deduped, first-wins).

    Free-text requests use their single distilled query verbatim. Typed
    categories fall back from the full English phrase to the Traditional
    Chinese label (HK aggregators index product titles in Chinese) and then
    to the bare English label. The query only decides what gets *found* —
    the deterministic rules still validate every offer downstream.
    """
    req = request.mandatory
    if req.category == OTHER_CATEGORY:
        return [p for p in [_search_phrase(request)] if p]

    spec = CATEGORIES.get(req.category)
    phrases: list[str] = [_search_phrase(request)]
    if spec is not None and spec.label_zh:
        parts = [spec.label_zh]
        for value in (req.attributes or {}).values():
            if isinstance(value, (list, tuple)):
                parts.extend(str(v) for v in value)
            elif value is not None:
                parts.append(str(value))
        phrases.append(_dedupe_tokens(" ".join(parts)))
    if spec is not None:
        phrases.append(spec.label)

    seen: set[str] = set()
    out: list[str] = []
    for phrase in phrases:
        key = phrase.lower()
        if phrase and key not in seen:
            seen.add(key)
            out.append(phrase)
    return out


def _request_keywords(request: PurchaseRequest) -> list[str]:
    """Deterministic keywords used to rank search results."""
    req = request.mandatory
    keywords: list[str] = []
    if req.category == OTHER_CATEGORY:
        keywords = [str(k) for k in (req.attributes.get("keywords") or [])]
    else:
        for value in (req.attributes or {}).values():
            if isinstance(value, (list, tuple)):
                keywords.extend(str(v) for v in value)
            elif value is not None:
                keywords.append(str(value))
    return [k.strip().lower() for k in keywords if k and k.strip()]


def _rank_listing(
    products: list[_ListingProduct], request: PurchaseRequest
) -> list[_ListingProduct]:
    """Deterministic ranking: keyword matches first, semantic relevance next,
    then lowest price.

    Keyword hits are counted against the listing text in BOTH languages —
    the site's original (product names are often local-language) and the
    extracted English translation — so request keywords in either language
    rank matching products first.

    Semantic rerank (Lab 3): the listing candidates fight for the scarce
    deep-scrape budget (``MAX_PRODUCT_PAGES`` pages), so a product that
    paraphrases or translates the request ("ANC" vs "noise cancelling",
    主動降噪 vs "active noise cancellation") must not fall behind on a
    missing substring. Each candidate is scored by local multilingual
    sentence embeddings against the request's search phrase (the bi-encoder
    retrieval step; the deep-scrape + rule validation that follows is the
    cross-encoder stage). The semantic score only breaks ties between equal
    substring-match counts and widens the matching filter — it never
    overrides a literal keyword hit. When the embedding model is unavailable
    the scores collapse to 0 and the ranking is exactly the substring-only
    behavior.
    """
    keywords = _request_keywords(request)

    def text_of(item: _ListingProduct) -> str:
        return " ".join(
            part
            for part in (item.product_name, item.description, item.description_english)
            if part
        ).lower()

    # Bi-encoder relevance: one batched pass, the search phrase against
    # every candidate's text (max cosine over sentence chunks).
    texts = [text_of(p) for p in products]
    sem_list = embeddings.semantic_scores(_search_phrase(request), texts)
    sem: dict[int, float] = {
        id(product): (sem_list[index] if sem_list is not None else 0.0)
        for index, product in enumerate(products)
    }

    def score(item: _ListingProduct) -> tuple[int, float, int]:
        text = text_of(item)
        matches = sum(1 for k in keywords if k in text)
        price = parse_hkd_minor(item.price_hkd)
        return (-matches, -sem[id(item)], price if price is not None else 10**12)

    ranked = sorted(products, key=score)
    if keywords:
        def relevant(item: _ListingProduct) -> bool:
            if any(k in text_of(item) for k in keywords):
                return True
            # A strong semantic hit also counts as matching: paraphrased or
            # translated listings stay inside the deep-scrape fence.
            return sem[id(item)] >= embeddings.SEMANTIC_PASS_THRESHOLD

        matching = [p for p in ranked if relevant(p)]
        if matching:
            return matching
    return ranked


def _extract_months(text: str | None) -> int | None:
    """Parse an explicit warranty duration; never invent a default."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*(個月|个月|months?|mo\b)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_stock_count(text: str | None) -> int | None:
    """Parse an explicit stock count, e.g. '3件' or '5 units'."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*(件|部|個|units?\b|pcs)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Catalog-page flow (mock world)
# ---------------------------------------------------------------------------


def _catalog_instruction(request: PurchaseRequest, supplier_name: str) -> str:
    req = request.mandatory
    if req.category == OTHER_CATEGORY:
        item = req.attributes.get("item", "")
        return (
            f"Extract every product listed on this {supplier_name} catalogue "
            f"page that matches: {item}. For each product return: product_id "
            "(from the product link URL if visible), model_name, "
            "unit_price_hkd (the unit price exactly as shown), available_units "
            "(units in stock), shipping_hkd (the shipping fee; set to null if "
            "not shown), warranty_months (warranty in months, null if not "
            "specified), delivery_business_days (delivery time in business "
            "days, null if not specified), and attributes as an object with "
            "'specifications' (the full specifications text shown for the "
            "product, translated into English if shown in another language) "
            "and 'stock_status' (the stock/availability text, null if not "
            "shown). If nothing matches or this is not a catalogue "
            "page, return an empty list."
        )
    spec = CATEGORIES.get(req.category)
    attr_names = [a.display_name for a in spec.attributes] if spec else []
    attr_hint = ", ".join(attr_names) if attr_names else "the listed specifications"
    label = spec.label if spec else req.category
    return (
        f"Extract every {label} product listed on this {supplier_name} catalogue "
        "page. For each product return: product_id (from the product link URL if "
        "visible), model_name, unit_price_hkd (the unit price exactly as shown), "
        "available_units (units in stock), shipping_hkd (the shipping fee; set to "
        "null if the page says to see the terms page or does not show one), "
        "warranty_months (warranty in months, null if not specified), "
        "delivery_business_days (delivery time in business days, null if not "
        f"specified), and attributes (an object with: {attr_hint}; use null for "
        "anything not stated). Only include products that are "
        f"{label.lower()}s. If the catalogue is empty or not a catalogue page, "
        "return an empty list."
    )


def _coerce_attributes(category: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Map extracted attributes back onto the catalog's typed keys.

    The extraction prompt uses display names ("screen size (inches)"), but the
    rules engine compares by catalog key and type. Strings are coerced: int
    attrs to int, bool attrs to Yes/No booleans, list attrs to lists.
    Free-text ("other") requests keep the extracted attributes verbatim.
    """
    spec = CATEGORIES.get(category)
    raw = raw or {}
    out: dict[str, Any] = {}
    if spec is None:
        # Free-text category: keep exactly what the browser showed.
        return {k: v for k, v in raw.items() if v is not None}
    display_to_name = {a.display_name: a.name for a in spec.attributes}
    for attr in spec.attributes:
        value = raw.get(attr.name, raw.get(attr.display_name))
        if value is None or value == "":
            continue
        if attr.type == INT:
            try:
                out[attr.name] = int(str(value).strip())
            except (TypeError, ValueError):
                continue
        elif attr.type == BOOL:
            if isinstance(value, bool):
                out[attr.name] = value
            else:
                out[attr.name] = str(value).strip().lower() in ("yes", "true", "y")
        elif attr.type == LIST:
            if isinstance(value, (list, tuple)):
                out[attr.name] = [str(v).strip() for v in value]
            elif isinstance(value, str):
                out[attr.name] = [v.strip() for v in value.split(",") if v.strip()]
            else:
                out[attr.name] = [str(value)]
        else:
            out[attr.name] = str(value).strip()
    return out


def _to_evidence(
    scraped: _ScrapedProduct,
    *,
    supplier_id: str,
    category: str,
    shipping_minor_fallback: int | None,
    retrieved_at,
) -> OfferEvidence | None:
    """Convert a scraped row into an OfferEvidence, keeping unknowns unknown."""
    if scraped.model_name is None and scraped.product_id is None:
        return None

    product_key = scraped.product_id or (scraped.model_name or "product").strip()

    unit_price_minor = parse_hkd_minor(scraped.unit_price_hkd)
    shipping_minor = parse_hkd_minor(scraped.shipping_hkd)
    if shipping_minor is None:
        shipping_minor = shipping_minor_fallback

    unknown_fields: dict[str, str] = dict(scraped.unknown_fields)
    if unit_price_minor is None:
        unknown_fields["unit_price_minor"] = "price not extractable from the catalog"
    if shipping_minor is None:
        unknown_fields["shipping_minor"] = "shipping not shown on the catalog"

    delivery_by: date | None = None
    if scraped.delivery_business_days is not None:
        delivery_by = retrieved_at.date() + timedelta(
            days=int(scraped.delivery_business_days)
        )

    return OfferEvidence(
        offer_id=f"{supplier_id}:{product_key}",
        supplier_id=supplier_id,
        product_id=product_key,
        category=category,
        model_name=scraped.model_name,
        unit_price_minor=unit_price_minor,
        available_quantity=scraped.available_units,
        shipping_minor=shipping_minor,
        attributes=_coerce_attributes(category, scraped.attributes),
        warranty_months=scraped.warranty_months,
        delivery_by=delivery_by,
        retrieved_at=retrieved_at,
        unknown_fields=unknown_fields,
    )


async def _research_catalog_page(browser, request: PurchaseRequest, supplier) -> list[OfferEvidence]:
    """Mock-world flow: one catalog page carries every product and term."""
    category = request.mandatory.category
    retrieved_at = datetime.now(timezone.utc)
    actions = 0

    await browser.goto(supplier.origin)
    actions += 1

    # Reach a catalogue that lists products: deterministic DOM click first,
    # LLM act as fallback for live layouts.
    if not await browser.click_link_containing(supplier.name):
        try:
            await browser.act(
                f"click the link that leads to the catalogue or shop page for "
                f"{supplier.name}"
            )
            actions += 1
        except Exception:
            pass  # already on a catalogue-like page

    try:
        page = await browser.extract(
            _catalog_instruction(request, supplier.name), _CatalogPage
        )
    except Exception:
        return []

    scraped_rows = page.products if page else []

    shipping_minor_fallback: int | None = None
    needs_terms = any(
        s.shipping_hkd is None or parse_hkd_minor(s.shipping_hkd or "") is None
        for s in scraped_rows
        if category != OTHER_CATEGORY
    )
    if scraped_rows and needs_terms and actions < MAX_ACTIONS_PER_SUPPLIER:
        try:
            await browser.act("open the shipping, delivery and returns terms page")
            terms = await browser.extract(
                "extract the standard shipping fee and the delivery time in "
                "business days from this terms page",
                _ShippingTerms,
            )
            shipping_minor_fallback = parse_hkd_minor(terms.standard_shipping_fee_hkd)
        except Exception:
            shipping_minor_fallback = None

    return [
        evidence
        for s in scraped_rows
        if (
            evidence := _to_evidence(
                s,
                supplier_id=str(supplier.id),
                category=category,
                shipping_minor_fallback=shipping_minor_fallback,
                retrieved_at=retrieved_at,
            )
        )
        is not None
    ]


# ---------------------------------------------------------------------------
# Aggregator deep-scrape flow (price.com.hk style)
# ---------------------------------------------------------------------------


def _listing_instruction(phrase: str) -> str:
    return (
        f"These are shopping search results for '{phrase}'. List up to 15 "
        "products that are relevant to that search. For each product return: "
        "product_name (the exact product title shown), price_hkd (the lowest "
        "price displayed for it, exactly as shown), description (the "
        "specifications or summary text exactly as shown in the result row), "
        "and description_english (the same description translated into English "
        "if it is in another language — identical to 'description' when the "
        "row is already in English). Ignore ads, banner deals and unrelated "
        "categories."
    )


def _typed_attribute_hints(category: str) -> str:
    """Extraction guidance per catalog attribute, with normalization rules.

    Only typed categories use this; "other" keeps the free specifications
    text. Values are normalized to the catalog's canonical choices so the
    deterministic rules engine can compare them exactly.
    """
    spec = CATEGORIES.get(category)
    if spec is None:
        return ""
    lines = []
    for attr in spec.attributes:
        if attr.type == INT:
            lines.append(
                f"- '{attr.display_name}': the number as an integer "
                f"(e.g. 27); null if not stated"
            )
        elif attr.type == BOOL:
            lines.append(
                f"- '{attr.display_name}': true or false; null if not stated"
            )
        elif attr.type == LIST:
            lines.append(
                f"- '{attr.display_name}': a list of the values that appear, "
                f"chosen from {list(attr.choices)} (e.g. 'HDMI x1, DP' -> "
                "[\"HDMI\", \"DisplayPort\"]); null if not stated"
            )
        elif attr.choices:
            lines.append(
                f"- '{attr.display_name}': normalize to exactly one of "
                f"{list(attr.choices)} (e.g. FHD/1080p/1920X1080 -> "
                "\"1920x1080\"); null if not stated"
            )
        else:
            lines.append(f"- '{attr.display_name}': the stated value; null if not stated")
    return "\n".join(lines)


def _attributes_part(request: PurchaseRequest) -> str:
    """Shared instruction preamble for the product's typed attributes."""
    hints = _typed_attribute_hints(request.mandatory.category)
    if not hints:
        return ""
    return (
        "First, 'attributes': an object describing THIS product, "
        "reading its specification text, with these keys (normalize "
        "variants to the canonical values; never guess a value the page "
        "does not state):\n"
        f"{hints}\n"
    )


#: Shared instruction part: the spec text in both languages.
_SPECIFICATIONS_PART = (
    "Next, 'specifications': the product's specification text "
    "shown on this page as one string — TRANSLATE it into English if it is "
    "in another language (keep model names, brands and numbers exactly as "
    "shown; translate feature terms like 降噪 to 'noise cancelling'). "
    "Then 'specifications_original': the same specification text exactly "
    "as shown on the page in its ORIGINAL language (identical to "
    "'specifications' when the page is already in English; never translate "
    "this one). "
)

#: Shared instruction part: read only THIS product. Product pages carry
#: sponsored/featured/related panels about *other* products; reading them
#: attributes the wrong price and specs to the page's subject (observed on
#: price.com.hk, where one shop's panel got credited to four ThinkPads).
_PANEL_WARNING = (
    "Read ONLY the product named above: it is the product whose name "
    "appears in the page's title. Ignore sponsored, featured, recommended "
    "and related-product panels anywhere on the page — a block that "
    "describes a different product, even with its own price and buy "
    "buttons, is a panel. Every specification and every offer you return "
    "must belong to the page's own product; the spec text you return must "
    "be the block that describes that product, not another product's "
    "advertising. If something on the page belongs to a different product, "
    "do not return it. "
)


def _product_instruction(product_name: str, request: PurchaseRequest) -> str:
    attributes_part = _attributes_part(request)
    return (
        f"This is a price-comparison page for: {product_name}. Extract "
        + ("three" if attributes_part else "two")
        + " things. "
        + attributes_part
        + _SPECIFICATIONS_PART
        + "Finally, 'offers': a list of up to "
        f"{MAX_OFFERS_PER_PRODUCT} seller offers shown on this page, each with "
        "shop_name (the seller/shop name), price_hkd (the offer price exactly "
        "as shown), warranty_text (warranty or goods-type info, e.g. 行貨/水貨, "
        "null if not shown), stock_text (the stock/availability text exactly as "
        "shown, null if not shown), and stock_state: classify the availability "
        "as exactly one of 'in_stock', 'low_stock', 'out_of_stock', 'preorder' "
        "(orderable, arrives in days) or null when the page shows no "
        "availability information. Do not include offers without a price. "
        + _PANEL_WARNING
    )


def _retailer_product_instruction(product_name: str, request: PurchaseRequest) -> str:
    """Variant for single-retailer product pages (e.g. fortress.com.hk).

    Marketplace pages carry a list of shop offers, which the standard
    instruction extracts. A retailer's own product page has exactly one
    offer — the retailer's — so when the standard read finds none, this
    instruction reads the page that way instead.
    """
    attributes_part = _attributes_part(request)
    return (
        f"This is a single retailer's product page for: {product_name}. "
        "Extract three things. "
        + attributes_part
        + _SPECIFICATIONS_PART
        + "Finally, 'offers': a list with exactly one entry — the retailer's "
        "own offer on this page: shop_name (the retailer's name, e.g. the "
        "site name), price_hkd (the product price exactly as shown; null if "
        "not shown), warranty_text (warranty or goods-type info, null if "
        "not shown), stock_text (the stock/availability text exactly as "
        "shown, null if not shown), and stock_state: classify the "
        "availability as exactly one of 'in_stock', 'low_stock', "
        "'out_of_stock', 'preorder' or null when the page shows no "
        "availability information. If the page shows no price at all, "
        "return an empty offers list. "
        + _PANEL_WARNING
    )


def _shop_offer_to_evidence(
    offer: _ShopOffer,
    details: _ProductDetails,
    listing: _ListingProduct,
    request: PurchaseRequest,
    supplier,
    retrieved_at,
    trust_specs: bool = True,
) -> OfferEvidence | None:
    """One extracted shop offer as OfferEvidence.

    ``trust_specs=False`` when the extracted spec block doesn't mention this
    product (it read like a panel): the seller-offer list still stands — on a
    landing-verified page the offers are the product's own — but the page's
    specs and typed attributes are dropped to *unknown* rather than trusted,
    so keyword checks can never pass on a stranger's spec text.
    """
    unit_price_minor = parse_hkd_minor(offer.price_hkd)
    if unit_price_minor is None:
        return None

    category = request.mandatory.category
    shop = (offer.shop_name or "shop").strip()
    product_key = f"{_slug(listing.product_name)}--{_slug(shop)}"

    specifications = " ".join(
        part
        for part in (
            details.specifications if trust_specs else None,
            listing.description_english or listing.description,
            listing.product_name,
        )
        if part
    )
    # The same evidence in the page's original language, so keywords entered
    # in Traditional Chinese (降噪) match untranslated text.
    specifications_original = " ".join(
        part
        for part in (
            details.specifications_original if trust_specs else None,
            listing.description,
            listing.product_name,
        )
        if part
    )
    attributes: dict[str, Any] = {}
    # Typed specs from the product page, coerced to the catalog's keys so the
    # deterministic rules can compare them (unknowns simply stay absent).
    if trust_specs:
        attributes.update(_coerce_attributes(category, details.attributes))
    else:
        attributes["spec_source"] = "unverified: page spec block did not mention this product"
    # Human-readable context and availability, always kept.
    attributes["shop"] = shop
    attributes["specifications"] = specifications
    if specifications_original:
        attributes["specifications_original"] = specifications_original
    if offer.stock_text:
        attributes["stock_status"] = offer.stock_text
    if offer.stock_state:
        attributes["stock_state"] = offer.stock_state

    unknown_fields: dict[str, str] = {}
    if offer.stock_text is None:
        unknown_fields["stock_status"] = "no availability text on the offer"
    unknown_fields["shipping_minor"] = "aggregator offers do not list shipping"

    return OfferEvidence(
        offer_id=f"{supplier.id}:{product_key}",
        supplier_id=str(supplier.id),
        product_id=product_key,
        category=category,
        model_name=listing.product_name[:120],
        unit_price_minor=unit_price_minor,
        available_quantity=_extract_stock_count(offer.stock_text),
        shipping_minor=None,
        attributes=attributes,
        warranty_months=_extract_months(offer.warranty_text),
        delivery_by=None,
        retrieved_at=retrieved_at,
        unknown_fields=unknown_fields,
    )


async def _read_product_details(
    browser, item: _ListingProduct, request: PurchaseRequest
) -> _ProductDetails | None:
    """Read one product page, adapting to the site's shape.

    Marketplace pages (price.com.hk) list seller offers, which the standard
    instruction extracts. Single-retailer pages (fortress.com.hk) have no
    "offers" — just the retailer's own price — so when the standard read
    finds none, one retry reads the page as the retailer's own offer.
    Deterministic control flow; the model only reads the page.
    """
    try:
        details = await browser.extract(
            _product_instruction(item.product_name, request), _ProductDetails
        )
    except Exception:
        details = None
    if details is not None and details.offers:
        return details
    try:
        retry = await browser.extract(
            _retailer_product_instruction(item.product_name, request),
            _ProductDetails,
        )
    except Exception:
        return details
    if retry is not None and retry.offers:
        return retry
    return details


#: DOM fallback for search results that render as product cards the listing
#: extraction cannot read (fortress.com.hk): collect same-site anchors whose
#: URLs look like product pages. Best effort — a site with no such anchors
#: simply yields no candidates.
#:
#: Two hard-won constraints on the expression's shape:
#: - Stagehand's ``page.evaluate`` only evaluates STRING expressions; an
#:   arrow-function expression silently returns ``{}``, so this must be an
#:   immediately-invoked function serialized as one string.
#: - Card tiles often wrap images only (empty ``textContent``), so the name
#:   falls back to the product slug in the URL — a deterministic source.
_PRODUCT_LINK_JS = """(function() {
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll('a[href]')) {
        const href = a.href || '';
        if (!href.startsWith(location.origin)) continue;
        if (seen.has(href)) continue;
        if (!/(product|item|detail|\\/p\\/)/i.test(href)) continue;
        if (/(search|categor|filter|compare|cart)/i.test(href)) continue;
        seen.add(href);
        let name = (a.textContent || '').trim();
        if (!name) {
            try {
                const segs = new URL(href).pathname.split('/').filter(Boolean);
                for (let i = 0; i < segs.length; i++) {
                    if ((segs[i] === 'product' || segs[i] === 'item' || segs[i] === 'detail') && segs[i + 1]) {
                        name = segs[i + 1];
                        break;
                    }
                    if (segs[i] === 'p' && segs[i - 1]) {
                        name = segs[i - 1];
                        break;
                    }
                }
                if (name) name = decodeURIComponent(name).replace(/[-_]+/g, ' ');
            } catch (e) {}
        }
        if (!name) continue;
        out.push({ name: name.slice(0, 120), href: href });
        if (out.length >= 20) break;
    }
    return JSON.stringify(out);
})()"""


async def _harvest_product_links(browser) -> list[_ListingProduct]:
    """Harvest product-page anchors from the search results DOM."""
    try:
        raw = await browser.evaluate(_PRODUCT_LINK_JS)
    except Exception:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return [
        _ListingProduct(product_name=link["name"], href=link["href"])
        for link in (raw or [])
        if isinstance(link, dict) and link.get("name") and link.get("href")
    ]


def _usable_href(href: str | None) -> str | None:
    """An absolute http(s) URL, or ``None``.

    Only the DOM harvest produces navigation targets (absolute URLs read
    from ``a.href``); anything else is treated as no href so the flow falls
    back to the title-based click path instead of attempting an invalid
    navigation (which crashed a whole supplier run as an RPCError).
    """
    if href and href.startswith(("http://", "https://")):
        return href
    return None


_NAME_TOKEN_SPLIT = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def _name_tokens(text: str) -> set[str]:
    return {t for t in _NAME_TOKEN_SPLIT.split((text or "").lower()) if len(t) >= 2}


def _match_harvest_href(
    candidate: _ListingProduct, harvested: list[_ListingProduct]
) -> str | None:
    """Harvested product URL matching an extracted listing row, or ``None``.

    Card-based sites (fortress.com.hk) render result rows as image tiles: the
    extracted title is not link text, so the title-click path cannot open
    them. Match the row against harvested product URLs by shared name
    tokens, with digit-bearing tokens (sizes, model numbers) weighted
    double so 'iPad Air 11"' does not match 'iPad Air 13"'. Requires a real
    overlap — a single shared word never matches.
    """
    cand_tokens = _name_tokens(candidate.product_name)
    if not cand_tokens:
        return None
    cand_digits = {t for t in cand_tokens if any(ch.isdigit() for ch in t)}
    best: str | None = None
    best_score = 0
    for link in harvested:
        link_tokens = _name_tokens(link.product_name)
        if not link_tokens:
            continue
        overlap = cand_tokens & link_tokens
        if not overlap:
            continue
        digit_overlap = cand_digits & link_tokens
        score = len(overlap) + 2 * len(digit_overlap)
        if score > best_score:
            best = _usable_href(link.href)
            best_score = score
    if best_score >= 3:
        return best
    return None


# ---------------------------------------------------------------------------
# Page-read integrity guards (deterministic; the LLM only reads pages)
# ---------------------------------------------------------------------------

#: Size/capacity tokens that appear in any product's title and prove nothing
#: ("512gb" is a requirement, not a model number).
_GENERIC_TOKEN = re.compile(r"^\d+(gb|tb|hz|w|wh|mah|kg|g|bit|inch)$")


def _listing_model_tokens(product_name: str) -> list[str]:
    """Digit-bearing tokens of a listing title — its model fingerprint.

    'Lenovo ThinkPad T480s 20L7S02000' -> ['t480s', '20l7s02000']; generic
    size tokens ('16gb') are excluded, and titles without digit tokens have
    no fingerprint at all.
    """
    tokens = re.findall(r"[a-z0-9]*\d+[a-z0-9]*", (product_name or "").lower())
    return [t for t in tokens if len(t) >= 3 and not _GENERIC_TOKEN.match(t)]


def _mentions_listing_tokens(text: str, item: _ListingProduct) -> bool:
    """Does ``text`` mention a model token of the listing's title?

    Listings without model tokens cannot be judged and pass.
    """
    tokens = _listing_model_tokens(item.product_name)
    if not tokens:
        return True
    return any(token in (text or "").lower() for token in tokens)


def _details_mention_listing(details: _ProductDetails, item: _ListingProduct) -> bool:
    """Guard against reading a page-level panel instead of this product.

    The extracted details must mention at least one model token of the
    listing's title. If they don't, the spec block read was about something
    else on the page (a sponsored or featured panel) and its specs and
    typed attributes cannot be trusted for this product. Deterministic:
    the LLM never sees this check.
    """
    haystack = " ".join(
        part
        for part in (
            details.specifications,
            details.specifications_original,
            json.dumps(details.attributes or {}, ensure_ascii=False),
        )
        if part
    )
    return _mentions_listing_tokens(haystack, item)


def _offer_signature(offer: _ShopOffer, details: _ProductDetails) -> tuple:
    """Content fingerprint of one extracted offer plus its page read.

    Identical fingerprints attached to two *different* listings mean the
    same panel (usually a sponsored/featured block) was read for both — a
    page fixture, not per-product offers. The listing's own text is
    deliberately excluded: only page-extracted content fingerprints.
    """
    return (
        (offer.shop_name or "").strip().lower(),
        (offer.price_hkd or "").strip(),
        (offer.stock_text or "").strip(),
        (offer.stock_state or "").strip(),
        (details.specifications or "").strip(),
        (details.specifications_original or "").strip(),
        str(details.attributes or ""),
    )


def _drop_cross_product_duplicates(
    entries: list[tuple[tuple, str, OfferEvidence]],
) -> list[OfferEvidence]:
    """Drop offers whose extracted content spans several different products.

    Repeats under *one* listing (duplicate search results for the same
    product) are kept — they collapse into one product row at upsert.
    """
    products_for_sig: dict[tuple, set[str]] = {}
    for signature, listing_name, _ in entries:
        products_for_sig.setdefault(signature, set()).add(listing_name)
    return [
        evidence
        for signature, _, evidence in entries
        if len(products_for_sig[signature]) == 1
    ]


async def _research_aggregator(browser, request: PurchaseRequest, supplier, started: float) -> list[OfferEvidence]:
    """Bounded deep scrape: search → top product pages → per-shop offers."""
    retrieved_at = datetime.now(timezone.utc)
    phrases = _search_phrases(request)
    if not phrases:
        return []

    entry = supplier.catalog_url or supplier.origin

    def _url_for(phrase: str) -> str:
        if "{query}" in entry:
            return entry.replace("{query}", quote_plus(phrase))
        return entry

    # Search with the phrase fallback chain: full English phrase first, then
    # the Traditional Chinese label, then the bare label. Two extraction
    # attempts per phrase (bot-protection remnants break snapshots mid-read).
    listing = None
    url = _url_for(phrases[0])
    for phrase in phrases:
        url = _url_for(phrase)
        await browser.goto(url)
        await browser.wait_until_ready()
        await browser.settle(3.0)
        for _attempt in range(2):
            try:
                # Extract with the href-free LLM schema; map to the internal
                # candidate model (whose href stays None here — navigation
                # targets come only from the deterministic DOM harvest).
                listing_llm = await browser.extract(
                    _listing_instruction(phrase), _ListingPageLLM
                )
                listing = (
                    _ListingPage(
                        products=[
                            _ListingProduct(**row.model_dump())
                            for row in (listing_llm.products if listing_llm else [])
                        ]
                    )
                    if listing_llm
                    else None
                )
                if listing and listing.products:
                    break
            except Exception:
                listing = None
            await browser.settle(6.0)
        if listing and listing.products:
            break
        listing = None
    candidates = _rank_listing(listing.products if listing else [], request)
    # Harvest product URLs from the DOM regardless of extraction outcome:
    # it is one cheap deterministic read, and card-based sites (fortress)
    # extract rows whose titles are not link text — those rows can only be
    # opened by URL. Extracted rows get a matching harvested href; when
    # extraction failed entirely, the harvested anchors ARE the candidates.
    harvested = _rank_listing(await _harvest_product_links(browser), request)
    if not candidates:
        candidates = harvested
    elif harvested:
        for candidate in candidates:
            if not _usable_href(candidate.href):
                candidate.href = _match_harvest_href(candidate, harvested)

    entries: list[tuple[tuple, str, OfferEvidence]] = []
    actions = 0
    for index, item in enumerate(candidates[:MAX_PRODUCT_PAGES]):
        if (
            time.monotonic() - started > MAX_RESEARCH_SECONDS
            or actions >= MAX_ACTIONS_PER_SUPPLIER
        ):
            break
        # DOM-harvested candidate: open the product page directly. Only an
        # absolute URL may be navigated to — anything else falls through to
        # the title-based click path.
        href = _usable_href(item.href)
        if href:
            await browser.goto(href)
            await browser.wait_until_ready(10)
            await browser.settle(2.0)
            actions += 1
        else:
            # Re-load the search page for each product: SPA-style product links
            # break history navigation, so "back" cannot be relied on.
            if index > 0:
                await browser.goto(url)
                await browser.wait_until_ready(10)
                await browser.settle(2.0)
            # Open the product page by its title: cheap DOM click first, then
            # observe-based resolution for layouts the DOM click cannot handle.
            if not await browser.click_link_to(item.product_name[:60]):
                continue
            actions += 1
        # Landing check: a readable title that doesn't mention this product
        # means the click missed — extracting whatever page we're on would
        # attach a stranger's facts to this listing. (An unreadable title
        # can't be judged; the details guard remains as backstop.)
        title = await browser.page_title()
        if title and not _mentions_listing_tokens(title, item):
            actions += 1
            continue
        details = await _read_product_details(browser, item, request)
        if details is not None:
            # Split trust: the landing was verified, so the seller offers on
            # this page are the product's own. The spec block is only trusted
            # when it mentions the product — otherwise it read like a panel
            # and is dropped to unknown instead of feeding keyword checks.
            specs_verified = _details_mention_listing(details, item)
            for offer in details.offers[:MAX_OFFERS_PER_PRODUCT]:
                evidence = _shop_offer_to_evidence(
                    offer,
                    details,
                    item,
                    request,
                    supplier,
                    retrieved_at,
                    trust_specs=specs_verified,
                )
                if evidence is not None:
                    entries.append(
                        (_offer_signature(offer, details), item.product_name, evidence)
                    )
        # (No go_back: href candidates goto their target, name-click
        # candidates reload the search page — see above.)
        actions += 1

    return _drop_cross_product_duplicates(entries)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _headless_for(supplier) -> bool:
    """Headless for mock/catalog sites; visible browser for aggregator sites.

    Live aggregator sites (e.g. price.com.hk) sit behind bot protection that
    a headless browser fails; a visible Chrome window passes the check — and
    makes the demo watchable. ``STAGEHAND_HEADLESS`` overrides both.
    """
    env = os.getenv("STAGEHAND_HEADLESS")
    if env is not None:
        return env.lower() not in ("false", "0", "no")
    return not bool(supplier.catalog_url)


async def research_supplier(
    request: PurchaseRequest, supplier, order_id: int | None = None
) -> list[OfferEvidence]:
    """Browse one supplier's site and extract product evidence.

    ``supplier`` is a ``procurement.repository.Supplier`` row. Suppliers with a
    ``catalog_url`` use the bounded aggregator deep-scrape flow; the rest use
    the single catalog-page flow. ``order_id`` attaches the browser to the
    run console's live view (a screenshot is captured at every navigation
    and page read); ``None`` disables capturing.
    """
    from .browser import StagehandBrowser

    browser = StagehandBrowser(headless=_headless_for(supplier), order_id=order_id)
    started = time.monotonic()
    await browser.start()
    try:
        if supplier.catalog_url:
            return await _research_aggregator(browser, request, supplier, started)
        return await _research_catalog_page(browser, request, supplier)
    finally:
        await browser.close()


def validate_offer(request: PurchaseRequest, offer: OfferEvidence) -> EligibilityResult:
    """Delegate to the deterministic rules engine (already implemented)."""
    from ..rules import validate_offer as _validate

    return _validate(request, offer)


def compare_offers(
    request: PurchaseRequest,
    offers: list[OfferEvidence],
    results: list[EligibilityResult],
) -> ComparisonResult:
    """Delegate to the deterministic rules engine (already implemented)."""
    from ..rules import compare_offers as _compare

    return _compare(request, offers, results)