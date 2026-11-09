"""Seeded synthetic data generation (§10).

Generates three suppliers with ~12 listings each from explicit value lists,
using a seeded ``random.Random`` (seed ``4110``) for repeatability. The same
underlying product schema is rendered through three different browser layouts
by the mock sites.

Ground truth is kept in a separate structure from browser-visible listings so
the agent can never shortcut supplier research by reading seed data (§10,
"Keep hidden ground-truth fixtures separate").
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

#: Fixed seed for reproducibility.
SEED = 4110

SCREEN_SIZES = [24, 27, 32]
RESOLUTIONS = ["1920x1080", "2560x1440", "3840x2160"]
CONNECTORS = ["HDMI", "DisplayPort", "USB-C"]
STAND_TYPES = ["fixed", "height-adjustable"]
WARRANTY_MONTHS = [12, 24, 36, 60]

#: Twelve reference products shared across suppliers so comparable offers exist.
REFERENCE_PRODUCTS = [
    {"screen_size": 24, "resolution": "1920x1080", "connectors": ["HDMI"], "stand": "fixed"},
    {"screen_size": 24, "resolution": "1920x1080", "connectors": ["HDMI", "DisplayPort"], "stand": "fixed"},
    {"screen_size": 27, "resolution": "2560x1440", "connectors": ["HDMI", "DisplayPort"], "stand": "height-adjustable"},
    {"screen_size": 27, "resolution": "2560x1440", "connectors": ["HDMI", "DisplayPort", "USB-C"], "stand": "height-adjustable"},
    {"screen_size": 27, "resolution": "1920x1080", "connectors": ["HDMI"], "stand": "fixed"},
    {"screen_size": 27, "resolution": "3840x2160", "connectors": ["HDMI", "DisplayPort"], "stand": "height-adjustable"},
    {"screen_size": 32, "resolution": "2560x1440", "connectors": ["HDMI", "DisplayPort"], "stand": "height-adjustable"},
    {"screen_size": 32, "resolution": "3840x2160", "connectors": ["HDMI", "DisplayPort", "USB-C"], "stand": "height-adjustable"},
    {"screen_size": 32, "resolution": "1920x1080", "connectors": ["HDMI", "DisplayPort"], "stand": "fixed"},
    {"screen_size": 24, "resolution": "2560x1440", "connectors": ["HDMI", "DisplayPort"], "stand": "fixed"},
    {"screen_size": 27, "resolution": "2560x1440", "connectors": ["HDMI"], "stand": "fixed"},
    {"screen_size": 32, "resolution": "2560x1440", "connectors": ["HDMI", "DisplayPort", "USB-C"], "stand": "fixed"},
]


@dataclass
class SupplierSeed:
    id: str
    name: str
    origin: str


@dataclass
class ListingSeed:
    product_id: str
    model_name: str
    supplier_id: str
    screen_size: int
    resolution: str
    connectors: list[str]
    stand_type: str
    warranty_months: int
    warranty_provider: str
    unit_price_minor: int
    available_units: int
    shipping_minor: int
    delivery_days: int | None
    scenario_flags: list[str] = field(default_factory=list)


@dataclass
class SeedData:
    suppliers: list[SupplierSeed]
    listings: list[ListingSeed]
    ground_truth: dict[str, dict]
    seed: int


#: Curated scenario overrides keyed by (supplier_id, product_index).
#: These guarantee meaningful edge cases exist in the synthetic catalogue.
SCENARIO_OVERRIDES: dict[tuple[str, int], dict] = {
    # Low stock: cheapest offer has only two units.
    ("SUP_A", 3): {"available_units": 2, "scenario_flags": ["low_stock"]},
    # Warranty failure: cheap model has a 12-month warranty.
    ("SUP_B", 6): {"warranty_months": 12, "scenario_flags": ["warranty_failure"]},
    # Unknown delivery: arrival date absent.
    ("SUP_C", 8): {"delivery_days": None, "scenario_flags": ["unknown_delivery"]},
    # Hidden shipping: shipping is on a separate terms page.
    ("SUP_A", 1): {"scenario_flags": ["hidden_shipping"]},
}


def generate(seed: int = SEED) -> SeedData:
    """Generate suppliers and listings deterministically."""
    rng = random.Random(seed)

    suppliers = [
        SupplierSeed(id="SUP_A", name="Pixel Peak Supplies", origin="http://127.0.0.1:8101"),
        SupplierSeed(id="SUP_B", name="Beacon Bay Office Co.", origin="http://127.0.0.1:8102"),
        SupplierSeed(id="SUP_C", name="Harbourview Tech", origin="http://127.0.0.1:8103"),
    ]

    listings: list[ListingSeed] = []
    for sup in suppliers:
        for idx, ref in enumerate(REFERENCE_PRODUCTS):
            override = SCENARIO_OVERRIDES.get((sup.id, idx), {})
            base_price = rng.randint(120000, 420000)  # HKD cents: HK$1,200 - HK$4,200
            listing = ListingSeed(
                product_id=f"{sup.id}_MON_{idx:03d}",
                model_name=f"Vision {ref['screen_size']}\" {ref['resolution'].split('x')[0]}x{ref['resolution'].split('x')[1]}",
                supplier_id=sup.id,
                screen_size=ref["screen_size"],
                resolution=ref["resolution"],
                connectors=list(ref["connectors"]),
                stand_type=ref["stand"],
                warranty_months=override.get("warranty_months", rng.choice(WARRANTY_MONTHS)),
                warranty_provider=rng.choice(["manufacturer", "supplier"]),
                unit_price_minor=base_price,
                available_units=override.get("available_units", rng.randint(1, 20)),
                shipping_minor=rng.choice([0, 10000, 15000, 20000]),
                delivery_days=override.get("delivery_days", rng.randint(1, 14)),
                scenario_flags=list(override.get("scenario_flags", [])),
            )
            listings.append(listing)

    # Ground truth mirrors browser-visible facts but lives in a separate
    # structure. Tests/evaluation use this; the agent must not.
    ground_truth = {
        l.product_id: {
            "screen_size": l.screen_size,
            "resolution": l.resolution,
            "connectors": l.connectors,
            "warranty_months": l.warranty_months,
            "unit_price_minor": l.unit_price_minor,
            "available_units": l.available_units,
            "shipping_minor": l.shipping_minor,
            "delivery_days": l.delivery_days,
        }
        for l in listings
    }

    return SeedData(
        suppliers=suppliers,
        listings=listings,
        ground_truth=ground_truth,
        seed=seed,
    )
