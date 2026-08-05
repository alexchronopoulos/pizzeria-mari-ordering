from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModifierOption:
    id: str
    name: str
    price_cents: int
    catalog_version: int | None = None
    on_by_default: bool = False

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "price_cents": self.price_cents,
            "price": f"${self.price_cents / 100:.2f}",
            "on_by_default": self.on_by_default,
        }


@dataclass(frozen=True)
class ModifierGroup:
    id: str
    name: str
    selection_type: str
    min_selected: int
    max_selected: int | None
    options: tuple[ModifierOption, ...] = field(default_factory=tuple)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "selection_type": self.selection_type,
            "min_selected": self.min_selected,
            "max_selected": self.max_selected,
            "options": [option.public_dict() for option in self.options],
        }


@dataclass(frozen=True)
class Addition:
    """One topping with Square modifier IDs for each available placement."""

    id: str
    name: str
    placements: dict[str, ModifierOption]

    @property
    def whole_price_cents(self) -> int:
        option = self.placements.get("whole")
        return option.price_cents if option else 0

    @property
    def half_price_cents(self) -> int:
        option = self.placements.get("first_half") or self.placements.get("second_half")
        return option.price_cents if option else 0

    def public_dict(self) -> dict:
        placement_payload = {
            placement: option.public_dict()
            for placement, option in self.placements.items()
        }
        return {
            "id": self.id,
            "name": self.name,
            "placements": placement_payload,
            # Retained for the demo UI and backwards-compatible tests.
            "whole_price_cents": self.whole_price_cents,
            "half_price_cents": self.half_price_cents,
            "whole_price": f"${self.whole_price_cents / 100:.2f}",
            "half_price": f"${self.half_price_cents / 100:.2f}",
        }


@dataclass(frozen=True)
class MenuItem:
    id: str
    name: str
    category: str
    category_label: str
    capacity_category: str | None
    price_cents: int
    description: str
    additions: tuple[Addition, ...] = field(default_factory=tuple)
    modifier_groups: tuple[ModifierGroup, ...] = field(default_factory=tuple)
    available: bool = True
    art: str = "plain"
    image_url: str | None = None
    catalog_object_id: str | None = None
    catalog_version: int | None = None

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "category_label": self.category_label,
            "capacity_category": self.capacity_category,
            "price_cents": self.price_cents,
            "price": f"${self.price_cents / 100:.2f}",
            "description": self.description,
            "additions": [option.public_dict() for option in self.additions],
            "modifier_groups": [group.public_dict() for group in self.modifier_groups],
            "available": self.available,
            "art": self.art,
            "image_url": self.image_url,
        }


@dataclass(frozen=True)
class MenuSnapshot:
    groups: tuple[dict, ...]
    items: tuple[MenuItem, ...]
    capacity_object_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def items_by_id(self) -> dict[str, MenuItem]:
        return {item.id: item for item in self.items}

    @property
    def pizza_catalog_object_ids(self) -> set[str]:
        if self.capacity_object_ids:
            return set(self.capacity_object_ids)
        return {
            item.catalog_object_id
            for item in self.items
            if item.capacity_category == "pizza" and item.catalog_object_id
        }


class MenuProvider(Protocol):
    def snapshot(self) -> MenuSnapshot: ...


DISPLAY_CATEGORIES = (
    ("seasonal", "Seasonal Special Pies"),
    ("traditional", "Traditional Pies"),
    ("mari", "Mari Pies"),
)


def _demo_addition(id: str, name: str, whole: int, half: int) -> Addition:
    return Addition(
        id=id,
        name=name,
        placements={
            "whole": ModifierOption(f"{id}:whole", name, whole),
            "first_half": ModifierOption(f"{id}:first-half", name, half),
            "second_half": ModifierOption(f"{id}:second-half", name, half),
        },
    )


COMMON_ADDITIONS = (
    _demo_addition("pepperoni", "Pepperoni", 500, 300),
    _demo_addition("stracciatella", "House Stracciatella", 600, 350),
    _demo_addition("oyster-mushrooms", "Oyster Mushrooms", 600, 350),
    _demo_addition("pickled-chiles", "Pickled Chiles", 300, 200),
    _demo_addition("pesto", "Basil Pesto", 300, 200),
    _demo_addition("hot-honey", "Mari’s Hot Honey", 200, 125),
    _demo_addition("pecorino", "Pecorino", 200, 125),
)


