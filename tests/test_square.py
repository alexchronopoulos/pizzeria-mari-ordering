from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import create_app
from app.square import SquareAPIError
from app.startup_performance import prepare_app_for_serving


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
            ("CAT_SIDES", "Sides"),
            ("CAT_DESSERTS", "Desserts"),
            ("CAT_SALADS", "Salads"),
            ("CAT_DRINKS", "Drinks"),
        )
    ]

    def modifier_list(list_id: str, name: str, modifier_id: str, price: int):
        return {
            "type": "MODIFIER_LIST",
            "id": list_id,
            "modifier_list_data": {
                "name": name,
                "selection_type": "MULTIPLE",
                "min_selected_modifiers": 0,
                "max_selected_modifiers": 0,
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
        modifier_list("SIDES", "Sides & Desserts", "MOD_SIDE_SALAD", 1200),
        modifier_list("DRINKS", "Drinks", "MOD_DRINK_COLA", 300),
    ]
    modifiers[3]["modifier_list_data"]["modifiers"].append(
        {
            "type": "MODIFIER",
            "id": "MOD_NO_BASIL",
            "version": 12,
            "modifier_data": {
                "name": "No Basil",
                "price_money": {"amount": 0, "currency": "USD"},
            },
        }
    )
    modifiers[0]["modifier_list_data"]["modifiers"].append(
        {
            "type": "MODIFIER",
            "id": "MOD_WHOLE_MUSHROOM",
            "version": 12,
            "modifier_data": {
                "name": "Oyster Mushrooms",
                "price_money": {"amount": 600, "currency": "USD"},
            },
        }
    )
    modifiers[1]["modifier_list_data"]["modifiers"].extend(
        [
            {
                "type": "MODIFIER",
                "id": "MOD_FIRST_MUSHROOM",
                "version": 12,
                "modifier_data": {
                    "name": "First Half Oyster Mushrooms",
                    "price_money": {"amount": 350, "currency": "USD"},
                },
            },
            {
                "type": "MODIFIER",
                "id": "MOD_FIRST_ONLY",
                "version": 12,
                "modifier_data": {
                    "name": "Half-list-only option",
                    "price_money": {"amount": 100, "currency": "USD"},
                },
            },
        ]
    )
    modifiers[2]["modifier_list_data"]["modifiers"].append(
        {
            "type": "MODIFIER",
            "id": "MOD_SECOND_MUSHROOM",
            "version": 12,
            "modifier_data": {
                # Deliberately not a textual match: real Square catalogs often
                # keep parallel placement lists aligned but label them
                # differently. The list position is the final correspondence.
                "name": "Mushroom surcharge",
                "price_money": {"amount": 375, "currency": "USD"},
            },
        }
    )
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
                    "min_selected_modifiers": -1,
                    "max_selected_modifiers": -1,
                },
                {"modifier_list_id": "SIDES", "enabled": True},
                {"modifier_list_id": "DRINKS", "enabled": True},
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

    def simple_item(
        item_id: str,
        variation_id: str,
        name: str,
        category_id: str,
        price: int,
    ) -> dict:
        return {
            "type": "ITEM",
            "id": item_id,
            "item_data": {
                "name": name,
                "categories": [{"id": category_id}],
                "variations": [
                    {
                        "type": "ITEM_VARIATION",
                        "id": variation_id,
                        "version": 14,
                        "item_variation_data": {
                            "name": "Regular",
                            "sellable": True,
                            "price_money": {"amount": price, "currency": "USD"},
                        },
                    }
                ],
            },
        }

    non_pizza_items = [
        simple_item("ITEM_SIDE", "VAR_SIDE", "Garlic Knots", "CAT_SIDES", 800),
        simple_item("ITEM_DESSERT", "VAR_DESSERT", "Chocolate Cookie", "CAT_DESSERTS", 500),
        simple_item("ITEM_SALAD", "VAR_SALAD", "Cucumber Salad", "CAT_SALADS", 1200),
        simple_item("ITEM_DRINK", "VAR_DRINK", "Sparkling Water", "CAT_DRINKS", 300),
    ]
    return [*categories, *modifiers, image, item, *non_pizza_items]


