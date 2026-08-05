from __future__ import annotations

from collections import Counter

from .menu import MenuItem


class CartLimitError(ValueError):
    pass


def validate_cart(
    lines: list[dict],
    items_by_id: dict[str, MenuItem],
    total_limit: int,
    category_limits: dict[str, int],
) -> None:
    total = sum(int(line["quantity"]) for line in lines)
    if total > total_limit:
        raise CartLimitError(f"Your cart can contain at most {total_limit} total items.")

    category_counts: Counter[str] = Counter()
    for line in lines:
        item = items_by_id.get(line["item_id"])
        if not item:
            raise CartLimitError("One of the items in your cart is no longer available.")
        if item.capacity_category:
            category_counts[item.capacity_category] += int(line["quantity"])

    for category, limit in category_limits.items():
        if category_counts[category] > limit:
            label = "pizzas" if category == "pizza" else category
            raise CartLimitError(
                f"You can order at most {limit} {label} per pickup time."
            )


def cart_totals(lines: list[dict], items_by_id: dict[str, MenuItem]) -> dict:
    subtotal = 0
    item_count = 0
    pizza_count = 0
    for line in lines:
        item = items_by_id[line["item_id"]]
        quantity = int(line["quantity"])
        modifier_cents = sum(
            int(modifier.get("price_cents", 0))
            for modifier in line.get("modifiers", [])
            if isinstance(modifier, dict)
        )
        subtotal += (item.price_cents + modifier_cents) * quantity
        item_count += quantity
        if item.capacity_category == "pizza":
            pizza_count += quantity
    return {
        "subtotal_cents": subtotal,
        "subtotal": f"${subtotal / 100:.2f}",
        "item_count": item_count,
        "pizza_count": pizza_count,
    }
