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

    @property
    def attribute_map(self) -> dict[str, AttributeSpec]:
        return {a.name: a for a in self.attributes}


#: Short codes used in product IDs.
CATEGORY_CODES = {"monitor": "MON", "keyboard": "KEY", "chair": "CHA"}

CATEGORIES: dict[str, CategorySpec] = {
    "monitor": CategorySpec(
        id="monitor",
        label="Monitor",
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
