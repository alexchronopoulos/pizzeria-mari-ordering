import pytest

from app.cart import CartLimitError, cart_totals, validate_cart
from app.menu import ITEMS_BY_ID


def line(item_id: str, quantity: int) -> dict:
    return {"id": item_id, "item_id": item_id, "quantity": quantity, "modifiers": []}


def test_three_pizzas_are_allowed():
    validate_cart([line("plain", 3)], ITEMS_BY_ID, 8, {"pizza": 3})


def test_four_pizzas_are_rejected_even_below_total_limit():
    with pytest.raises(CartLimitError, match="at most 3 pizzas"):
        validate_cart(
            [line("plain", 2), line("white", 2)],
            ITEMS_BY_ID,
            8,
            {"pizza": 3},
        )


def test_overall_limit_is_independently_enforced():
    with pytest.raises(CartLimitError, match="at most 2 total items"):
        validate_cart([line("plain", 3)], ITEMS_BY_ID, 2, {"pizza": 3})


def test_modifier_prices_apply_to_every_item_in_a_line():
    cart_line = line("plain", 2)
    cart_line["modifiers"] = [{"price_cents": 300}]
    totals = cart_totals([cart_line], ITEMS_BY_ID)
    assert totals["subtotal_cents"] == 5800
