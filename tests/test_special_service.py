from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from app.menu import MenuItem, MenuSnapshot


SPECIAL_SERVICE_AT = "2026-09-21T16:00:00-04:00"
REGULAR_SERVICE_AT = "2026-09-17T16:00:00-04:00"


class SpecialServiceMenuProvider:
    def __init__(self, base_snapshot: MenuSnapshot) -> None:
        special_pizza = MenuItem(
            id="pizza-friends",
            name="Pizza Friends Collaboration Pie",
            category="pizza-friends",
            category_label="Pizza Friends",
            capacity_category="pizza",
            price_cents=3800,
            description="A one-day collaboration pie.",
            days_available=(0,),
        )
        self.snapshot_value = MenuSnapshot(
            groups=(
                *base_snapshot.groups,
                {
                    "id": "pizza-friends",
                    "label": "Pizza Friends",
                    "items": (special_pizza,),
                },
            ),
            items=(*base_snapshot.items, special_pizza),
            capacity_object_ids=base_snapshot.capacity_object_ids,
        )
        self.snapshot_calls = 0

    def snapshot(self) -> MenuSnapshot:
        self.snapshot_calls += 1
        return self.snapshot_value


@pytest.fixture()
def app():
    configured = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "TEST_NOW": datetime(
                2026, 9, 14, 12, tzinfo=ZoneInfo("America/New_York")
            ),
            "PICKUP_SCHEDULE": (
                '{"2026-09-21":['
                '{"start":"16:00","end":"17:00","pizzas":3}]}'
            ),
            "SPECIAL_SERVICE_DATES": "2026-09-21",
            "SPECIAL_SERVICE_CATEGORIES": "Pizza Friends",
        }
    )
    configured.extensions["menu_provider"] = SpecialServiceMenuProvider(
        configured.extensions["menu_provider"].snapshot()
    )
    return configured


def csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as browser_session:
        return browser_session["csrf_token"]


def set_pickup(client, service_at: str) -> None:
    with client.session_transaction() as browser_session:
        browser_session["service_at"] = service_at


def add_item(client, item_id: str):
    return client.post(
        "/api/cart",
        json={"item_id": item_id, "quantity": 1},
        headers={"X-CSRF-Token": csrf(client)},
    )


def test_special_and_regular_items_remain_visible_on_special_date(app):
    client = app.test_client()
    set_pickup(client, SPECIAL_SERVICE_AT)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-item-id="plain"' in page
    assert 'data-item-id="pizza-friends"' in page
    assert '"specialServiceDates": ["2026-09-21"]' in page
    assert '"specialServiceCategories": ["Pizza Friends"]' in page
    assert "/static/app.js?v=0.18.42" in page


def test_only_special_category_can_be_added_on_special_date(app):
    client = app.test_client()
    set_pickup(client, SPECIAL_SERVICE_AT)

    blocked = add_item(client, "plain")
    allowed = add_item(client, "pizza-friends")

    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == (
        "Only items from Pizza Friends are available for pickup on Monday, "
        "September 21. Change your pickup day to add Plain to your order."
    )
    assert allowed.status_code == 201


def test_days_available_still_limits_special_item_on_regular_date(app):
    client = app.test_client()
    set_pickup(client, REGULAR_SERVICE_AT)

    response = add_item(client, "pizza-friends")

    assert response.status_code == 409
    assert response.get_json()["error"] == (
        "Pizza Friends Collaboration Pie can only be ordered for pickup on Monday. "
        "Change your pickup day to add it to your order."
    )


def test_regular_cart_cannot_switch_to_special_service_date(app):
    client = app.test_client()
    set_pickup(client, REGULAR_SERVICE_AT)
    assert add_item(client, "plain").status_code == 201

    response = client.post(
        "/api/selected-slot",
        json={"service_at": SPECIAL_SERVICE_AT},
        headers={"X-CSRF-Token": csrf(client)},
    )

    assert response.status_code == 409
    assert "Only items from Pizza Friends" in response.get_json()["error"]


def test_stale_regular_cart_is_blocked_at_quantity_change_and_checkout(app):
    client = app.test_client()
    set_pickup(client, REGULAR_SERVICE_AT)
    added = add_item(client, "plain")
    line_id = added.get_json()["lines"][0]["id"]
    set_pickup(client, SPECIAL_SERVICE_AT)

    updated = client.patch(
        f"/api/cart/{line_id}",
        json={"quantity": 2},
        headers={"X-CSRF-Token": csrf(client)},
    )
    checkout = client.get("/checkout")

    assert updated.status_code == 409
    assert "Only items from Pizza Friends" in updated.get_json()["error"]
    assert checkout.status_code == 409
    assert b"Only items from Pizza Friends" in checkout.data


def test_special_service_configuration_requires_both_valid_settings():
    with pytest.raises(RuntimeError, match="configured together"):
        create_app(
            {
                "TESTING": True,
                "SPECIAL_SERVICE_DATES": "2026-09-21",
            }
        )

    with pytest.raises(RuntimeError, match="YYYY-MM-DD"):
        create_app(
            {
                "TESTING": True,
                "SPECIAL_SERVICE_DATES": "09/21/2026",
                "SPECIAL_SERVICE_CATEGORIES": "Pizza Friends",
            }
        )


def test_client_script_disables_regular_items_without_hiding_them():
    script = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text()

    assert "specialServiceAvailability" in script
    assert "Special menu only for this pickup date" in script
    assert "Only items from ${specialAvailability.label}" in script
    assert "button.disabled = true" in script