class SquareFixture:
    def __init__(self, *, orders: list[dict] | None = None):
        self.requests: list[httpx.Request] = []
        self.orders = orders or []
        self.canceled = False
        self.created_order: dict | None = None
        self.payment_status = "PENDING"
        self.order_state_override: str | None = None
        self.inventory_counts: dict[str, int] = {}
        self.unflagged_inventory_ids: set[str] = set()
        self.inventory_aliases: dict[str, str] = {}
        self.inventory_error = False

    def _catalog_objects(self) -> list[dict]:
        objects = catalog_objects()
        for displayed_id, alias_id in self.inventory_aliases.items():
            for catalog_object in objects:
                variations = catalog_object.get("item_data", {}).get(
                    "variations", []
                )
                displayed = next(
                    (
                        variation
                        for variation in variations
                        if variation.get("id") == displayed_id
                    ),
                    None,
                )
                if displayed is None:
                    continue
                alias = json.loads(json.dumps(displayed))
                alias["id"] = alias_id
                alias["type"] = "ITEM_VARIATION"
                alias["present_at_all_locations"] = False
                alias["absent_at_location_ids"] = ["LOCATION"]
                alias_data = alias.setdefault("item_variation_data", {})
                alias_data["item_id"] = catalog_object["id"]
                alias_data["name"] = "Secondary inventory variation"
                objects.append(alias)
                break
        return objects

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["square-version"] == "2026-07-15"
        if request.method == "GET" and request.url.path == "/v2/catalog/list":
            assert "ITEM_VARIATION" in request.url.params["types"].split(",")
            objects = self._catalog_objects()
            for catalog_object in objects:
                for variation in catalog_object.get("item_data", {}).get(
                    "variations", []
                ):
                    if (
                        variation.get("id") in self.inventory_counts
                        and variation.get("id")
                        not in self.unflagged_inventory_ids
                    ):
                        variation.setdefault("item_variation_data", {})[
                            "track_inventory"
                        ] = True
            return httpx.Response(200, json={"objects": objects})
        if (
            request.method == "POST"
            and request.url.path == "/v2/inventory/counts/batch-retrieve"
        ):
            if self.inventory_error:
                return httpx.Response(
                    503,
                    json={"errors": [{"code": "SERVICE_UNAVAILABLE"}]},
                )
            body = json.loads(request.read())
            assert body["location_ids"] == ["LOCATION"]
            assert body["states"] == ["IN_STOCK"]
            objects = self._catalog_objects()
            expected_ids = {
                catalog_object["id"]
                for catalog_object in objects
                if catalog_object.get("type") in {"ITEM", "ITEM_VARIATION"}
            }
            expected_ids.update(
                variation["id"]
                for catalog_object in objects
                for variation in catalog_object.get("item_data", {}).get(
                    "variations", []
                )
            )
            assert set(body["catalog_object_ids"]) == expected_ids
            return httpx.Response(
                200,
                json={
                    "counts": [
                        {
                            "catalog_object_id": object_id,
                            "location_id": "LOCATION",
                            "state": "IN_STOCK",
                            "quantity": str(quantity),
                        }
                        for object_id, quantity in self.inventory_counts.items()
                    ]
                },
            )
        if request.url.path == "/v2/orders/search":
            orders = list(self.orders)
            if self.created_order:
                orders.append(self.created_order)
            return httpx.Response(200, json={"orders": orders})
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
        if (
            request.method == "POST"
            and request.url.path == "/v2/online-checkout/payment-links"
        ):
            body = json.loads(request.read())
            order = body["order"]
            assert order["line_items"][0]["catalog_object_id"] == "VAR_PLAIN"
            pickup = order["fulfillments"][0]["pickup_details"]
            assert pickup["pickup_at"] == "2026-08-06T16:00:00-04:00"
            assert pickup["recipient"]["display_name"] == "Alex Customer"
            assert pickup["recipient"]["email_address"] == "alex@example.com"
            assert pickup["recipient"]["phone_number"] == "+15185550100"
            assert set(body["checkout_options"]) == {
                "allow_tipping",
                "enable_coupon",
                "redirect_url",
            }
            assert body["checkout_options"]["allow_tipping"] is True
            assert body["checkout_options"]["enable_coupon"] is True
            assert body["checkout_options"]["redirect_url"].startswith(
                "https://orders.example.test/checkout/complete?attempt="
            )
            uuid.UUID(body["idempotency_key"])
            assert "pre_populated_data" not in body
            self.created_order = {
                **order,
                "id": "SQUARE_ORDER_12345678",
                "version": 1,
                "state": "DRAFT",
                "created_at": "2026-08-04T16:00:00Z",
                "total_money": {"amount": 3348, "currency": "USD"},
            }
            return httpx.Response(
                200,
                json={
                    "payment_link": {
                        "id": "PAYMENT_LINK",
                        "order_id": "SQUARE_ORDER_12345678",
                        "url": "https://sandbox.square.link/u/test-checkout",
                    },
                    "related_resources": {"orders": [self.created_order]},
                },
            )
        if request.method == "GET" and request.url.path == "/v2/orders/SQUARE_ORDER_12345678":
            assert self.created_order is not None
            state = self.order_state_override or (
                "OPEN" if self.payment_status == "COMPLETED" else "DRAFT"
            )
            order = {**self.created_order, "state": state}
            if state == "OPEN":
                order["tenders"] = [{"id": "PAYMENT"}]
            return httpx.Response(200, json={"order": order})
        if request.method == "GET" and request.url.path == "/v2/payments/PAYMENT":
            return httpx.Response(
                200,
                json={
                    "payment": {
                        "id": "PAYMENT",
                        "status": self.payment_status,
                        "buyer_email_address": "alex@example.com",
                        "total_money": {"amount": 3813, "currency": "USD"},
                        "receipt_url": "https://squareup.com/receipt/preview",
                    }
                },
            )
        if request.method == "PUT" and request.url.path.startswith("/v2/orders/"):
            self.canceled = True
            if self.created_order:
                self.created_order["state"] = "CANCELED"
            return httpx.Response(200, json={"order": {"id": "SQUARE_ORDER_12345678"}})
        raise AssertionError(f"Unexpected Square request: {request.method} {request.url}")


