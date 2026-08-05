import pytest

from app.capacity import SQLiteCapacityStore, SlotUnavailableError


def confirm(store, service_at: str, count: int):
    return store.confirm_demo_order(
        service_at=service_at,
        pizza_count=count,
        capacity=3,
        customer={"name": "Alex", "email": "alex@example.com", "phone": ""},
        notes="",
        tip_cents=0,
        cart=[{"item_id": "plain", "quantity": count}],
    )


def test_slot_stays_open_until_exact_capacity_is_reached(tmp_path):
    store = SQLiteCapacityStore(str(tmp_path / "orders.sqlite3"))
    store.initialize()
    service_at = "2026-08-06T16:15:00-04:00"

    confirm(store, service_at, 1)
    assert store.remaining(service_at, 3) == 2

    confirm(store, service_at, 2)
    assert store.remaining(service_at, 3) == 0


def test_slot_cannot_oversell(tmp_path):
    store = SQLiteCapacityStore(str(tmp_path / "orders.sqlite3"))
    store.initialize()
    service_at = "2026-08-06T16:15:00-04:00"

    confirm(store, service_at, 2)
    with pytest.raises(SlotUnavailableError):
        confirm(store, service_at, 2)

    assert store.remaining(service_at, 3) == 1
