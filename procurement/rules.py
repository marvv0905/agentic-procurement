"""Business rules (§5 of the project proposal).

Pure functions only — no I/O, no LLM, no database. Everything here is unit
testable. All money is integer HKD minor units.

Three-valued validation: each requirement returns PASS, FAIL, or UNKNOWN.
UNKNOWN means "not enough evidence to decide", never silently converted to a
pass or a fail.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date

from . import embeddings
from .catalog import BOOL, CATEGORIES, INT, LIST, OTHER_CATEGORY, STR, AttributeSpec
from .money import verified_total
from .schemas import (
    ApprovalRole,
    ComparisonResult,
    EligibilityResult,
    FieldValidation,
    OfferEvidence,
    PurchaseRequest,
    RequestRequirements,
    ValidationStatus,
)

#: Number of comparable quotations required before the shortfall is waived (rule 7).
QUOTATION_TARGET = 3

#: Purchases at or above this total require a manager role (rule 6). HK$10,000.
MANAGER_THRESHOLD_MINOR = 10_000 * 100


def supplier_allowed(supplier_id: str, allowlist: Iterable[str]) -> bool:
    """Rule 1: only allowlisted suppliers may be recommended."""
    return supplier_id in set(allowlist)


def required_approval_role(total_minor: int) -> ApprovalRole:
    """Rule 6: total at/above threshold requires a manager, else a requester."""
    if total_minor >= MANAGER_THRESHOLD_MINOR:
        return ApprovalRole.MANAGER
    return ApprovalRole.REQUESTER


def resolved_total_minor(offer: OfferEvidence, quantity: int) -> int | None:
    """Return the complete verified total, or ``None`` if any term is unknown.

    Rule 4/5: a total with any unresolved charge must not be treated as valid.
    """
    if offer.unit_price_minor is None or offer.shipping_minor is None:
        return None
    return verified_total(offer.unit_price_minor, quantity, offer.shipping_minor)


#: Type-conflict nouns for the free-text ("other") item-type check. An offer
#: whose TITLE is one of these is an accessory, not the requested item: the
#: brand keyword matches it ("ThinkPad Laptop Backpack" passed every check
#: for a laptop request), and the cheapest-verified-total ranking then
#: recommended a HK$125 backpack for a 7-laptop order (PO-2026-0051).
#: Nouns are grouped by product type: when the requested item already names
#: ANY noun of a group (item "laptop bag"), no noun of that group conflicts
#: — the user asked for exactly that kind of accessory.
_ACCESSORY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("backpack", "bag", "briefcase", "背包", "電腦袋", "手提袋", "公事包", "行李袋"),
    ("case", "sleeve", "skin", "cover", "保護套", "保護殼", "保護貼"),
    ("stand", "holder", "支架", "底座"),
    ("dock", "docking", "擴充塢"),
    ("charger", "adapter", "充電器", "火牛"),
    ("mouse", "mousepad", "mouse pad", "滑鼠", "鼠標"),
    ("keyboard", "鍵盤"),
    ("battery", "電池"),
    ("cable", "kit"),
)

#: Flat noun list (every group member), for fast substring scans.
_ACCESSORY_NOUNS: tuple[str, ...] = tuple(
    noun for group in _ACCESSORY_GROUPS for noun in group
)

#: Noun -> its group, so the item-side suppression lookup is O(1).
_NOUN_GROUP: dict[str, tuple[str, ...]] = {
    noun: group for group in _ACCESSORY_GROUPS for noun in group
}


def is_accessory_conflict(title: str, item: str) -> str | None:
    """The accessory noun ``title`` carries that ``item`` does not, or None.

    Deterministic substring scan (title and item lowercased). A noun is a
    conflict only when NO noun of its product-type group appears in the
    item: "backpack" in a title is fine when the item is a "laptop bag"
    (same group), but fails a plain "laptop" request. Returns the
    conflicting noun so callers can name it in their reason strings.
    """
    title = (title or "").lower()
    item = (item or "").lower()
    if not title or not item:
        return None
    for noun in _ACCESSORY_NOUNS:
        if noun in title:
            group = _NOUN_GROUP[noun]
            if not any(member in item for member in group):
                return noun
    return None


def _item_type_check(req: RequestRequirements, offer: OfferEvidence) -> FieldValidation | None:
    """Rule 2 (free-text): the offer must be the requested item, not an
    accessory that happens to carry the same brand keywords.

    Only the conflict case is decidable deterministically — an offer whose
    title IS a bag/stand/charger fails. The item's absence from the text is
    deliberately NOT judged (a genuine "ThinkPad T480s" title never says
    "laptop", and three-valued logic must not block real products).
    """
    item = str((req.attributes or {}).get("item") or "").strip()
    if not item:
        return None
    noun = is_accessory_conflict(offer.model_name or "", item)
    if noun is None:
        return None
    return _check(
        "item_type",
        ValidationStatus.FAIL,
        f"offer looks like an accessory ({noun}), not the requested item",
    )


def _keyword_checks(req: RequestRequirements, offer: OfferEvidence) -> list[FieldValidation]:
    """Free-text category validation: every requested keyword must appear in
    the offer's extracted specifications text (or its model name).

    Primary check: deterministic substring matching — the LLM only extracted
    the text; Python decides eligibility. Matching is normalized (case,
    hyphens, punctuation) so "noise cancelling" matches "Noise-Cancelling",
    and runs against the English translation *and* the page's original
    language, so a Traditional Chinese keyword like 降噪 matches the
    untranslated evidence too.

    Fallback (Lab 3): a keyword whose substring match fails is scored
    against the spec text by local multilingual sentence embeddings (max
    cosine over sentence chunks). At/above the pass threshold it is a PASS
    (a paraphrase or translation of the keyword is stated); in the uncertain
    band it is UNKNOWN — weaker than a literal string and never assumed;
    below that, FAIL. A keyword nobody stated is UNKNOWN, never assumed.
    """
    def normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.lower()).strip()

    spec_parts = [
        str(v)
        for v in (
            offer.model_name,
            offer.attributes.get("specifications"),
            offer.attributes.get("specifications_original"),
        )
        if v
    ]
    spec_text = normalize(" ".join(spec_parts))
    # The raw (un-normalized) text carries the natural phrasing the
    # embedding model was trained on; the semantic fallback scores it.
    raw_spec_text = " ".join(spec_parts)

    checks: list[FieldValidation] = []
    keywords = [k for k in (req.attributes.get("keywords") or []) if isinstance(k, str) and k.strip()]
    passed: set[str] = set()
    for keyword in keywords:
        if normalize(keyword) in spec_text:
            checks.append(
                _check(keyword.strip(), ValidationStatus.PASS, "appears in the product specifications")
            )
            passed.add(keyword.strip())
    missing = [k.strip() for k in keywords if k.strip() not in passed]
    # One batched embedding pass scores every substring-missed keyword.
    semantic = embeddings.semantic_keyword_scores(missing, raw_spec_text) if missing else None
    for keyword in missing:
        score = (semantic or {}).get(keyword)
        if score is not None and score >= embeddings.SEMANTIC_PASS_THRESHOLD:
            checks.append(
                _check(
                    keyword,
                    ValidationStatus.PASS,
                    f"semantic match in the specifications (cosine {score:.2f})",
                )
            )
        elif score is not None and score >= embeddings.SEMANTIC_UNKNOWN_THRESHOLD:
            checks.append(
                _check(
                    keyword,
                    ValidationStatus.UNKNOWN,
                    f"possible semantic match in the specifications (cosine {score:.2f}) — not literally stated",
                )
            )
        else:
            reason = "not found in the extracted specifications"
            if score is not None:
                reason += f" (closest semantic match cosine {score:.2f})"
            checks.append(_check(keyword, ValidationStatus.FAIL, reason))
    if not checks:
        checks.append(
            _check("item keywords", ValidationStatus.UNKNOWN, "no required keywords were extracted")
        )
    return checks


#: Explicit out-of-stock evidence still fails the quantity check — checked
#: BEFORE the assumed-in-stock default so "無存貨" is never mistaken for stock.
_OUT_OF_STOCK_PATTERNS = ("缺貨", "無存貨", "售罄", "已售完", "out of stock", "sold out")


def _quantity_check(
    request: PurchaseRequest, offer: OfferEvidence
) -> FieldValidation:
    """Rule 3: a supplier must cover the entire quantity.

    A numeric stock count below the requested quantity is the only hard
    failure. Explicit out-of-stock evidence (classifier label or raw text)
    also fails. Otherwise a product *listed for sale on the site* is assumed
    available — checking deeper is redundant, so the check passes with the
    count left unverified rather than degrading to UNKNOWN and blocking the
    offer. The raw availability text is always kept for the human approver.
    """
    if offer.available_quantity is not None:
        if offer.available_quantity < request.quantity:
            return _check("available_quantity", ValidationStatus.FAIL, "insufficient stock for full quantity")
        return _check("available_quantity", ValidationStatus.PASS, "sufficient stock for full quantity")

    state = str(offer.attributes.get("stock_state") or "").lower()
    if state == "out_of_stock":
        return _check("available_quantity", ValidationStatus.FAIL, "listed as out of stock")
    stock_status = str(offer.attributes.get("stock_status") or "").lower()
    if any(p in stock_status for p in _OUT_OF_STOCK_PATTERNS):
        return _check("available_quantity", ValidationStatus.FAIL, "listed as out of stock")

    if state in ("in_stock", "low_stock"):
        note = "low stock" if state == "low_stock" else "in stock"
        return _check(
            "available_quantity",
            ValidationStatus.PASS,
            f"listed as {note} (count not shown)",
        )
    if state == "preorder":
        return _check(
            "available_quantity",
            ValidationStatus.PASS,
            "orderable, ships in a few days (count not shown)",
        )
    # No availability information at all: a product listed for sale on the
    # site is assumed in stock — never UNKNOWN, never a blocker.
    return _check(
        "available_quantity",
        ValidationStatus.PASS,
        "listed for sale — assumed in stock (count not verified)",
    )


def _check(
    field: str,
    status: ValidationStatus,
    reason: str,
    evidence_ids: Sequence[str] = (),
) -> FieldValidation:
    return FieldValidation(
        field=field, status=status, reason=reason, evidence_ids=list(evidence_ids)
    )


def _attribute_check(
    spec: AttributeSpec,
    req_value: object,
    offer_value: object,
) -> FieldValidation:
    """Three-valued check of one category attribute against the request.

    ``offer_value`` missing -> UNKNOWN (never FAIL). Otherwise compare by the
    attribute's declared type.
    """
    name = spec.name
    if offer_value is None:
        return _check(name, ValidationStatus.UNKNOWN, f"{name} not extracted")

    if spec.type == INT:
        ok = offer_value >= req_value
        reason = "meets minimum" if ok else "below minimum"
    elif spec.type == STR:
        ok = offer_value == req_value
        reason = "matches requirement" if ok else "does not match requirement"
    elif spec.type == BOOL:
        ok = bool(offer_value) == bool(req_value)
        reason = "matches requirement" if ok else "does not match requirement"
    elif spec.type == LIST:
        required = req_value if isinstance(req_value, list) else [req_value]
        missing = [c for c in required if c not in offer_value]
        ok = not missing
        reason = "all required values present" if ok else f"missing: {', '.join(missing)}"
    else:  # pragma: no cover - defensive
        return _check(name, ValidationStatus.UNKNOWN, f"unknown attribute type {spec.type}")

    status = ValidationStatus.PASS if ok else ValidationStatus.FAIL
    return _check(name, status, reason)


def validate_offer(
    request: PurchaseRequest,
    offer: OfferEvidence,
    budget_snapshot_minor: int | None = None,
) -> EligibilityResult:
    """Validate a single offer against the request and (optional) budget.

    Checks: category match + mandatory specifications (rule 2), quantity
    sufficiency (rule 3), unknown mandatory information (rule 4), and budget
    ceilings (rule 5). Category attributes are validated generically against the
    catalog spec.
    """
    checks: list[FieldValidation] = []
    req = request.mandatory

    spec = CATEGORIES.get(req.category)
    if req.category == OTHER_CATEGORY:
        # Free-text category: deterministic keyword matching against the
        # offer's extracted specifications. The offer itself is labeled
        # "other" by the extraction, so no category mismatch is possible.
        checks.extend(_keyword_checks(req, offer))
        # Item-type integrity: keywords validate attributes, not product
        # intent — a "ThinkPad Laptop Backpack" passes the "Thinkpad"
        # keyword, so the accessory conflict is checked separately.
        item_check = _item_type_check(req, offer)
        if item_check is not None:
            checks.append(item_check)
    elif spec is None:
        checks.append(
            _check("category", ValidationStatus.FAIL, f"unknown category {req.category!r}")
        )
    else:
        if offer.category != req.category:
            checks.append(
                _check(
                    "category",
                    ValidationStatus.FAIL,
                    f"offer category {offer.category!r} does not match request {req.category!r}",
                )
            )
        for attr in spec.attributes:
            req_value = req.attributes.get(attr.name)
            if req_value is None:
                # Not requested by the requester; nothing to enforce.
                continue
            checks.append(_attribute_check(attr, req_value, offer.attributes.get(attr.name)))

    # Warranty (universal across categories).
    if req.warranty_months_min is not None:
        if offer.warranty_months is None:
            checks.append(_check("warranty_months", ValidationStatus.UNKNOWN, "warranty not extracted"))
        elif offer.warranty_months >= req.warranty_months_min:
            checks.append(_check("warranty_months", ValidationStatus.PASS, "warranty meets minimum"))
        else:
            checks.append(_check("warranty_months", ValidationStatus.FAIL, "warranty below minimum"))

    # Quantity sufficiency (rule 3): a supplier must cover the entire quantity.
    checks.append(_quantity_check(request, offer))

    # Verified total and budget (rule 5). When shipping is unknown (common on
    # aggregator sites), the total is verified WITHOUT shipping and the check
    # says so — the manager sees the unverified shipping on the approval card.
    total = resolved_total_minor(offer, request.quantity)
    shipping_unverified = offer.shipping_minor is None
    if offer.unit_price_minor is None:
        checks.append(_check("verified_total", ValidationStatus.UNKNOWN, "unit price unresolved"))
        total = None
    elif total is None:
        total = offer.unit_price_minor * request.quantity
        if total > request.max_total_cost_minor:
            checks.append(
                _check("verified_total", ValidationStatus.FAIL, "exceeds request budget (shipping unverified, excluded)")
            )
        else:
            checks.append(
                _check("verified_total", ValidationStatus.PASS, "within budget (shipping unverified, excluded)")
            )
    else:
        if total > request.max_total_cost_minor:
            checks.append(_check("verified_total", ValidationStatus.FAIL, "exceeds request budget"))
        elif budget_snapshot_minor is not None and total > budget_snapshot_minor:
            checks.append(_check("verified_total", ValidationStatus.FAIL, "exceeds department budget snapshot"))
        else:
            checks.append(_check("verified_total", ValidationStatus.PASS, "within budget"))

    # Aggregate status: any FAIL dominates; else any UNKNOWN; else PASS.
    statuses = {c.status for c in checks}
    if ValidationStatus.FAIL in statuses:
        overall = ValidationStatus.FAIL
    elif ValidationStatus.UNKNOWN in statuses:
        overall = ValidationStatus.UNKNOWN
    else:
        overall = ValidationStatus.PASS

    return EligibilityResult(
        offer_id=offer.offer_id,
        status=overall,
        checks=checks,
        verified_total_minor=total,
    )


def _rank_key(item: tuple[OfferEvidence, int]) -> tuple[int, object, str]:
    """Sort key: lowest total, then earlier delivery, then stable supplier id.

    ``date.min`` is used as a sentinel so offers missing a delivery date sort
    after any dated offer without crashing.
    """
    offer, total = item
    delivery = offer.delivery_by if offer.delivery_by is not None else date.min
    return (total, delivery, offer.supplier_id)


def compare_offers(
    request: PurchaseRequest,
    offers: Sequence[OfferEvidence],
    results: Sequence[EligibilityResult],
) -> ComparisonResult:
    """Rank fully eligible offers and select the lowest verified total (rule 8)."""
    eligible: list[tuple[OfferEvidence, int]] = []
    for offer, result in zip(offers, results):
        if result.status is ValidationStatus.PASS and result.verified_total_minor is not None:
            eligible.append((offer, result.verified_total_minor))

    ranked = sorted(eligible, key=_rank_key)
    selected = ranked[0][0].offer_id if ranked else None

    shortfall = len(ranked) < QUOTATION_TARGET

    return ComparisonResult(
        request_id=request.request_id,
        eligible_offers=[o.offer_id for o, _ in eligible],
        ranked_offers=[o.offer_id for o, _ in ranked],
        selected_offer_id=selected,
        quotation_shortfall=shortfall,
    )