class GiftCardFixture(SquareFixture):
    def __init__(self, *, gift_amount: int):
        super().__init__()
        self.gift_amount = gift_amount
        self.payments: dict[str, dict] = {}
        self.payment_bodies: list[dict] = []
        self.pay_order_body: dict | None = None
        self.canceled_payment_ids: list[str] = []
        self.pay_order_failures = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v2/orders":
            self.requests.append(request)
            body = json.loads(request.read())
            order = body["order"]
            uuid.UUID(body["idempotency_key"])
            assert order["reference_id"].startswith("PMGC-")
            assert order["line_items"][0]["catalog_object_id"] == "VAR_PLAIN"
            pickup = order["fulfillments"][0]["pickup_details"]
            assert pickup["pickup_at"] == "2026-08-06T16:00:00-04:00"
            self.created_order = {
                **order,
                "id": "GIFT_ORDER_12345678",
                "version": 1,
                "state": "OPEN",
                "created_at": "2026-08-04T16:00:00Z",
                "total_money": {"amount": 3348, "currency": "USD"},
                "tenders": [],
            }
            return httpx.Response(200, json={"order": self.created_order})
        if request.method == "GET" and path == "/v2/orders/GIFT_ORDER_12345678":
            self.requests.append(request)
            assert self.created_order is not None
            return httpx.Response(200, json={"order": self.created_order})
        if request.method == "POST" and path == "/v2/payments":
            self.requests.append(request)
            assert self.created_order is not None
            body = json.loads(request.read())
            self.payment_bodies.append(body)
            assert body["autocomplete"] is False
            assert body["order_id"] == "GIFT_ORDER_12345678"
            assert body["location_id"] == "LOCATION"
            requested = body["amount_money"]["amount"]
            is_gift = body["source_id"] == "gift-token"
            payment_id = "GIFT_PAYMENT" if is_gift else "CARD_PAYMENT"
            approved = min(self.gift_amount, requested) if is_gift else requested
            payment = {
                "id": payment_id,
                "status": "APPROVED",
                "source_type": "CARD",
                "amount_money": {"amount": approved, "currency": "USD"},
                "total_money": {"amount": approved, "currency": "USD"},
                "card_details": {
                    "status": "AUTHORIZED",
                    "card": {
                        "card_brand": "SQUARE_GIFT_CARD" if is_gift else "VISA",
                        "last_4": "0000" if is_gift else "1111",
                    },
                },
            }
            self.payments[payment_id] = payment
            self.created_order["tenders"].append(
                {
                    "id": payment_id,
                    "payment_id": payment_id,
                    "amount_money": {"amount": approved, "currency": "USD"},
                    "card_details": {"status": "AUTHORIZED"},
                }
            )
            self.created_order["version"] += 1
            return httpx.Response(200, json={"payment": payment})
        if request.method == "GET" and path.startswith("/v2/payments/"):
            self.requests.append(request)
            payment_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"payment": self.payments[payment_id]})
        if (
            request.method == "POST"
            and path == "/v2/orders/GIFT_ORDER_12345678/pay"
        ):
            self.requests.append(request)
            if self.pay_order_failures:
                self.pay_order_failures -= 1
                return httpx.Response(503, json={"errors": [{"code": "UNAVAILABLE"}]})
            self.pay_order_body = json.loads(request.read())
            assert sum(
                self.payments[payment_id]["amount_money"]["amount"]
                for payment_id in self.pay_order_body["payment_ids"]
            ) == 3348
            for payment in self.payments.values():
                payment["status"] = "COMPLETED"
                payment["receipt_url"] = (
                    f"https://squareup.com/receipt/{payment['id'].lower()}"
                )
                payment["card_details"]["status"] = "CAPTURED"
            self.created_order["state"] = "COMPLETED"
            self.created_order["version"] += 1
            return httpx.Response(200, json={"order": self.created_order})
        if request.method == "POST" and path.endswith("/cancel"):
            self.requests.append(request)
            payment_id = path.split("/")[-2]
            self.canceled_payment_ids.append(payment_id)
            self.payments[payment_id]["status"] = "CANCELED"
            self.payments[payment_id]["card_details"]["status"] = "VOIDED"
            return httpx.Response(
                200, json={"payment": self.payments[payment_id]}
            )
        if request.method == "PUT" and path == "/v2/orders/GIFT_ORDER_12345678":
            self.requests.append(request)
            self.canceled = True
            self.created_order["state"] = "CANCELED"
            return httpx.Response(200, json={"order": self.created_order})
        return super().__call__(request)


@pytest.fixture()
def square_app():
    fixture = SquareFixture()
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "PUBLIC_BASE_URL": "https://orders.example.test",
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


def gift_card_app(*, gift_amount: int):
    fixture = GiftCardFixture(gift_amount=gift_amount)
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "PUBLIC_BASE_URL": "https://orders.example.test",
            "SQUARE_LOCATION_ID": "LOCATION",
            "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
            "SQUARE_APPLICATION_ID": "sandbox-sq0idb-public-test-id",
            "SQUARE_GIFT_CARDS_ENABLED": True,
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
    assert b"Sides &amp; Desserts" not in response.data
    assert b"MOD_SIDE_SALAD" not in response.data
    assert b"MOD_DRINK_COLA" not in response.data
    assert b"Cherry Tomato" not in response.data
    assert b"sandbox.web.squarecdn.com" not in response.data

    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )
    assert [addition.name for addition in item.additions] == [
        "Pepperoni",
        "Oyster Mushrooms",
    ]
    assert set(item.additions[0].placements) == {
        "whole",
        "first_half",
        "second_half",
    }
    assert [group.name for group in item.modifier_groups] == ["Preferences"]
    assert item.modifier_groups[0].min_selected == 0
    assert item.modifier_groups[0].max_selected is None


def test_square_inventory_displays_one_through_four_as_low_stock(square_app):
    square_app.square_fixture.inventory_counts.update(
        {
            "VAR_SIDE": 1,
            "VAR_DESSERT": 4,
            "VAR_SALAD": 5,
            "VAR_DRINK": 0,
        }
    )
    client = square_app.test_client()
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Low stock · 1 left" in html
    assert "Low stock · 4 left" in html
    assert "Low stock · 5 left" not in html
    assert html.count("Low stock ·") == 2
    drink_button = re.search(
        r'<button\b(?=[^>]*data-item-id="VAR_DRINK")[^>]*>', html
    )
    assert drink_button is not None
    assert "disabled" in drink_button.group(0)

    menu = square_app.extensions["menu_provider"].snapshot()
    side = menu.items_by_id["VAR_SIDE"]
    dessert = menu.items_by_id["VAR_DESSERT"]
    salad = menu.items_by_id["VAR_SALAD"]
    drink = menu.items_by_id["VAR_DRINK"]
    pizza = menu.items_by_id["VAR_PLAIN"]
    assert (side.stock_count, side.low_stock, side.available) == (1, True, True)
    assert (dessert.stock_count, dessert.low_stock, dessert.available) == (
        4,
        True,
        True,
    )
    assert (salad.stock_count, salad.low_stock, salad.available) == (
        5,
        False,
        True,
    )
    assert (drink.stock_count, drink.low_stock, drink.available) == (
        0,
        False,
        False,
    )
    assert (pizza.stock_count, pizza.low_stock, pizza.available) == (
        None,
        False,
        True,
    )

    inventory_requests = [
        request
        for request in square_app.square_fixture.requests
        if request.url.path == "/v2/inventory/counts/batch-retrieve"
    ]
    assert len(inventory_requests) == 1
    javascript = client.get("/static/app.js").get_data(as_text=True)
    assert "activeItem.low_stock" in javascript
    assert "#item-stock" in javascript
    css = client.get("/static/style.css").get_data(as_text=True)
    assert (
        ".menu-card-copy em.menu-stock { margin-top: 9px; "
        "color: var(--paper); }"
    ) in css

    token = csrf(client)
    too_many = client.post(
        "/api/cart",
        json={"item_id": "VAR_SIDE", "quantity": 2},
        headers={"X-CSRF-Token": token},
    )
    assert too_many.status_code == 409
    assert "Only 1 Garlic Knots left in stock." in too_many.get_json()["error"]


