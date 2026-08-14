from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.square import SquareCommerce


class CapacityClient:
    def __init__(self, orders: list[dict], payments: dict[str, dict]) -> None:
        self.orders = orders
        self.payments = payments
        self.retrieved_payment_ids: list[str] = []

    def search_orders(self, *, location_id: str, created_after: datetime) -> list[dict]:
        assert location_id == "LOCATION"
        return self.orders

    def retrieve_payment(self, payment_id: str) -> dict:
        self.retrieved_payment_ids.append(payment_id)
        return self.payments[payment_id]


def order(
    order_id: str,
    *,
    reference_id: str,
    state: str = "OPEN",
    payment_id: str | None = None,
    quantity: int = 1,
    fulfillment_state: str = "PROPOSED",
) -> dict:
    result = {
        "id": order_id,
        "state": state,
        "reference_id": reference_id,
        "fulfillments": [
            {
                "type": "PICKUP",
                "state": fulfillment_state,
                "pickup_details": {"pickup_at": "2026-08-13T17:15:00-04:00"},
            }
        ],
        "line_items": [
            {"catalog_object_id": "PIZZA_VARIATION", "quantity": str(quantity)}
        ],
    }
    if payment_id:
        result["tenders"] = [{"payment_id": payment_id}]
    return result


def test_failed_hosted_checkout_orders_do_not_reserve_capacity():
    client = CapacityClient(
        orders=[
            order(
                "FAILED_CHECKOUT",
                reference_id="PMOC-failed",
                payment_id="FAILED_PAYMENT",
            ),
            order(
                "CANCELED_CHECKOUT",
                reference_id="PMOC-canceled",
                payment_id="CANCELED_PAYMENT",
            ),
            order("UNSTARTED_CHECKOUT", reference_id="PMOC-unstarted"),
            order(
                "PAID_CHECKOUT",
                reference_id="PMOC-paid",
                payment_id="COMPLETED_PAYMENT",
            ),
            order("OPEN_POS_ORDER", reference_id="POS-order"),
            order("UNPAID_GIFT_ORDER", reference_id="PMGC-unpaid"),
        ],
        payments={
            "FAILED_PAYMENT": {"id": "FAILED_PAYMENT", "status": "FAILED"},
            "CANCELED_PAYMENT": {
                "id": "CANCELED_PAYMENT",
                "status": "CANCELED",
            },
            "COMPLETED_PAYMENT": {
                "id": "COMPLETED_PAYMENT",
                "status": "COMPLETED",
            },
        },
    )
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )

    counts = commerce.pizza_counts_by_slot(
        variation_ids={"PIZZA_VARIATION"},
        now=datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    assert counts == {"2026-08-13T17:15:00-04:00": 2}
    assert client.retrieved_payment_ids == [
        "FAILED_PAYMENT",
        "CANCELED_PAYMENT",
        "COMPLETED_PAYMENT",
    ]


def test_paid_open_pmoc_order_reserves_capacity():
    client = CapacityClient(
        orders=[
            order(
                "PAID_CHECKOUT",
                reference_id="PMOC-paid",
                payment_id="COMPLETED_PAYMENT",
                quantity=3,
            )
        ],
        payments={
            "COMPLETED_PAYMENT": {
                "id": "COMPLETED_PAYMENT",
                "status": "COMPLETED",
            }
        },
    )
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )

    counts = commerce.pizza_counts_by_slot(
        variation_ids={"PIZZA_VARIATION"},
        now=datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    assert counts == {"2026-08-13T17:15:00-04:00": 3}


def test_square_completed_order_releases_pickup_capacity():
    client = CapacityClient(
        orders=[
            order(
                "COMPLETED_CHECKOUT",
                reference_id="PMOC-completed",
                state="COMPLETED",
                payment_id="COMPLETED_PAYMENT",
                quantity=3,
            )
        ],
        payments={
            "COMPLETED_PAYMENT": {
                "id": "COMPLETED_PAYMENT",
                "status": "COMPLETED",
            }
        },
    )
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )

    counts = commerce.pizza_counts_by_slot(
        variation_ids={"PIZZA_VARIATION"},
        now=datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    assert counts == {}
    assert client.retrieved_payment_ids == []


def test_paid_gift_card_order_reserves_until_pickup_is_completed():
    client = CapacityClient(
        orders=[
            order(
                "PAID_GIFT_ORDER",
                reference_id="PMGC-paid",
                state="COMPLETED",
                quantity=2,
            ),
            order(
                "FULFILLED_GIFT_ORDER",
                reference_id="PMGC-fulfilled",
                state="COMPLETED",
                quantity=3,
                fulfillment_state="COMPLETED",
            ),
        ],
        payments={},
    )
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )

    counts = commerce.pizza_counts_by_slot(
        variation_ids={"PIZZA_VARIATION"},
        now=datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    assert counts == {"2026-08-13T17:15:00-04:00": 2}
