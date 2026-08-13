from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pytest

from app import create_app
from app.menu import MenuItem, MenuSnapshot


class CookieMenuProvider:
    def __init__(self, base_snapshot: MenuSnapshot) -> None:
        cookie = MenuItem(
            id="weekend-cookie",
            name="Chocolate Chip Cookie (Friday–Sunday)",
            category="desserts",
            category_label="Desserts",
            capacity_category=None,
            price_cents=500,
            description="Available Friday through Sunday.",
        )
        self.snapshot_value = MenuSnapshot(
            groups=(
                *base_snapshot.groups,
                {"id": "desserts", "label": "Desserts", "items": (cookie,)},
            ),
            items=(*base_snapshot.items, cookie),
            capacity_object_ids=base_snapshot.capacity_object_ids,
        )

    def snapshot(self) -> MenuSnapshot:
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
    configured.extensions["menu_provider"] = CookieMenuProvider(
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


def test_cookie_is_hidden_for_thursday_and_script_tracks_date_changes(app):
    client = app.test_client()
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    cookie_button = re.search(
        r'<button\b(?=[^>]*data-item-id="weekend-cookie")[^>]*>', page
    )
    assert cookie_button is not None
    assert " hidden" in cookie_button.group(0)
    assert "/static/cookie-availability.js?v=0.18.20" in page

    javascript = client.get("/static/cookie-availability.js").get_data(as_text=True)
    assert "weekday === 5 || weekday === 6 || weekday === 0" in javascript
    assert "MutationObserver" in javascript


@pytest.mark.parametrize(
    "service_at",
    (
        "2026-08-07T16:00:00-04:00",  # Friday
        "2026-08-08T11:00:00-04:00",  # Saturday
        "2026-08-09T11:00:00-04:00",  # Sunday
    ),
)
def test_cookie_is_visible_and_can_be_added_friday_through_sunday(
    app, service_at
):
    client = app.test_client()
    set_pickup(client, service_at)

    page = client.get("/").get_data(as_text=True)
    cookie_button = re.search(
        r'<button\b(?=[^>]*data-item-id="weekend-cookie")[^>]*>', page
    )
    assert cookie_button is not None
    assert " hidden" not in cookie_button.group(0)

    response = add_cookie(client)
    assert response.status_code == 201
    assert response.get_json()["lines"][0]["name"].startswith(
        "Chocolate Chip Cookie"
    )


def test_cookie_cannot_be_added_for_thursday_even_with_direct_api_request(app):
    client = app.test_client()
    set_pickup(client, "2026-08-06T16:00:00-04:00")

    response = add_cookie(client)

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Cookies are only available Friday through Sunday."
    }


def test_cart_with_cookie_cannot_switch_to_thursday(app):
    client = app.test_client()
    set_pickup(client, "2026-08-07T16:00:00-04:00")
    assert add_cookie(client).status_code == 201

    response = client.post(
        "/api/selected-slot",
        json={"service_at": "2026-08-06T16:00:00-04:00"},
        headers={"X-CSRF-Token": csrf(client)},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == (
        "Cookies are only available Friday through Sunday."
    )


def test_stale_thursday_cookie_cart_is_blocked_before_checkout(app):
    client = app.test_client()
    set_pickup(client, "2026-08-07T16:00:00-04:00")
    assert add_cookie(client).status_code == 201
    set_pickup(client, "2026-08-06T16:00:00-04:00")

    response = client.get("/checkout")

    assert response.status_code == 409
    assert b"Cookies are available Friday through Sunday" in response.data
    assert b"Return to your cart" in response.data