def test_square_inventory_uses_positive_count_when_catalog_flag_is_omitted(
    square_app,
):
    square_app.square_fixture.inventory_counts["VAR_PLAIN"] = 1
    square_app.square_fixture.unflagged_inventory_ids.add("VAR_PLAIN")

    response = square_app.test_client().get("/")
    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )

    assert response.status_code == 200
    assert b"Low stock \xc2\xb7 1 left" in response.data
    assert (item.stock_count, item.low_stock, item.available) == (1, True, True)


def test_square_inventory_matches_a_secondary_variation_for_single_variation_item(
    square_app,
):
    square_app.square_fixture.inventory_aliases["VAR_PLAIN"] = (
        "VAR_PLAIN_INVENTORY"
    )
    square_app.square_fixture.inventory_counts["VAR_PLAIN_INVENTORY"] = 1

    response = square_app.test_client().get("/")
    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )

    assert response.status_code == 200
    assert b"Low stock \xc2\xb7 1 left" in response.data
    assert (item.stock_count, item.low_stock, item.available) == (1, True, True)
    assert sum(
        request.url.path == "/v2/inventory/counts/batch-retrieve"
        for request in square_app.square_fixture.requests
    ) == 1


def test_square_inventory_ignores_untracked_zero_count(square_app):
    square_app.square_fixture.inventory_counts["VAR_PLAIN"] = 0
    square_app.square_fixture.unflagged_inventory_ids.add("VAR_PLAIN")

    response = square_app.test_client().get("/")
    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )

    assert response.status_code == 200
    assert (item.stock_count, item.low_stock, item.available) == (
        None,
        False,
        True,
    )


def test_square_inventory_failure_keeps_the_last_good_counts(square_app):
    square_app.square_fixture.inventory_counts["VAR_DESSERT"] = 3
    provider = square_app.extensions["menu_provider"]
    first = provider.snapshot()
    assert first.items_by_id["VAR_DESSERT"].stock_count == 3

    square_app.square_fixture.inventory_error = True
    provider._cached_at = 0.0
    refreshed = provider.snapshot()

    assert refreshed.items_by_id["VAR_DESSERT"].stock_count == 3
    assert refreshed.items_by_id["VAR_DESSERT"].low_stock is True


