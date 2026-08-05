from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import create_app
from app.square import SquareAPIError


def catalog_objects() -> list[dict]:
    categories = [
        {
            "type": "CATEGORY",
            "id": category_id,
            "category_data": {
                "name": name,
                "category_type": "REGULAR_CATEGORY",
            },
        }
        for category_id, name in (
            ("CAT_SEASONAL", "Seasonal Special Pies"),
            ("CAT_TRADITIONAL", "Traditional Pies"),
            ("CAT_MARI", "Mari Pies"),
        )
    ]

    def modifier_list(list_id: str, name: str, modifier_id: str, price: int):
        return {
            "type": "MODIFIER_LIST",
            "id": list_id,
            "modifier_list_data": {
                "name": name,
                "selection_type": "MULTIPLE",
                "modifiers": [
                    {
                        "type": "MODIFIER",
                        "id": modifier_id,
                        "version": 12,
                        "modifier_data": {
                            "name": "Pepperoni" if "ADD" in list_id else "Double Cut",
                            "price_money": {"amount": price, "currency": "USD"},
                        },
                    }
                ],
            },
        }

    modifiers = [
        modifier_list("ADD_WHOLE", "Whole Pie Additions", "MOD_WHOLE_PEP", 500),
        modifier_list("ADD_FIRST", "First Half Pie Additions", "MOD_FIRST_PEP", 300),
        modifier_list("ADD_SECOND", "Second Half Pie Additions", "MOD_SECOND_PEP", 300),
        modifier_list("PREFERENCES", "Preferences", "MOD_DOUBLE_CUT", 0),
    ]
    image = {
        "type": "IMAGE",
        "id": "IMAGE_PLAIN",
        "image_data": {"url": "https://example.com/plain.jpg"},
    }
    item = {
        "type": "ITEM",
        "id": "ITEM_PLAIN",
        "item_data": {
            "name": "Plain",
            "description_plaintext": "Tomato, mozzarella, basil.",
            "categories": [{"id": "CAT_TRADITIONAL"}],
            "image_ids": ["IMAGE_PLAIN"],
            "modifier_list_info": [
                {"modifier_list_id": "ADD_WHOLE", "enabled": True},
                {"modifier_list_id": "ADD_FIRST", "enabled": True},
                {"modifier_list_id": "ADD_SECOND", "enabled": True},
                {
                    "modifier_list_id": "PREFERENCES",
                    "enabled": True,
                    "min_selected_modifiers": 0,
                    "max_selected_modifiers": 1,
                },
            ],
            "variations": [
                {
                    "type": "ITEM_VARIATION",
                    "id": "VAR_PLAIN",
                    "version": 14,
                    "item_variation_data": {
                        "name": "Regular",
                        "sellable": True,
                        "price_money": {"amount": 2600, "currency": "USD"},
                    },
                }
            ],
        },
    }
    return [*categories, *modifiers, image, item]


