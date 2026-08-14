from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.square import SquareCommerce


class PaymentClient:
    def __init__(self) -> None:
        self.payment_requests = 0

    def search_orders(self, *, location_id: str, created_after: datetime) -> list[dict]:
        return [
            {
                "id": "FAILED_CHECKOUT",
                "state": "OPEN",
                "reference_id": "PMOC-failed",
                "tenders": [{"payment_id": "FAILED_PAYMENT"}],
                "fulfillments": [
                    {
                        "type": "PICKUP",
                        "pickup_details": {
                            "pickup_at": "2026-08-13T17:15:00-04:00"
                        },
                    }
                ],
                "line_items": [
                    {"catalog_object_id": "PIZZA", "quantity": "1"}
                ],
            }
        ]

    def retrieve_payment(self, payment_id: str) -> dict:
        self.payment_requests += 1
        return {"id": payment_id, "status": "FAILED"}


def test_terminal_payment_status_is_reused_across_availability_refreshes():
    client = PaymentClient()
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )
    now = datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York"))

    first = commerce.pizza_counts_by_slot(variation_ids={"PIZZA"}, now=now)
    second = commerce.pizza_counts_by_slot(variation_ids={"PIZZA"}, now=now)

    assert first == second == {}
    assert client.payment_requests == 1


class BatchedPaymentClient:
    def __init__(self) -> None:
        self.list_requests = 0
        self.payment_requests = 0

    def search_orders(self, *, location_id: str, created_after: datetime) -> list[dict]:
        return [
            {
                "id": f"FAILED_CHECKOUT_{index}",
                "state": "OPEN",
                "reference_id": f"PMOC-failed-{index}",
                "tenders": [{"payment_id": f"FAILED_PAYMENT_{index}"}],
            }
            for index in range(25)
        ]

    def list_payments(self, *, location_id: str, created_after: datetime) -> list[dict]:
        self.list_requests += 1
        return [
            {"id": f"FAILED_PAYMENT_{index}", "status": "FAILED"}
            for index in range(25)
        ]

    def retrieve_payment(self, payment_id: str) -> dict:
        self.payment_requests += 1
        raise AssertionError("availability should use the batched payment list")


def test_availability_batches_payment_statuses_instead_of_retrieving_each_one():
    client = BatchedPaymentClient()
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )
    now = datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York"))

    assert commerce.pizza_counts_by_slot(variation_ids={"PIZZA"}, now=now) == {}
    assert client.list_requests == 1
    assert client.payment_requests == 0


class OrderedFallbackPaymentClient:
    def __init__(self) -> None:
        self.retrieved_payment_ids: list[str] = []

    def search_orders(self, *, location_id: str, created_after: datetime) -> list[dict]:
        statuses = ("FAILED", "CANCELED", "COMPLETED")
        return [
            {
                "id": f"{status}_CHECKOUT",
                "state": "OPEN",
                "reference_id": f"PMOC-{status.casefold()}",
                "tenders": [{"payment_id": f"{status}_PAYMENT"}],
            }
            for status in statuses
        ]

    def retrieve_payment(self, payment_id: str) -> dict:
        self.retrieved_payment_ids.append(payment_id)
        return {
            "id": payment_id,
            "status": payment_id.removesuffix("_PAYMENT"),
        }


def test_payment_fallback_preserves_square_order_sequence():
    client = OrderedFallbackPaymentClient()
    commerce = SquareCommerce(
        client=client,
        location_id="LOCATION",
        timezone_name="America/New_York",
        availability_cache_seconds=0,
    )
    now = datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("America/New_York"))

    commerce.pizza_counts_by_slot(variation_ids={"PIZZA"}, now=now)

    assert client.retrieved_payment_ids == [
        "FAILED_PAYMENT",
        "CANCELED_PAYMENT",
        "COMPLETED_PAYMENT",
    ]


def test_preloaded_pickup_dates_do_not_refresh_immediately():
    javascript = open("app/static/app.js", encoding="utf-8").read()

    assert "slotCacheLifetime = 30000" in javascript
    assert "Date.now() - (slotCacheTimes.get(date) || 0)" in javascript
    assert "return;" in javascript
