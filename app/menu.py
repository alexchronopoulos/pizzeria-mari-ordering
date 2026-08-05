from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Addition:
    id: str
    name: str
    whole_price_cents: int
    half_price_cents: int

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
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
    available: bool = True
    art: str = "plain"

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
            "available": self.available,
            "art": self.art,
        }


DISPLAY_CATEGORIES = (
    ("seasonal", "Seasonal Special Pies"),
    ("traditional", "Traditional Pies"),
    ("mari", "Mari Pies"),
)


COMMON_ADDITIONS = (
    Addition("pepperoni", "Pepperoni", 500, 300),
    Addition("stracciatella", "House Stracciatella", 600, 350),
    Addition("oyster-mushrooms", "Oyster Mushrooms", 600, 350),
    Addition("pickled-chiles", "Pickled Chiles", 300, 200),
    Addition("pesto", "Basil Pesto", 300, 200),
    Addition("hot-honey", "Mari’s Hot Honey", 200, 125),
    Addition("pecorino", "Pecorino", 200, 125),
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

ITEMS_BY_ID = {item.id: item for item in ITEMS}


def grouped_menu() -> list[dict]:
    groups = []
    for category_id, label in DISPLAY_CATEGORIES:
        groups.append(
            {
                "id": category_id,
                "label": label,
                "items": [item for item in ITEMS if item.category == category_id],
            }
        )
    return groups
