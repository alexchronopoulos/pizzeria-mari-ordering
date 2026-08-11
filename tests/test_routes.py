from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import create_app


@pytest.fixture()
def app():
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
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
    assert b"<title>Pizzeria Mari Order Online</title>" in response.data
    assert b'rel="icon" type="image/png"' in response.data
    assert b"/static/images/PM_icon_black.png?v=0.18.8" in response.data
    assert b"Order ahead" not in response.data
    assert b"Whole pies" not in response.data
    assert b"pizza spots" not in response.data
    assert b"category-nav" not in response.data
    assert b'class="menu-card-media"' in response.data
    assert response.data.index(b'class="menu-card-media"') < response.data.index(
        b"<strong>Cherry Tomato</strong>"
    )

    css = app.test_client().get("/static/style.css").get_data(as_text=True)
    assert ".menu-card {" in css
    assert "border: 2px solid var(--ink)" in css
    assert "background-size: cover" in css
    assert "background-position: center 56%" in css
    assert ".item-detail-media" in css
    detail_rules = css[
        css.index(".item-detail-media {"):
        css.index(".item-detail-media #item-art:not(.item-photo)")
    ]
    assert "height: clamp(240px, 52dvh, 430px)" in detail_rules
    assert "background-size: cover" in detail_rules
    assert b"/static/style.css?v=0.18.8" in response.data
    assert b"/static/app.js?v=0.18.8" in response.data

    favicon = app.test_client().get("/static/images/PM_icon_black.png")
    assert favicon.status_code == 200
    assert favicon.mimetype == "image/png"
    assert favicon.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_health_is_lightweight_and_stays_healthy_when_ordering_is_paused():
    paused = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "ORDERING_ENABLED": False,
            "FALLBACK_ORDERING_URL": "https://pizzeriamari.square.site",
        }
    )
    client = paused.test_client()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.get_json() == {
        "status": "ok",
        "version": "0.18.8",
        "ordering_enabled": False,
    }
    assert health.headers["Cache-Control"] == "no-store"

    page = client.get("/")
    assert page.status_code == 200
    assert b"not taking new orders" in page.data
    assert b"https://pizzeriamari.square.site" in page.data
    assert client.get("/checkout").status_code == 503


def test_emergency_switch_and_fallback_configuration_are_validated():
    with pytest.raises(RuntimeError, match="ORDERING_ENABLED must be"):
        create_app({"TESTING": True, "ORDERING_ENABLED": "sometimes"})

    with pytest.raises(RuntimeError, match="FALLBACK_ORDERING_URL must be"):
        create_app(
            {
                "TESTING": True,
                "FALLBACK_ORDERING_URL": "javascript:alert(1)",
            }
        )


def test_capacity_thresholds_load_from_environment_and_reject_invalid_values(
    monkeypatch,
):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SQUARE_CATALOG_ENABLED", "false")
    monkeypatch.setenv("PIZZA_CART_LIMIT", "4")
    monkeypatch.setenv("PIZZA_SLOT_CAPACITY", "5")
    monkeypatch.setenv("CART_TOTAL_LIMIT", "6")
    monkeypatch.setenv("ORDERING_ENABLED", "true")

    configured = create_app()
    assert configured.config["PIZZA_CART_LIMIT"] == 4
    assert configured.config["PIZZA_SLOT_CAPACITY"] == 5
    assert configured.config["CART_TOTAL_LIMIT"] == 6
    assert configured.config["CATEGORY_LIMITS"] == {"pizza": 4}

    page = configured.test_client().get("/")
    assert b"add up to 4 pizzas and 6 items total" in page.data

    monkeypatch.setenv("CART_TOTAL_LIMIT", "not-a-number")
    with pytest.raises(RuntimeError, match="CART_TOTAL_LIMIT must be"):
        create_app()


def test_capacity_threshold_relationships_are_validated():
    with pytest.raises(RuntimeError, match="largest configured pickup-slot capacity"):
        create_app(
            {
                "TESTING": True,
                "PIZZA_CART_LIMIT": 4,
                "PIZZA_SLOT_CAPACITY": 3,
                "CART_TOTAL_LIMIT": 8,
            }
        )

    with pytest.raises(RuntimeError, match="cannot exceed CART_TOTAL_LIMIT"):
        create_app(
            {
                "TESTING": True,
                "PIZZA_CART_LIMIT": 4,
                "PIZZA_SLOT_CAPACITY": 4,
                "CART_TOTAL_LIMIT": 3,
            }
        )


