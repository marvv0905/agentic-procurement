from procurement.catalog import CATEGORIES
from procurement.seed import SCENARIO_OVERRIDES, generate


def test_generation_is_deterministic():
    a = generate()
    b = generate()
    assert [l.product_id for l in a.listings] == [l.product_id for l in b.listings]
    assert a.ground_truth == b.ground_truth


def test_supplier_and_listing_counts():
    data = generate()
    assert len(data.suppliers) == 3
    assert len(data.listings) == 3 * 12 == 36


def test_covers_all_categories():
    data = generate()
    categories = {l.category for l in data.listings}
    assert categories == set(CATEGORIES.keys())
    # 12 listings per supplier = 4 per category.
    from collections import Counter

    counts = Counter(l.category for l in data.listings)
    assert counts == {"monitor": 12, "keyboard": 12, "chair": 12}


def test_scenario_overrides_applied():
    data = generate()
    low_stock = next(l for l in data.listings if l.product_id == "SUP_A_MON_003")
    assert low_stock.available_units == 2
    assert "low_stock" in low_stock.scenario_flags

    warranty = next(l for l in data.listings if l.product_id == "SUP_B_KEY_006")
    assert warranty.warranty_months == 12

    unknown = next(l for l in data.listings if l.product_id == "SUP_C_CHA_008")
    assert unknown.delivery_days is None


def test_ground_truth_mirrors_listings():
    data = generate()
    for listing in data.listings:
        gt = data.ground_truth[listing.product_id]
        assert gt["unit_price_minor"] == listing.unit_price_minor
        assert gt["category"] == listing.category
        assert gt["attributes"] == listing.attributes
