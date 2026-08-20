from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from app.menu import MenuItem, MenuSnapshot


class DayRestrictedMenuProvider:
    def __init__(self, base_snapshot: MenuSnapshot) -> None:
        cookie = MenuItem(
            id="weekend-cookie",
            name="Chocolate Chip Cookie",
            category="desserts",
            category_label="Desserts",
            capacity_category=None,
            price_cents=500,
            description="A very good cookie.",
            days_available=(4, 5, 6),
        )
        self.snapshot_value = MenuSnapshot(
            groups=(
                *base_snapshot.groups,
                {"id": "desserts", "label": "Desserts", "items": (cookie,)},
            ),
            items=(*base_snapshot.items, cookie),
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
                2026, 8, 6, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )
    configured.extensions["menu_provider"] = DayRestrictedMenuProvider(
        configured.extensions["menu_provider"].snapshot()
    )
    return configured


def csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as session:
        return session["csrf_token"]


def set_pickup(client, service_at: str) -> None:
    with client.session_transaction() as session:
        session["service_at"] = service_at


def add_cookie(client):
    return client.post(
        "/api/cart",
        json={"item_id": "weekend-cookie", "quantity": 1},
        headers={"X-CSRF-Token": csrf(client)},
    )


def test_restricted_item_remains_visible_and_has_client_side_day_data(app):
    client = app.test_client()
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-item-id="weekend-cookie"' in page
    assert '"days_available": [4, 5, 6]' in page
    assert '"days_available_label": "Friday, Saturday, or Sunday"' in page
    assert "/static/app.js?v=0.18.35" in page
    assert "cookie-availability.js" not in page


@pytest.mark.parametrize(
    "service_at",
    (
        "2026-08-07T16:00:00-04:00",
        "2026-08-08T11:00:00-04:00",
        "2026-08-09T11:00:00-04:00",
    ),
)
def test_restricted_item_can_be_added_on_each_configured_day(app, service_at):
    client = app.test_client()
    set_pickup(client, service_at)
    assert add_cookie(client).status_code == 201


def test_restricted_item_cannot_be_added_on_an_unconfigured_day(app):
    client = app.test_client()
    set_pickup(client, "2026-08-06T16:00:00-04:00")
    response = add_cookie(client)

    assert response.status_code == 409
    assert response.get_json()["error"] == (
        "Chocolate Chip Cookie can only be ordered for pickup on Friday, "
        "Saturday, or Sunday. Change your pickup day to add it to your order."
    )


def test_cart_with_restricted_item_cannot_switch_to_an_unconfigured_day(app):
    client = app.test_client()
    set_pickup(client, "2026-08-07T16:00:00-04:00")
    assert add_cookie(client).status_code == 201
    response = client.post(
        "/api/selected-slot",
        json={"service_at": "2026-08-06T16:00:00-04:00"},
        headers={"X-CSRF-Token": csrf(client)},
    )

    assert response.status_code == 409
    assert "Chocolate Chip Cookie" in response.get_json()["error"]


def test_stale_cart_is_blocked_before_checkout(app):
    client = app.test_client()
    set_pickup(client, "2026-08-07T16:00:00-04:00")
    assert add_cookie(client).status_code == 201
    set_pickup(client, "2026-08-06T16:00:00-04:00")
    response = client.get("/checkout")

    assert response.status_code == 409
    assert b"Chocolate Chip Cookie can only be ordered" in response.data


def test_slot_change_performs_only_the_routes_normal_menu_lookup(app):
    client = app.test_client()
    token = csrf(client)
    provider = app.extensions["menu_provider"]
    provider.snapshot_calls = 0

    response = client.post(
        "/api/selected-slot",
        json={"service_at": "2026-08-06T16:15:00-04:00"},
        headers={"X-CSRF-Token": token},
    )

    assert response.status_code == 200
    assert provider.snapshot_calls == 1


def test_client_script_disables_add_button_and_shows_clear_warning():
    script = (Path(__file__).parents[1] / "app" / "static" / "app.js").read_text()

    assert "updateMenuDayAvailability" in script
    assert "itemAvailabilityMessage.hidden = false" in script
    assert "Change your pickup day to add it to your order." in script