def test_configured_three_pizza_window_can_exceed_two_pizza_default():
    configured = create_app(
        {
            "TESTING": True,
            "PIZZA_CART_LIMIT": 3,
            "PIZZA_SLOT_CAPACITY": 2,
            "PICKUP_SCHEDULE": '{"thursday":[{"start":"18:00","end":"20:00","pizzas":3}]}',
        }
    )

    assert configured.config["PIZZA_MAX_SLOT_CAPACITY"] == 3
    assert configured.config["CATEGORY_LIMITS"] == {"pizza": 3}


def test_pickup_schedule_can_limit_hours_and_vary_capacity(app):
    app.config["PICKUP_SCHEDULE"] = {
        "thursday": (
            ("17:00", "17:30", 2),
            ("18:00", "18:30", 3),
        ),
        "sunday": (("14:00", "17:00", 2),),
    }
    client = app.test_client()

    page = client.get("/")
    assert b"Thursday, August 6 at 5:00 PM" in page.data

    thursday = client.get("/api/slots?date=2026-08-06").get_json()["slots"]
    assert [(slot["time"], slot["remaining"]) for slot in thursday] == [
        ("5:00 PM", 2),
        ("5:15 PM", 2),
        ("5:30 PM", 2),
        ("6:00 PM", 3),
        ("6:15 PM", 3),
        ("6:30 PM", 3),
    ]

    sunday = client.get("/api/slots?date=2026-08-09").get_json()["slots"]
    assert sunday[0]["time"] == "2:00 PM"
    assert sunday[-1]["time"] == "5:00 PM"
    assert {slot["remaining"] for slot in sunday} == {2}


def test_date_pickup_schedule_override_wins_over_weekday(app):
    app.config["PICKUP_SCHEDULE"] = {
        "thursday": (("16:00", "20:00", 2),),
        "2026-08-06": (("18:00", "18:15", 3),),
    }
    client = app.test_client()

    payload = client.get("/api/slots?date=2026-08-06").get_json()
    assert [(slot["time"], slot["remaining"]) for slot in payload["slots"]] == [
        ("6:00 PM", 3),
        ("6:15 PM", 3),
    ]


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

    for filename in (
        "compagnon-medium.otf",
        "semplicita-book.otf",
        "semplicita-book-italic.otf",
    ):
        font = client.get(f"/static/fonts/{filename}")
        assert font.status_code == 200
        assert font.mimetype == "font/otf"
        assert font.data


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


def test_pickup_api_shows_remaining_pizzas_and_keeps_full_times_visible(app):
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
    assert set(payload["slots"][0]) == {
        "iso",
        "time",
        "available",
        "remaining",
        "status",
    }
    assert payload["slots"][0] == {
        "iso": service_at,
        "time": "4:00 PM",
        "available": False,
        "remaining": 0,
        "status": "Full",
    }
    assert payload["slots"][1]["remaining"] == 3
    assert payload["slots"][1]["status"] == "3 pizzas available"

    javascript = client.get("/static/app.js")
    checkout_javascript = client.get("/static/checkout.js")
    assert b"slot-choice-unavailable" in javascript.data
    assert b"slot.status" in javascript.data
    assert b'class="slot-capacity"' in javascript.data
    assert b'class="slot-capacity"' in checkout_javascript.data
    assert b"slot-choice-unavailable" in checkout_javascript.data


def test_pickup_dialog_renders_embedded_slots_before_refreshing(app):
    client = app.test_client()
    page = client.get("/")
    javascript = client.get("/static/app.js").get_data(as_text=True)
    checkout_javascript = client.get("/static/checkout.js").get_data(as_text=True)

    assert b'"slotsByDate"' in page.data
    for source in (javascript, checkout_javascript):
        cached = source.index("const cached = slotCache.get(date);")
        render = source.index("if (cached) renderSlots(cached);", cached)
        refresh = source.index("const payload = await fetchSlots(date);", render)
        assert cached < render < refresh


def test_saved_full_pickup_is_replaced_by_the_next_available_slot(app):
    full_slot = "2026-08-06T16:00:00-04:00"
    next_slot = "2026-08-06T16:15:00-04:00"
    app.extensions["capacity_store"].confirm_demo_order(
        service_at=full_slot,
        pizza_count=3,
        capacity=3,
        customer={"name": "Alex", "email": "alex@example.com", "phone": ""},
        notes="",
        tip_cents=0,
        cart=[{"item_id": "plain", "quantity": 3}],
    )

    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["service_at"] = full_slot

    response = client.get("/")

    assert response.status_code == 200
    assert b"Thursday, August 6 at 4:15 PM" in response.data
    assert b"Thursday, August 6 at 4:00 PM" not in response.data
    with client.session_transaction() as browser_session:
        assert browser_session["service_at"] == next_slot