def test_page_picker_and_cart_edits_reuse_square_reads(square_app):
    square_app.square_fixture.inventory_counts["VAR_SIDE"] = 10
    client = square_app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert b'"slotsByDate"' in page.data

    def request_count(path: str) -> int:
        return sum(
            request.url.path == path
            for request in square_app.square_fixture.requests
        )

    assert request_count("/v2/catalog/list") == 1
    assert request_count("/v2/inventory/counts/batch-retrieve") == 1
    assert request_count("/v2/orders/search") == 1

    # Opening the picker revalidates in the background, but the short server
    # cache collapses that immediate duplicate availability read.
    slots = client.get("/api/slots?date=2026-08-06")
    assert slots.status_code == 200
    assert request_count("/v2/catalog/list") == 1
    assert request_count("/v2/inventory/counts/batch-retrieve") == 1
    assert request_count("/v2/orders/search") == 1

    with client.session_transaction() as browser_session:
        token = browser_session["csrf_token"]
    added = client.post(
        "/api/cart",
        json={"item_id": "VAR_PLAIN", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201
    line_id = added.get_json()["lines"][0]["id"]

    updated = client.patch(
        f"/api/cart/{line_id}",
        json={"quantity": 2},
        headers={"X-CSRF-Token": token},
    )
    assert updated.status_code == 200
    assert updated.get_json()["lines"][0]["quantity"] == 2

    # The cart uses the menu and remaining capacity shown on the page. It does
    # not block each click on another catalog or order search.
    assert request_count("/v2/catalog/list") == 1
    assert request_count("/v2/inventory/counts/batch-retrieve") == 1
    assert request_count("/v2/orders/search") == 1


def test_production_warmup_keeps_inventory_off_initial_page_load(square_app):
    square_app.square_fixture.inventory_counts["VAR_SIDE"] = 4
    prepare_app_for_serving(square_app, version="0.18.30")

    def request_count(path: str) -> int:
        return sum(
            request.url.path == path
            for request in square_app.square_fixture.requests
        )

    assert request_count("/v2/catalog/list") == 1
    assert request_count("/v2/inventory/counts/batch-retrieve") == 1
    assert request_count("/v2/orders/search") == 1

    response = square_app.test_client().get("/")

    assert response.status_code == 200
    assert b"Low stock \xc2\xb7 4 left" in response.data
    assert request_count("/v2/catalog/list") == 1
    assert request_count("/v2/inventory/counts/batch-retrieve") == 1
    assert request_count("/v2/orders/search") == 1


def test_square_non_pizza_categories_appear_and_do_not_consume_pizza_capacity(square_app):
    client = square_app.test_client()
    response = client.get("/")
    html = response.get_data(as_text=True)

    expected = [
        ("Sides", "Garlic Knots"),
        ("Desserts", "Chocolate Cookie"),
        ("Salads", "Cucumber Salad"),
        ("Drinks", "Sparkling Water"),
    ]
    previous_position = html.index("Mari Pies")
    for category, item_name in expected:
        category_position = html.index(f"<h2>{category}</h2>")
        item_position = html.index(f"<strong>{item_name}</strong>")
        assert previous_position < category_position < item_position
        previous_position = category_position

    menu = square_app.extensions["menu_provider"].snapshot()
    for variation_id in ("VAR_SIDE", "VAR_DESSERT", "VAR_SALAD", "VAR_DRINK"):
        assert menu.items_by_id[variation_id].capacity_category is None

    token = csrf(client)
    headers = {"X-CSRF-Token": token}
    pizza = client.post(
        "/api/cart",
        json={"item_id": "VAR_PLAIN", "quantity": 3},
        headers=headers,
    )
    assert pizza.status_code == 201
    side = client.post(
        "/api/cart",
        json={"item_id": "VAR_SIDE", "quantity": 1},
        headers=headers,
    )
    assert side.status_code == 201
    assert side.get_json()["totals"]["pizza_count"] == 3
    assert side.get_json()["totals"]["item_count"] == 4


def test_square_menu_uses_large_borderless_image_above_item_name(square_app):
    response = square_app.test_client().get("/")
    html = response.get_data(as_text=True)
    css = square_app.test_client().get("/static/style.css").get_data(as_text=True)

    card_start = html.index('class="menu-card"')
    image_position = html.index('class="menu-photo"', card_start)
    name_position = html.index("<strong>Plain</strong>", card_start)
    assert image_position < name_position
    detail_image_position = html.index('class="item-detail-media"')
    detail_name_position = html.index('id="item-name"')
    assert detail_image_position < detail_name_position
    assert '<span class="menu-photo" data-image-url="https://example.com/plain.jpg"></span>' in html
    assert '<img class="menu-photo"' not in html
    assert ".menu-card-media" in css
    assert "aspect-ratio: 1" in css
    assert ".menu-card {" in css and "border: 2px solid var(--ink)" in css
    assert ".menu-photo" in css and "background-size: cover" in css
    assert "background-position: center 56%" in css
    detail_rules = css[css.index(".item-detail-media {"):css.index(".item-detail-media #item-art:not(.item-photo)")]
    assert "height: clamp(240px, 52dvh, 430px)" in detail_rules
    assert "aspect-ratio" not in detail_rules
    assert "background: transparent" in detail_rules
    assert "background-size: cover" in detail_rules
    assert "background-position: center 58%" in detail_rules


def test_square_additions_use_whole_list_as_the_single_canonical_option_set(square_app):
    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )

    assert [addition.name for addition in item.additions] == [
        "Pepperoni",
        "Oyster Mushrooms",
    ]
    assert "Half-list-only option" not in [
        addition.name for addition in item.additions
    ]

    mushrooms = item.additions[1]
    assert mushrooms.placements["whole"].id == "MOD_WHOLE_MUSHROOM"
    assert mushrooms.placements["whole"].price_cents == 600
    assert mushrooms.placements["first_half"].id == "MOD_FIRST_MUSHROOM"
    assert mushrooms.placements["first_half"].price_cents == 350
    assert mushrooms.placements["second_half"].id == "MOD_SECOND_MUSHROOM"
    assert mushrooms.placements["second_half"].price_cents == 375

    response = square_app.test_client().get("/")
    html = response.get_data(as_text=True)
    assert html.count('id="additions-fieldset"') == 1
    assert "<legend>Additions <span>Optional</span></legend>" in html
    assert "Whole Pie Additions" not in [
        group.name for group in item.modifier_groups
    ]
    assert "First Half Pie Additions" not in [
        group.name for group in item.modifier_groups
    ]
    assert "Second Half Pie Additions" not in [
        group.name for group in item.modifier_groups
    ]


def test_square_unlimited_modifier_sentinel_never_becomes_negative_limit(square_app):
    item = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
    )
    group = item.modifier_groups[0]

    assert group.public_dict()["max_selected"] is None
    javascript = square_app.test_client().get("/static/app.js").get_data(as_text=True)
    assert "group.max_selected > 0" in javascript
    assert "Choose up to -1" not in javascript


def test_square_unlimited_modifier_group_accepts_multiple_options(square_app):
    client = square_app.test_client()
    response = client.post(
        "/api/cart",
        json={
            "item_id": "VAR_PLAIN",
            "quantity": 1,
            "modifier_selections": {
                "PREFERENCES": ["MOD_DOUBLE_CUT", "MOD_NO_BASIL"]
            },
        },
        headers={"X-CSRF-Token": csrf(client)},
    )

    assert response.status_code == 201
    assert response.get_json()["lines"][0]["modifiers"] == [
        "Preferences: Double Cut",
        "Preferences: No Basil",
    ]


def test_square_checkout_redirects_to_hosted_payment_and_confirms_return(square_app):
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
    assert b"Secure Checkout on Square" in checkout.data
    assert b"before tip" not in checkout.data
    assert b"Choose on Square" not in checkout.data
    assert b"<span>Tip</span>" not in checkout.data
    assert b"Pay by card or an available digital wallet" not in checkout.data
    assert b'id="card-container"' not in checkout.data
    assert b"squarecdn.com/v1/square.js" not in checkout.data
    checkout_javascript = client.get("/static/checkout.js").get_data(as_text=True)
    assert "before tip" not in checkout_javascript
    assert "Choose on Square" not in checkout_javascript
    csp = checkout.headers["Content-Security-Policy"]
    assert "squarecdn.com" not in csp
    assert "connect-src 'self'" in csp
    assert "test-token-not-a-real-secret" not in checkout.get_data(as_text=True)
    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "3348",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
            "notes": "Allergy note",
        },
    )

    assert response.status_code == 303
    assert response.headers["Location"].startswith("/checkout/square?attempt=")
    with client.session_transaction() as browser_session:
        assert browser_session["cart"]
        pending = browser_session["pending_square_checkout"]
        assert pending["order_id"] == "SQUARE_ORDER_12345678"
        attempt_id = pending["attempt_id"]

    handoff = client.get(response.headers["Location"])
    assert handoff.status_code == 200
    assert b"Opening Square" in handoff.data
    assert b'id="square-checkout-link" href="https://sandbox.square.link/u/test-checkout"' in handoff.data
    assert b"/static/square-redirect.js?v=0.18.29" in handoff.data
    assert "form-action 'self'" in handoff.headers["Content-Security-Policy"]
    handoff_javascript = client.get("/static/square-redirect.js").get_data(as_text=True)
    assert "window.location.replace(link.href)" in handoff_javascript

    square_app.square_fixture.payment_status = "COMPLETED"
    complete = client.get(f"/checkout/complete?attempt={attempt_id}")
    assert complete.status_code == 200
    assert b"Thanks, Alex Customer." in complete.data
    assert b"View Square receipt" in complete.data
    assert b"$38.13" in complete.data
    with client.session_transaction() as browser_session:
        assert "cart" not in browser_session
        assert "pending_square_checkout" not in browser_session


