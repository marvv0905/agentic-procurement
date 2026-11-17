"""Category catalog — the single source of truth for product categories.

Each category declares its attributes (name, type, required, choices). The
rules engine, seed generator, prompts, and mock-site templates all read from
here, so adding a category means adding one entry.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Attribute value types understood by the validator.
INT = "int"
STR = "str"
BOOL = "bool"
LIST = "list"


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    type: str  # one of INT / STR / BOOL / LIST
    required: bool = True
    choices: tuple[str, ...] | None = None
    label: str | None = None

    @property
    def display_name(self) -> str:
        return self.label or self.name.replace("_", " ")


@dataclass(frozen=True)
class CategorySpec:
    id: str
    label: str
    attributes: tuple[AttributeSpec, ...]
    #: Traditional Chinese label used as a live-site search fallback (HK
    #: aggregators index product titles in Chinese).
    label_zh: str | None = None

    @property
    def attribute_map(self) -> dict[str, AttributeSpec]:
        return {a.name: a for a in self.attributes}


#: Short codes used in product IDs.
CATEGORY_CODES = {"monitor": "MON", "keyboard": "KEY", "chair": "CHA"}

#: Free-text category id. Requests outside the typed catalog are searched as
#: natural language ("noise cancelling headphones") and validated by keyword
#: presence instead of typed attributes.
OTHER_CATEGORY = "other"

CATEGORIES: dict[str, CategorySpec] = {
    "monitor": CategorySpec(
        id="monitor",
        label="Monitor",
        label_zh="顯示器",
        attributes=(
            AttributeSpec("screen_inches_min", INT, label="screen size (inches)"),
            AttributeSpec(
                "resolution",
                STR,
                choices=("1920x1080", "2560x1440", "3840x2160"),
            ),
            AttributeSpec(
                "connectors",
                LIST,
                choices=("HDMI", "DisplayPort", "USB-C"),
            ),
        ),
    ),
    "keyboard": CategorySpec(
        id="keyboard",
        label="Keyboard",
        label_zh="鍵盤",
        attributes=(
            AttributeSpec(
                "layout", STR, choices=("full-size", "tenkeyless", "75%", "60%")
            ),
            AttributeSpec("switch_type", STR, choices=("mechanical", "membrane")),
            AttributeSpec("wireless", BOOL),
        ),
    ),
    "chair": CategorySpec(
        id="chair",
        label="Office chair",
        label_zh="辦公椅",
        attributes=(
            AttributeSpec("material", STR, choices=("mesh", "fabric", "leather")),
            AttributeSpec("armrests", BOOL),
            AttributeSpec("max_weight_kg", INT),
        ),
    ),
}


def category_ids() -> list[str]:
    return list(CATEGORIES.keys())


def get_category(category: str | None) -> CategorySpec | None:
    if category is None:
        return None
    return CATEGORIES.get(category)


def detect_category(text: str | None) -> str | None:
    """Deterministically map a free-text request onto a typed category.

    A plain substring match on the category label (covers plurals —
    "monitors" contains "monitor"). Returns the category id, or ``None``
    when nothing matches: the caller then routes the request to the
    free-text keyword search flow, which works for anything. Detection only
    ever picks the *validation contract* — it never blocks a request.
    """
    if not text:
        return None
    lowered = text.lower()
    for category_id, spec in CATEGORIES.items():
        if spec.label.lower() in lowered:
            return category_id
    return None