def test_pickup_api_shows_partial_capacity_and_disables_slots_that_cannot_fit_cart(app):
    service_at = "2026-08-06T16:15:00-04:00"
    app.extensions["capacity_store"].confirm_demo_order(
        service_at=service_at,
        pizza_count=2,
        capacity=3,
        customer={"name": "Alex", "email": "alex@example.com", "phone": ""},
        notes="",
        tip_cents=0,
        cart=[{"item_id": "plain", "quantity": 2}],
    )

    client = app.test_client()
    token = csrf(client)
    added = client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 2},
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201

    payload = client.get("/api/slots?date=2026-08-06").get_json()
    partial = payload["slots"][1]
    assert partial == {
        "iso": service_at,
        "time": "4:15 PM",
        "available": False,
        "remaining": 1,
        "status": "1 pizza available",
    }


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


def test_demo_checkout_shows_tax_default_tip_and_inline_pickup_change(app):
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
    assert b'id="discount-code"' not in response.data
    assert b"Square-hosted checkout" in response.data
    assert b'id="checkout-change-pickup"' in response.data
    assert b'id="summary-lines"' in response.data
    assert b"pizza spots" not in response.data


def test_checkout_field_statuses_share_inline_typography(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    response = client.get("/checkout")
    css = client.get("/static/style.css").get_data(as_text=True)

    assert response.status_code == 200
    assert response.data.count(b"field-status field-status-required") == 4
    assert response.data.count(b"field-status field-status-optional") == 1
    assert b">First name <span class=" in response.data
    assert b">Last name <span class=" in response.data
    assert b">Email <span class=" in response.data
    assert b">Phone <span class=" in response.data
    assert b">Order notes <span class=" in response.data
    assert b'name="first_name" autocomplete="given-name"' in response.data
    assert b'name="last_name" autocomplete="family-name"' in response.data
    assert b'name="phone" type="tel" autocomplete="tel"' in response.data
    assert ".field-label { display: flex; align-items: baseline;" in css
    assert ".field-status { color: #666;" in css
    assert ".field-status-required { color: var(--red); }" in css


def test_checkout_can_remember_contact_information_in_the_browser(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    response = client.get("/checkout")
    javascript = client.get("/static/checkout.js").get_data(as_text=True)

    assert response.status_code == 200
    assert b'id="remember-contact" type="checkbox"' in response.data
    assert b"Remember my contact information" in response.data
    assert b"Saved only in this browser. Avoid on a shared device." not in response.data
    assert "pizzeriaMari.checkoutContact.v1" in javascript
    assert "window.localStorage.setItem" in javascript
    assert "window.localStorage.getItem" in javascript
    assert "window.localStorage.removeItem" in javascript
    assert "restoreRememberedContact();" in javascript
    assert "saveRememberedContact();" in javascript


def test_checkout_submission_is_native_for_mobile_navigation(app):
    javascript = app.test_client().get("/static/checkout.js").get_data(as_text=True)

    assert "checkoutForm?.addEventListener('submit'" not in javascript
    assert "dataset.submitting" not in javascript
    assert "Opening Square…" not in javascript
    assert "rememberedContactFields.forEach" in javascript
    assert "addEventListener('input'" in javascript


def test_checkout_requires_first_last_email_and_phone(app):
    client = app.test_client()
    token = csrf(client)
    client.post(
        "/api/cart",
        json={"item_id": "plain", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )

    missing_name = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "first_name": "Alex",
            "email": "alex@example.com",
            "phone": "5185550100",
        },
    )
    assert missing_name.status_code == 200
    assert b"Enter your first name, last name, and a valid email address." in missing_name.data

    missing_phone = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
        },
    )
    assert missing_phone.status_code == 200
    assert b"Enter a valid US phone number (10 digits or +1)." in missing_phone.data


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
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
            "tip_choice": "custom",
            "custom_tip": "4.25",
        },
    )
    assert response.status_code == 200
    assert b"Thanks, Alex Customer." in response.data
    assert b"$32.33" in response.data