ITEMS = (
    MenuItem(
        id="cherry-tomato",
        name="Cherry Tomato",
        category="seasonal",
        category_label="Seasonal Special Pies",
        capacity_category="pizza",
        price_cents=3500,
        description=(
            "Slow roasted Sprouting Heart Farm cherry tomato confit, aged mozzarella, "
            "pickled garlic scapes. Finished with house stracciatella, basil pesto, and parm."
        ),
        additions=COMMON_ADDITIONS,
        art="cherry",
    ),
    MenuItem(
        id="sungold-vodka-roni",
        name="Sungold Vodka Roni",
        category="seasonal",
        category_label="Seasonal Special Pies",
        capacity_category="pizza",
        price_cents=3600,
        description=(
            "Cherry tomato vodka sauce, cup & crisp pepperoni, Cobanero chili, scamorza, "
            "aged mozzarella, black pepper, basil, and pecorino."
        ),
        additions=COMMON_ADDITIONS,
        available=False,
        art="sungold",
    ),
    MenuItem(
        id="plain",
        name="Plain",
        category="traditional",
        category_label="Traditional Pies",
        capacity_category="pizza",
        price_cents=2600,
        description=(
            "Aged mozzarella, fresh mozzarella, Cal-Jersey tomato sauce. Finished with "
            "24 month parm, extra virgin olive oil, and fresh basil."
        ),
        additions=COMMON_ADDITIONS,
        art="plain",
    ),
    MenuItem(
        id="tomato",
        name="Tomato",
        category="traditional",
        category_label="Traditional Pies",
        capacity_category="pizza",
        price_cents=2400,
        description=(
            "Double Cal-Jersey tomato sauce and garlic panko. Finished with oregano and "
            "extra virgin olive oil. Vegan friendly."
        ),
        additions=COMMON_ADDITIONS,
        art="tomato",
    ),
    MenuItem(
        id="white",
        name="White",
        category="traditional",
        category_label="Traditional Pies",
        capacity_category="pizza",
        price_cents=2800,
        description=(
            "Aged mozzarella, fresh mozzarella, hand-dipped ricotta, and garlic confit. "
            "Finished with extra virgin olive oil and 24 month parm."
        ),
        additions=COMMON_ADDITIONS,
        art="white",
    ),
    MenuItem(
        id="collar-city",
        name="Collar City",
        category="mari",
        category_label="Mari Pies",
        capacity_category="pizza",
        price_cents=3600,
        description=(
            "Local oyster mushrooms, creme fraiche, Jasper Hill Alpha Tolman, aged "
            "mozzarella, pickled red onions, seasonal herbs, and black pepper."
        ),
        additions=COMMON_ADDITIONS,
        art="mushroom",
    ),
    MenuItem(
        id="pep-and-pepp",
        name="Pep & Pepp",
        category="mari",
        category_label="Mari Pies",
        capacity_category="pizza",
        price_cents=3300,
        description=(
            "Cup & crisp pepperoni, aged mozzarella, scamorza, tomato sauce, and pickled "
            "chiles. Finished with Mari's hot honey, pecorino, and fennel pollen."
        ),
        additions=COMMON_ADDITIONS,
        art="pepperoni",
    ),
    MenuItem(
        id="nduja",
        name="’Nduja",
        category="mari",
        category_label="Mari Pies",
        capacity_category="pizza",
        price_cents=3800,
        description=(
            "La Salumina ’nduja, scamorza, aged mozzarella, and tomato sauce. Finished "
            "with black malt vinegar, house stracciatella, and pecorino."
        ),
        additions=COMMON_ADDITIONS,
        art="nduja",
    ),
)


def _snapshot(items: tuple[MenuItem, ...]) -> MenuSnapshot:
    groups = tuple(
        {
            "id": category_id,
            "label": label,
            "items": tuple(item for item in items if item.category == category_id),
        }
        for category_id, label in DISPLAY_CATEGORIES
    )
    return MenuSnapshot(groups=groups, items=items)


DEMO_MENU = _snapshot(ITEMS)
ITEMS_BY_ID = DEMO_MENU.items_by_id


class StaticMenuProvider:
    def snapshot(self) -> MenuSnapshot:
        return DEMO_MENU


def grouped_menu() -> list[dict]:
    """Backwards-compatible helper for callers that still use the demo menu."""

    return list(DEMO_MENU.groups)