class SquareFixture:
    def __init__(self, *, payment_fails: bool = False, orders: list[dict] | None = None):
        self.requests: list[httpx.Request] = []
        self.payment_fails = payment_fails
        self.orders = orders or []
        self.canceled = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["square-version"] == "2026-07-15"
        if request.method == "GET" and request.url.path == "/v2/catalog/list":
            return httpx.Response(200, json={"objects": catalog_objects()})
        if request.url.path == "/v2/orders/search":
            return httpx.Response(200, json={"orders": self.orders})
        if request.url.path == "/v2/orders/calculate":
            order = json.loads(request.read())["order"]
            has_addition = any(line.get("modifiers") for line in order["line_items"])
            subtotal = 3100 if has_addition else 2600
            tax = round(subtotal * 0.08)
            return httpx.Response(
                200,
                json={
                    "order": {
                        "total_money": {
                            "amount": subtotal + tax,
                            "currency": "USD",
                        },
                        "total_tax_money": {"amount": tax, "currency": "USD"},
                        "total_discount_money": {"amount": 0, "currency": "USD"},
                    }
                },
            )
        if request.method == "POST" and request.url.path == "/v2/orders":
            body = request.read().decode()
            assert '"catalog_object_id":"VAR_PLAIN"' in body
            assert '"pickup_at":"2026-08-06T16:00:00-04:00"' in body
            return httpx.Response(
                200,
                json={
                    "order": {
                        "id": "SQUARE_ORDER_12345678",
                        "version": 1,
                        "location_id": "LOCATION",
                        "total_money": {"amount": 3348, "currency": "USD"},
                    }
                },
            )
        if request.url.path == "/v2/payments":
            body = request.read().decode()
            assert '"source_id":"sandbox-card-token"' in body
            assert '"amount":3348' in body
            assert '"amount":465' in body
            if self.payment_fails:
                return httpx.Response(
                    402,
                    json={
                        "errors": [
                            {
                                "category": "PAYMENT_METHOD_ERROR",
                                "code": "CARD_DECLINED",
                                "detail": "The card was declined.",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "payment": {
                        "id": "PAYMENT",
                        "status": "COMPLETED",
                        "receipt_url": "https://squareup.com/receipt/preview",
                    }
                },
            )
        if request.method == "PUT" and request.url.path.startswith("/v2/orders/"):
            self.canceled = True
            return httpx.Response(200, json={"order": {"id": "SQUARE_ORDER_12345678"}})
        raise AssertionError(f"Unexpected Square request: {request.method} {request.url}")


@pytest.fixture()
def square_app():
    fixture = SquareFixture()
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "SQUARE_APPLICATION_ID": "sandbox-app-id",
            "SQUARE_LOCATION_ID": "LOCATION",
            "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
            "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(fixture),
            "TEST_NOW": datetime(
                2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )
    app.square_fixture = fixture
    return app


def csrf(client) -> str:
    client.get("/")
    with client.session_transaction() as browser_session:
        return browser_session["csrf_token"]


def test_square_catalog_drives_items_images_and_modifier_groups(square_app):
    client = square_app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Plain" in response.data
    assert b"Tomato, mozzarella, basil." in response.data
    assert b"https://example.com/plain.jpg" in response.data
    assert b"VAR_PLAIN" in response.data
    assert b"MOD_WHOLE_PEP" in response.data
    assert b"MOD_FIRST_PEP" in response.data
    assert b"MOD_SECOND_PEP" in response.data
    assert b"Preferences" in response.data
    assert b"Cherry Tomato" not in response.data
    assert b"sandbox.web.squarecdn.com" not in response.data


def test_square_checkout_creates_scheduled_order_and_payment(square_app):
    client = square_app.test_client()
    token = csrf(client)
    addition_id = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
        .additions[0]
        .id
    )
    added = client.post(
        "/api/cart",
        json={
            "item_id": "VAR_PLAIN",
            "quantity": 1,
            "additions": [{"id": addition_id, "placement": "whole"}],
            "modifier_selections": {"PREFERENCES": ["MOD_DOUBLE_CUT"]},
        },
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201
    with client.session_transaction() as browser_session:
        stored_modifier = browser_session["cart"][0]["modifiers"][0]
        assert "name" not in stored_modifier
        assert "price_cents" not in stored_modifier

    checkout = client.get("/checkout")
    assert checkout.status_code == 200
    assert b"sandbox.web.squarecdn.com/v1/square.js" in checkout.data
    assert b'id="card-container"' in checkout.data
    csp = checkout.headers["Content-Security-Policy"]
    assert "https://sandbox.web.squarecdn.com" in csp
    assert "https://pci-connect.squareupsandbox.com" in csp
    assert "test-token-not-a-real-secret" not in checkout.get_data(as_text=True)
    with client.session_transaction() as browser_session:
        attempt_id = browser_session["checkout_attempt_id"]

    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "checkout_attempt_id": attempt_id,
            "source_id": "sandbox-card-token",
            "verification_total_cents": "3813",
            "name": "Alex Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
            "tip_choice": "15",
            "notes": "Allergy note",
        },
    )

    assert response.status_code == 200
    assert b"Thanks, Alex Customer." in response.data
    assert b"View Square receipt" in response.data


def test_declined_payment_cancels_unpaid_square_order():
    fixture = SquareFixture(payment_fails=True)
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "SQUARE_APPLICATION_ID": "sandbox-app-id",
            "SQUARE_LOCATION_ID": "LOCATION",
            "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
            "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(fixture),
            "TEST_NOW": datetime(
                2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )
    client = app.test_client()
    token = csrf(client)
    menu = app.extensions["menu_provider"].snapshot()
    addition_id = menu.items_by_id["VAR_PLAIN"].additions[0].id
    client.post(
        "/api/cart",
        json={
            "item_id": "VAR_PLAIN",
            "quantity": 1,
            "additions": [{"id": addition_id, "placement": "whole"}],
            "modifier_selections": {"PREFERENCES": []},
        },
        headers={"X-CSRF-Token": token},
    )
    client.get("/checkout")
    with client.session_transaction() as browser_session:
        attempt_id = browser_session["checkout_attempt_id"]
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "checkout_attempt_id": attempt_id,
            "source_id": "sandbox-card-token",
            "verification_total_cents": "3813",
            "name": "Alex",
            "email": "alex@example.com",
            "tip_choice": "15",
        },
    )

    assert response.status_code == 200
    assert b"The card was declined." in response.data
    assert fixture.canceled is True


def test_square_scheduled_orders_drive_full_slot_status():
    fixture = SquareFixture(
        orders=[
            {
                "id": "EXISTING_ORDER",
                "state": "OPEN",
                "fulfillments": [
                    {
                        "type": "PICKUP",
                        "pickup_details": {"pickup_at": "2026-08-06T20:00:00Z"},
                    }
                ],
                "line_items": [
                    {"catalog_object_id": "VAR_PLAIN", "quantity": "3"}
                ],
            }
        ]
    )
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "SQUARE_APPLICATION_ID": "sandbox-app-id",
            "SQUARE_LOCATION_ID": "LOCATION",
            "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
            "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(fixture),
            "TEST_NOW": datetime(
                2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )
    payload = app.test_client().get("/api/slots?date=2026-08-06").get_json()

    assert payload["slots"][0] == {
        "iso": "2026-08-06T16:00:00-04:00",
        "time": "4:00 PM",
        "available": False,
        "status": "Full",
    }


def test_square_network_errors_do_not_expose_access_token(square_app):
    error = SquareAPIError("Square could not be reached.")
    assert "test-token" not in str(error)