def test_checkout_does_not_reread_capacity_after_customer_reviews_order(square_app):
    client = square_app.test_client()
    token = csrf(client)
    added = client.post(
        "/api/cart",
        json={"item_id": "VAR_PLAIN", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201
    assert client.get("/checkout").status_code == 200

    square_app.square_fixture.orders = [
        {
            "id": "ORDER_PLACED_WHILE_CUSTOMER_REVIEWED",
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

    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "2808",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
        },
    )

    assert response.status_code == 303
    assert response.headers["Location"].startswith("/checkout/square?attempt=")


@pytest.mark.parametrize("payment_status", ("FAILED", "CANCELED"))
def test_unsuccessful_hosted_payment_gets_clear_notice_and_preserves_cart(
    square_app, payment_status
):
    client = square_app.test_client()
    token = csrf(client)
    added = client.post(
        "/api/cart",
        json={"item_id": "VAR_PLAIN", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201

    submitted = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "2808",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
        },
    )
    assert submitted.status_code == 303
    with client.session_transaction() as browser_session:
        attempt_id = browser_session["pending_square_checkout"]["attempt_id"]
        original_cart = list(browser_session["cart"])

    square_app.square_fixture.order_state_override = "OPEN"
    square_app.square_fixture.payment_status = payment_status
    result = client.get(f"/checkout/complete?attempt={attempt_id}")

    assert result.status_code == 200
    assert b"Payment unsuccessful" in result.data
    assert b"Your order was not placed" in result.data
    assert b"no completed charge was recorded" in result.data
    assert b"Your cart is still saved" in result.data
    assert b"Thursday, August 6 at 4:00 PM" in result.data
    assert b"Return to checkout" in result.data
    assert b"pending authorization" in result.data
    with client.session_transaction() as browser_session:
        assert browser_session["cart"] == original_cart
        assert "pending_square_checkout" not in browser_session
        assert "service_at" in browser_session

    retry = client.get("/checkout")
    assert retry.status_code == 200
    assert b"Who is picking up?" in retry.data


def test_hosted_checkout_accepts_a_remembered_e164_us_phone(square_app):
    client = square_app.test_client()
    token = csrf(client)
    added = client.post(
        "/api/cart",
        json={"item_id": "VAR_PLAIN", "quantity": 1},
        headers={"X-CSRF-Token": token},
    )
    assert added.status_code == 201

    response = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "2808",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "+15185550100",
            "payment_method": "hosted",
        },
    )

    assert response.status_code == 303
    assert response.headers["Location"].startswith("/checkout/square?attempt=")
    with client.session_transaction() as browser_session:
        assert browser_session["pending_square_checkout"]["customer_phone"] == "+15185550100"

    handoff = client.get(response.headers["Location"])
    assert handoff.status_code == 200
    assert b"https://sandbox.square.link/u/test-checkout" in handoff.data


def start_gift_card_checkout(app, client) -> tuple[str, str]:
    token = csrf(client)
    addition_id = (
        app.extensions["menu_provider"]
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

    checkout = client.get("/checkout")
    assert b"Pay with a Gift Card" in checkout.data
    assert b"Pay with a Square Gift Card" not in checkout.data
    assert b"Apply one Pizzeria Mari Square gift card" not in checkout.data
    assert b'name="payment_method" value="hosted" checked' in checkout.data
    assert b"sandbox.web.squarecdn.com/v1/square.js" not in checkout.data
    started = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "3348",
            "payment_method": "gift_card",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
            "notes": "Allergy note",
        },
    )
    assert started.status_code == 303
    with client.session_transaction() as browser_session:
        pending = browser_session["pending_square_checkout"]
        attempt_id = pending["attempt_id"]
        assert pending["mode"] == "gift_card"
        assert pending["order_id"] == "GIFT_ORDER_12345678"
        assert browser_session["cart"]
    assert started.headers["Location"].endswith(
        f"/checkout/gift-card?attempt={attempt_id}"
    )
    return token, attempt_id


def test_gift_card_checkout_loads_square_fields_with_restricted_csp():
    app = gift_card_app(gift_amount=3348)
    client = app.test_client()
    _, attempt_id = start_gift_card_checkout(app, client)

    response = client.get(f"/checkout/gift-card?attempt={attempt_id}")

    assert response.status_code == 200
    assert b'id="gift-card-container"' in response.data
    assert b'id="card-container"' in response.data
    assert b"sandbox.web.squarecdn.com/v1/square.js" in response.data
    assert b"test-token-not-a-real-secret" not in response.data
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "https://sandbox.web.squarecdn.com" in csp
    assert "https://pci-connect.squareupsandbox.com" in csp
    assert "frame-src 'self' https://sandbox.web.squarecdn.com" in csp


