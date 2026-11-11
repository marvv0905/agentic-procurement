"""Mock supplier sites and requisition portal.

Renders the deterministic seed data (``procurement.seed``) into browser-visible
HTML. Three suppliers share one schema but render through three different
layouts, so the agent must handle visibly different page structures (§10).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from procurement.catalog import CATEGORIES, BOOL, LIST, get_category
from procurement.money import format_hkd
from procurement.seed import generate

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="PurchasePilot mock world")

SEED_DATA = generate()

_LAYOUTS = {
    "SUP_A": "supplier_a.html",
    "SUP_B": "supplier_b.html",
    "SUP_C": "supplier_c.html",
}


def _supplier(sid: str):
    return next((s for s in SEED_DATA.suppliers if s.id == sid), None)


def _listing(pid: str):
    return next((l for l in SEED_DATA.listings if l.product_id == pid), None)


def _fmt_value(attr, value) -> str:
    if value is None:
        return "Not specified"
    if attr.type == BOOL:
        return "Yes" if value else "No"
    if attr.type == LIST:
        return ", ".join(value)
    return str(value)


def _display_listing(l):
    spec = get_category(l.category)
    attributes_display = []
    if spec is not None:
        for attr in spec.attributes:
            attributes_display.append((attr.display_name, _fmt_value(attr, l.attributes.get(attr.name))))

    return {
        "product_id": l.product_id,
        "model_name": l.model_name,
        "category": l.category,
        "category_label": spec.label if spec else l.category,
        "attributes_display": attributes_display,
        "warranty_months": l.warranty_months,
        "warranty_provider": l.warranty_provider,
        "price": format_hkd(l.unit_price_minor),
        "available_units": l.available_units,
        "shipping": (
            "See terms page"
            if "hidden_shipping" in l.scenario_flags
            else format_hkd(l.shipping_minor)
        ),
        "delivery": (
            "Not specified"
            if l.delivery_days is None
            else f"{l.delivery_days} business days"
        ),
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"suppliers": SEED_DATA.suppliers}
    )


@app.get("/supplier/{supplier_id}/catalog", response_class=HTMLResponse)
def supplier_catalog(request: Request, supplier_id: str) -> HTMLResponse:
    supplier = _supplier(supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="unknown supplier")
    listings = [
        _display_listing(l)
        for l in SEED_DATA.listings
        if l.supplier_id == supplier_id
    ]
    return templates.TemplateResponse(
        request,
        _LAYOUTS.get(supplier_id, "supplier_a.html"),
        {"supplier": supplier, "listings": listings},
    )


@app.get("/supplier/{supplier_id}/product/{product_id}", response_class=HTMLResponse)
def supplier_product(request: Request, supplier_id: str, product_id: str) -> HTMLResponse:
    listing = _listing(product_id)
    supplier = _supplier(supplier_id)
    if listing is None or supplier is None or listing.supplier_id != supplier_id:
        raise HTTPException(status_code=404, detail="unknown product")
    return templates.TemplateResponse(
        request,
        "detail.html",
        {"supplier": supplier, "p": _display_listing(listing)},
    )


@app.get("/supplier/{supplier_id}/terms", response_class=HTMLResponse)
def supplier_terms(request: Request, supplier_id: str) -> HTMLResponse:
    supplier = _supplier(supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="unknown supplier")
    listings = [l for l in SEED_DATA.listings if l.supplier_id == supplier_id]
    shipping_minor = listings[0].shipping_minor if listings else 0
    max_days = max((l.delivery_days or 0) for l in listings)
    return templates.TemplateResponse(
        request,
        "terms.html",
        {
            "supplier": supplier,
            "shipping_standard": format_hkd(shipping_minor),
            "delivery_days": max_days,
        },
    )


@app.get("/portal", response_class=HTMLResponse)
def portal(request: Request) -> HTMLResponse:
    """Placeholder requisition form (Phase 4 renders the real form)."""
    return templates.TemplateResponse(
        request, "portal.html", {"title": "Mock requisition portal"}
    )


@app.post("/portal/submit")
def portal_submit() -> JSONResponse:
    """Placeholder submission endpoint (Phase 4 implements idempotent writes)."""
    return JSONResponse({"status": "not_implemented"})
