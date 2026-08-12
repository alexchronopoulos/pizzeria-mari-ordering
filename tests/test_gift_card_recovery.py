from __future__ import annotations

from pathlib import Path

from app import create_app
from app.square import SquareAPIError


ATTEMPT_ID = "c3097bbd-53c5-4841-926c-beec8be813f2"


class RecoveryCommerce:
    def __init__(self, state: dict | Exception):
        self.state = state
        self.order_ids: list[str] = []

    def gift_card_checkout_state(self, order_id: str) -> dict:
        self.order_ids.append(order_id)
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


def recovery_app(state: dict | Exception):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
        }
    )
    app.extensions["square_commerce"] = RecoveryCommerce(state)
    return app


def set_pending(client) -> None:
    with client.session_transaction() as browser_session:
        browser_session["pending_square_checkout"] = {
            "attempt_id": ATTEMPT_ID,
            "mode": "gift_card",
            "order_id": "ORIGINAL_GIFT_ORDER",
            "service_at": "2026-08-13T17:30:00-04:00",
            "customer_name": "Alex Customer",
            "customer_email": "alex@example.com",
            "customer_phone": "+15185550100",
        }


def state(*, status: str, paid: int, remaining: int, payment_ids: list[str]):
    return {
        "status": status,
        "paid_cents": paid,
        "remaining_cents": remaining,
        "payment_ids": payment_ids,
        "gift_card_applied": bool(payment_ids),
    }


def test_completed_attempt_reopens_confirmation_instead_of_checkout():
    app = recovery_app(
        state(status="COMPLETED", paid=3348, remaining=0, payment_ids=["G", "C"])
    )
    client = app.test_client()
    set_pending(client)

    response = client.get("/checkout")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/checkout/complete?attempt={ATTEMPT_ID}"
    )
    assert app.extensions["square_commerce"].order_ids == ["ORIGINAL_GIFT_ORDER"]


def test_partial_attempt_cannot_be_replaced_by_checkout_post():
    app = recovery_app(
        state(status="PENDING", paid=1000, remaining=2348, payment_ids=["G"])
    )
    client = app.test_client()
    set_pending(client)

    response = client.post("/checkout", data={})

    assert response.status_code == 303
    assert response.headers["Location"].endswith(
        f"/checkout/gift-card?attempt={ATTEMPT_ID}"
    )
    with client.session_transaction() as browser_session:
        assert browser_session["pending_square_checkout"]["order_id"] == (
            "ORIGINAL_GIFT_ORDER"
        )


def test_unknown_attempt_state_blocks_a_duplicate_order():
    app = recovery_app(
        SquareAPIError("Square could not be reached.", ambiguous=True)
    )
    client = app.test_client()
    set_pending(client)

    response = client.post("/checkout", data={})

    assert response.status_code == 503
    assert b"we did not start another order" in response.data
    assert b"Check existing payment" in response.data


def test_status_endpoint_recovers_completed_and_partial_attempts():
    completed_app = recovery_app(
        state(status="COMPLETED", paid=3348, remaining=0, payment_ids=["G", "C"])
    )
    completed_client = completed_app.test_client()
    set_pending(completed_client)

    completed = completed_client.get(
        f"/api/gift-card/status?attempt={ATTEMPT_ID}"
    )

    assert completed.status_code == 200
    assert completed.get_json()["status"] == "COMPLETED"
    assert completed.get_json()["redirect_url"].endswith(
        f"/checkout/complete?attempt={ATTEMPT_ID}"
    )
    assert completed.headers["Cache-Control"] == "no-store"

    partial_app = recovery_app(
        state(status="PENDING", paid=1000, remaining=2348, payment_ids=["G"])
    )
    partial_client = partial_app.test_client()
    set_pending(partial_client)

    partial = partial_client.get(f"/api/gift-card/status?attempt={ATTEMPT_ID}")

    assert partial.status_code == 200
    assert partial.get_json() == {
        "status": "PARTIAL",
        "paid_cents": 1000,
        "remaining_cents": 2348,
        "gift_card_applied": True,
    }


def test_gift_card_browser_recovers_status_and_warns_against_retrying():
    javascript = Path("app/static/gift-card.js").read_text()
    template = Path("app/templates/gift_card_payment.html").read_text()

    assert "startStatusWatch(payments, 'card')" in javascript
    assert "Do not submit another order" in javascript
    assert "data.statusUrl" in javascript
    assert 'id="gift-card-exit"' in template
    assert "gift_card_recovery_status" in template
    assert "gift_card_asset_version" in template