def test_gift_card_can_pay_the_full_order_and_show_a_receipt():
    app = gift_card_app(gift_amount=3348)
    client = app.test_client()
    token, attempt_id = start_gift_card_checkout(app, client)

    paid = client.post(
        "/api/gift-card/payment",
        json={
            "attempt_id": attempt_id,
            "payment_method": "gift_card",
            "source_id": "gift-token",
        },
        headers={"X-CSRF-Token": token},
    )

    assert paid.status_code == 200
    assert paid.get_json()["status"] == "COMPLETED"
    assert "gift-token" not in paid.get_data(as_text=True)
    fixture = app.square_fixture
    assert fixture.payment_bodies[0]["accept_partial_authorization"] is True
    assert fixture.payment_bodies[0]["amount_money"]["amount"] == 3348
    assert fixture.pay_order_body["payment_ids"] == ["GIFT_PAYMENT"]
    uuid.UUID(fixture.payment_bodies[0]["idempotency_key"])
    uuid.UUID(fixture.pay_order_body["idempotency_key"])
    assert fixture.payment_bodies[0]["idempotency_key"] != fixture.pay_order_body[
        "idempotency_key"
    ]

    confirmation = client.get(paid.get_json()["redirect_url"])
    assert confirmation.status_code == 200
    assert b"Thanks, Alex Customer." in confirmation.data
    assert b"$33.48" in confirmation.data
    assert b"does not automatically email receipts" in confirmation.data
    assert b"View Square receipt" in confirmation.data
    with client.session_transaction() as browser_session:
        assert "cart" not in browser_session
        assert "pending_square_checkout" not in browser_session


def test_partial_gift_card_collects_the_remainder_on_one_square_order():
    app = gift_card_app(gift_amount=1000)
    client = app.test_client()
    token, attempt_id = start_gift_card_checkout(app, client)

    gift = client.post(
        "/api/gift-card/payment",
        json={
            "attempt_id": attempt_id,
            "payment_method": "gift_card",
            "source_id": "gift-token",
        },
        headers={"X-CSRF-Token": token},
    )
    assert gift.status_code == 200
    assert gift.get_json() == {
        "status": "PARTIAL",
        "applied_cents": 1000,
        "paid_cents": 1000,
        "remaining_cents": 2348,
    }

    resumed = client.get(f"/checkout/gift-card?attempt={attempt_id}")
    assert b'id="gift-card-step" hidden' in resumed.data
    assert b'id="remainder-card-step"' in resumed.data
    assert b"$23.48" in resumed.data

    card = client.post(
        "/api/gift-card/payment",
        json={
            "attempt_id": attempt_id,
            "payment_method": "card",
            "source_id": "card-token",
        },
        headers={"X-CSRF-Token": token},
    )

    assert card.status_code == 200
    assert card.get_json()["status"] == "COMPLETED"
    fixture = app.square_fixture
    assert fixture.payment_bodies[1]["amount_money"]["amount"] == 2348
    assert "accept_partial_authorization" not in fixture.payment_bodies[1]
    assert fixture.pay_order_body["payment_ids"] == [
        "GIFT_PAYMENT",
        "CARD_PAYMENT",
    ]
    assert fixture.created_order["id"] == "GIFT_ORDER_12345678"
    assert fixture.created_order["state"] == "COMPLETED"


def test_interrupted_gift_card_completion_is_not_retried_automatically():
    app = gift_card_app(gift_amount=3348)
    app.square_fixture.pay_order_failures = 1
    client = app.test_client()
    token, attempt_id = start_gift_card_checkout(app, client)

    interrupted = client.post(
        "/api/gift-card/payment",
        json={
            "attempt_id": attempt_id,
            "payment_method": "gift_card",
            "source_id": "gift-token",
        },
        headers={"X-CSRF-Token": token},
    )
    assert interrupted.status_code == 409
    assert app.square_fixture.created_order["state"] == "OPEN"
    assert app.square_fixture.payments["GIFT_PAYMENT"]["status"] == "APPROVED"

    follow_up = client.get(
        f"/checkout/gift-card?attempt={attempt_id}"
    )

    assert follow_up.status_code == 503
    assert b"did not finish the order" in follow_up.data
    assert app.square_fixture.created_order["state"] == "OPEN"


def test_unfinished_gift_card_order_does_not_consume_displayed_capacity():
    app = gift_card_app(gift_amount=1000)
    client = app.test_client()
    token, attempt_id = start_gift_card_checkout(app, client)
    client.post(
        "/api/gift-card/payment",
        json={
            "attempt_id": attempt_id,
            "payment_method": "gift_card",
            "source_id": "gift-token",
        },
        headers={"X-CSRF-Token": token},
    )
    app.square_fixture.created_order["created_at"] = "2026-08-04T15:40:00Z"

    slots = client.get("/api/slots?date=2026-08-06").get_json()

    assert slots["slots"][0]["remaining"] == 3
    assert app.square_fixture.canceled_payment_ids == []
    assert app.square_fixture.created_order["state"] == "OPEN"


def test_abandoned_hosted_checkout_does_not_block_a_fresh_checkout_page():
    fixture = SquareFixture()
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "PUBLIC_BASE_URL": "https://orders.example.test",
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
    started = client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "3348",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "518-555-0100",
        },
    )
    assert started.status_code == 303
    with client.session_transaction() as browser_session:
        attempt_id = browser_session["pending_square_checkout"]["attempt_id"]

    pending = client.get(f"/checkout/complete?attempt={attempt_id}")
    assert pending.status_code == 202
    assert b"Your payment is not confirmed yet." in pending.data
    assert b"Return to Square" in pending.data
    assert b"Cancel Square checkout" not in pending.data

    fresh = client.get("/checkout")
    assert fresh.status_code == 200
    assert b"Who is picking up?" in fresh.data
    assert fixture.canceled is False


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
            "PUBLIC_BASE_URL": "https://orders.example.test",
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
        "remaining": 0,
        "status": "Full",
    }


