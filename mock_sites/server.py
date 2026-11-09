"""Mock supplier sites and requisition portal (skeleton).

Three supplier layouts and a mock portal run as local FastAPI apps. HTML polish
and full catalogue rendering arrive in Phase 2/4; this file establishes the app
structure and route boundaries so the browser tools have stable targets.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="PurchasePilot mock world")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "base.html",
        {
            "request": request,
            "title": "PurchasePilot mock world",
            "suppliers": ["Pixel Peak Supplies", "Beacon Bay Office Co.", "Harbourview Tech"],
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/supplier/{supplier_id}/catalog")
def supplier_catalog(supplier_id: str) -> JSONResponse:
    """Placeholder catalogue endpoint (Phase 3 renders real listings)."""
    return JSONResponse({"supplier_id": supplier_id, "listings": []})


@app.get("/portal")
def portal(request: Request) -> HTMLResponse:
    """Placeholder requisition form (Phase 4 renders the real form)."""
    return templates.TemplateResponse(
        "base.html", {"request": request, "title": "Mock requisition portal"}
    )


@app.post("/portal/submit")
def portal_submit() -> JSONResponse:
    """Placeholder submission endpoint (Phase 4 implements idempotent writes)."""
    return JSONResponse({"status": "not_implemented"})
