from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "DATABASE": str(tmp_path / "test.sqlite3"),
            "TEST_NOW": datetime(
                2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )


def csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_menu_has_prominent_pickup_and_allowed_categories(app):
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Your pickup" in response.data
    assert b"Seasonal Special Pies" in response.data
    assert b"Traditional Pies" in response.data
    assert b"Mari Pies" in response.data
    assert b"images/pizzeria-mari-logo-cream.png" in response.data
    assert b"Order ahead" not in response.data
    assert b"Whole pies" not in response.data
    assert b"pizza spots" not in response.data
    assert b"category-nav" not in response.data


def test_brand_typography_uses_compagnon_for_display_and_semplicita_for_body(app):
    client = app.test_client()
    css = client.get("/static/style.css")

    assert css.status_code == 200
    assert b"--font-display: 'Compagnon'" in css.data
    assert b"--font-body: 'Semplicita'" in css.data
    assert b".header-meta > span { font: 500 1.08rem/1 var(--font-display); }" in css.data
    assert b".menu-card-copy > strong" in css.data
    assert b"font-family: var(--font-body)" in css.data
    assert b"Kalakala" not in css.data


def test_server_rejects_cart_above_pizza_limit(app):
    client = app.test_client()
    token = csrf(client)
    headers = {"X-CSRF-Token": token}

    first = client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 3, "modifiers": []},
        headers=headers,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/cart",
        json={"item_id": "white", "quantity": 1, "modifiers": []},
        headers=headers,
    )
    assert second.status_code == 409
    assert "at most 3 pizzas" in second.get_json()["error"]


def test_cart_quantity_can_be_changed_without_exceeding_pizza_limit(app):
    client = app.test_client()
    token = csrf(client)
    headers = {"X-CSRF-Token": token}
    added = client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1, "modifiers": []},
        headers=headers,
    ).get_json()
    line_id = added["lines"][0]["id"]

    updated = client.patch(
        f"/api/cart/{line_id}", json={"quantity": 3}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.get_json()["lines"][0]["quantity"] == 3
    assert updated.get_json()["totals"]["subtotal"] == "$78.00"

    too_many = client.patch(
        f"/api/cart/{line_id}", json={"quantity": 4}, headers=headers
    )
    assert too_many.status_code == 409
    assert "at most 3 pizzas" in too_many.get_json()["error"]


def test_cart_quantity_controls_and_three_pizza_button_message_are_present(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    menu = client.get("/")
    checkout = client.get("/checkout")
    javascript = client.get("/static/app.js")
    assert b'id="cart-error"' in menu.data
    assert b'id="add-to-cart-label"' in menu.data
    assert b'data-cart-action="increase"' in checkout.data
    assert b'data-cart-action="decrease"' in checkout.data
    assert b"Add to order \xc2\xb7 ${data.pizzaLimit} pizza maximum" in javascript.data
    assert b"quantityUp.disabled = reachesPizzaLimit || reachesTotalLimit" in javascript.data


def test_pickup_api_keeps_full_times_visible_without_capacity_counts(app):
    service_at = "2026-08-06T16:00:00-04:00"
    app.extensions["capacity_store"].confirm_demo_order(
        service_at=service_at,
        pizza_count=3,
        capacity=3,
        customer={"name": "Alex", "email": "alex@example.com", "phone": ""},
        notes="",
        tip_cents=0,
        cart=[{"item_id": "plain", "quantity": 3}],
    )

    client = app.test_client()
    payload = client.get("/api/slots?date=2026-08-06").get_json()
    assert payload["slots"]
    assert set(payload["slots"][0]) == {"iso", "time", "available", "status"}
    assert payload["slots"][0] == {
        "iso": service_at,
        "time": "4:00 PM",
        "available": False,
        "status": "Full",
    }
    assert "remaining" not in payload["slots"][0]

    javascript = client.get("/static/app.js")
    checkout_javascript = client.get("/static/checkout.js")
    assert b"slot-choice-unavailable" in javascript.data
    assert b"slot.status" in javascript.data
    assert b"slot-choice-unavailable" in checkout_javascript.data


def test_addition_placement_is_validated_and_priced(app):
    client = app.test_client()
    token = csrf(client)
    response = client.post(
        "/api/cart",
        json={
            "item_id": "plain",
            "quantity": 1,
            "additions": [{"id": "pepperoni", "placement": "first_half"}],
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["totals"]["subtotal"] == "$29.00"
    assert payload["lines"][0]["modifiers"] == ["Pepperoni · First half"]


def test_preferences_are_removed_from_items_and_item_dialog(app):
    client = app.test_client()
    menu = client.get("/")
    javascript = client.get("/static/app.js")

    assert b"Preferences" not in menu.data
    assert b"item-preferences" not in menu.data
    assert b"activeItem.preferences" not in javascript.data


def test_checkout_shows_tax_default_tip_code_field_and_inline_pickup_change(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    response = client.get("/checkout")
    assert response.status_code == 200
    assert b"Sales tax (8%)" in response.data
    assert b"$2.08" in response.data
    assert b"$31.98" in response.data
    assert b'name="tip_choice" value="15" checked' in response.data
    assert b'id="discount-code"' in response.data
    assert b'id="checkout-change-pickup"' in response.data
    assert b'id="summary-lines"' in response.data
    assert b"pizza spots" not in response.data


def test_custom_tip_is_included_in_confirmed_total(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "name": "Alex",
            "email": "alex@example.com",
            "tip_choice": "custom",
            "custom_tip": "4.25",
        },
    )
    assert response.status_code == 200
    assert b"Thanks, Alex." in response.data
    assert b"$32.33" in response.data