def test_unpaid_hosted_checkout_drafts_do_not_consume_capacity():
    def draft(order_id: str, *, reference: str, created_at: str, quantity: int) -> dict:
        return {
            "id": order_id,
            "version": 1,
            "location_id": "LOCATION",
            "state": "DRAFT",
            "reference_id": reference,
            "created_at": created_at,
            "fulfillments": [
                {
                    "type": "PICKUP",
                    "pickup_details": {"pickup_at": "2026-08-06T20:00:00Z"},
                }
            ],
            "line_items": [
                {"catalog_object_id": "VAR_PLAIN", "quantity": str(quantity)}
            ],
        }

    fixture = SquareFixture(
        orders=[
            draft(
                "RECENT_CHECKOUT",
                reference="PMOC-recent",
                created_at="2026-08-04T15:55:00Z",
                quantity=1,
            ),
            draft(
                "STALE_CHECKOUT",
                reference="PMOC-stale",
                created_at="2026-08-04T15:40:00Z",
                quantity=2,
            ),
            draft(
                "UNRELATED_SQUARE_DRAFT",
                reference="square-online-cart",
                created_at="2026-08-04T15:55:00Z",
                quantity=3,
            ),
        ]
    )
    app = create_app(
        {
            "TESTING": True,
            "DEMO_MODE": False,
            "SECRET_KEY": "test-secret-not-for-production",
            "PUBLIC_BASE_URL": "https://orders.example.test",
            "SQUARE_LOCATION_ID": "LOCATION",
            "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
            "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(fixture),
            "TEST_NOW": datetime(
                2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
            ),
        }
    )

    payload = app.test_client().get("/api/slots?date=2026-08-06").get_json()

    assert payload["slots"][0]["remaining"] == 3
    assert payload["slots"][0]["status"] == "3 pizzas available"
    assert fixture.canceled is False


def test_square_network_errors_do_not_expose_access_token(square_app):
    error = SquareAPIError("Square could not be reached.")
    assert "test-token" not in str(error)


def test_signed_checkout_session_survives_an_app_restart():
    fixture = SquareFixture()
    config = {
        "TESTING": True,
        "DEMO_MODE": False,
        "SECRET_KEY": "stable-secret-shared-across-restarts",
        "PUBLIC_BASE_URL": "https://orders.example.test",
        "SQUARE_LOCATION_ID": "LOCATION",
        "SQUARE_ACCESS_TOKEN": "test-token-not-a-real-secret",
        "SQUARE_HTTP_TRANSPORT": httpx.MockTransport(fixture),
        "TEST_NOW": datetime(
            2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York")
        ),
    }
    first_app = create_app(config)
    first_client = first_app.test_client()
    token = csrf(first_client)
    addition_id = (
        first_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
        .additions[0]
        .id
    )
    first_client.post(
        "/api/cart",
        json={
            "item_id": "VAR_PLAIN",
            "quantity": 1,
            "additions": [{"id": addition_id, "placement": "whole"}],
            "modifier_selections": {"PREFERENCES": []},
        },
        headers={"X-CSRF-Token": token},
    )
    started = first_client.post(
        "/checkout",
        data={
            "csrf_token": token,
            "verification_total_cents": "3348",
            "first_name": "Alex",
            "last_name": "Customer",
            "email": "alex@example.com",
            "phone": "5185550100",
        },
    )
    assert started.status_code == 303
    with first_client.session_transaction() as browser_session:
        attempt_id = browser_session["pending_square_checkout"]["attempt_id"]
    session_cookie = first_client.get_cookie(first_app.config["SESSION_COOKIE_NAME"])
    assert session_cookie is not None

    second_app = create_app({**config, "ORDERING_ENABLED": False})
    second_client = second_app.test_client()
    second_client.set_cookie(
        second_app.config["SESSION_COOKIE_NAME"], session_cookie.value
    )
    fixture.payment_status = "COMPLETED"
    completed = second_client.get(f"/checkout/complete?attempt={attempt_id}")

    assert completed.status_code == 200
    assert b"Thanks, Alex Customer." in completed.data
    with second_client.session_transaction() as browser_session:
        assert "pending_square_checkout" not in browser_session
        assert "cart" not in browser_session


def test_maximum_realistic_cart_and_pending_checkout_fit_in_the_session_cookie(square_app):
    client = square_app.test_client()
    token = csrf(client)
    addition_id = (
        square_app.extensions["menu_provider"]
        .snapshot()
        .items_by_id["VAR_PLAIN"]
        .additions[0]
        .id
    )
    pizza = client.post(
        "/api/cart",
        json={
            "item_id": "VAR_PLAIN",
            "quantity": 3,
            "additions": [{"id": addition_id, "placement": "second_half"}],
            "modifier_selections": {
                "PREFERENCES": ["MOD_DOUBLE_CUT", "MOD_NO_BASIL"]
            },
        },
        headers={"X-CSRF-Token": token},
    )
    assert pizza.status_code == 201
    for _ in range(5):
        side = client.post(
            "/api/cart",
            json={"item_id": "VAR_SIDE", "quantity": 1},
            headers={"X-CSRF-Token": token},
        )
        assert side.status_code == 201

    with client.session_transaction() as browser_session:
        browser_session["pending_square_checkout"] = {
            "attempt_id": "99999999-9999-4999-8999-999999999999",
            "mode": "hosted",
            "service_at": "2026-08-06T16:00:00-04:00",
            "customer_name": "A Customer With A Realistically Long Name",
            "customer_email": "customer-with-long-address@example.com",
            "customer_phone": "+15185550100",
            "created_at": "2026-08-04T12:00:00-04:00",
            "order_id": "SQUARE_ORDER_12345678901234567890",
            "payment_link_id": "PAYMENT_LINK_12345678901234567890",
            "checkout_url": "https://sandbox.square.link/u/test-checkout",
        }
    session_cookie = client.get_cookie(square_app.config["SESSION_COOKIE_NAME"])

    assert session_cookie is not None
    assert len(session_cookie.value) < 3800
