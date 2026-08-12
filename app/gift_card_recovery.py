from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from flask import (
    after_this_request,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .operations import log_event
from .square import SquareAPIError

if TYPE_CHECKING:
    from flask import Flask


def _pending_attempt() -> dict | None:
    pending = session.get("pending_square_checkout")
    if not isinstance(pending, dict) or pending.get("mode") != "gift_card":
        return None
    if not pending.get("attempt_id") or not pending.get("order_id"):
        return None
    return pending


def _matches_attempt(pending: dict, attempt_id: str) -> bool:
    return bool(
        attempt_id
        and secrets.compare_digest(str(pending.get("attempt_id", "")), attempt_id)
    )


def install_gift_card_recovery(app: Flask) -> None:
    """Keep a paid or partially paid gift-card attempt from being replaced."""

    @app.get("/api/gift-card/status", endpoint="gift_card_recovery_status")
    def gift_card_recovery_status():
        @after_this_request
        def prevent_status_caching(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        pending = _pending_attempt()
        attempt_id = request.args.get("attempt", "")
        if pending is None or not _matches_attempt(pending, attempt_id):
            return jsonify({"error": "This gift-card checkout expired."}), 409

        commerce = app.extensions.get("square_commerce")
        if commerce is None:
            return jsonify({"error": "Square gift-card checkout is unavailable."}), 404
        try:
            state = commerce.gift_card_checkout_state(str(pending["order_id"]))
        except SquareAPIError as exc:
            log_event(
                app.logger,
                "gift_card_status_failed",
                level=logging.WARNING,
                checkout_attempt_id=str(pending["attempt_id"]),
                square_order_id=str(pending["order_id"]),
                error_type=type(exc).__name__,
                ambiguous=exc.ambiguous,
            )
            return jsonify(
                {
                    "status": "UNKNOWN",
                    "error": (
                        "We could not confirm the payment yet. Do not submit "
                        "another order; check this payment again in a moment."
                    ),
                }
            ), 503

        if state["status"] == "COMPLETED":
            return jsonify(
                {
                    "status": "COMPLETED",
                    "remaining_cents": 0,
                    "redirect_url": url_for(
                        "storefront.checkout_complete", attempt=attempt_id
                    ),
                }
            )
        if state["status"] == "CANCELED":
            return jsonify({"status": "CANCELED", "remaining_cents": 0})

        paid_cents = int(state.get("paid_cents", 0))
        remaining_cents = int(state.get("remaining_cents", 0))
        payment_ids = list(state.get("payment_ids", []))
        status = (
            "PROCESSING"
            if payment_ids and remaining_cents == 0
            else "PARTIAL"
            if payment_ids or paid_cents > 0
            else "PENDING"
        )
        return jsonify(
            {
                "status": status,
                "paid_cents": paid_cents,
                "remaining_cents": remaining_cents,
                "gift_card_applied": bool(state.get("gift_card_applied")),
            }
        )

    @app.before_request
    def preserve_active_gift_card_attempt():
        if request.endpoint != "storefront.checkout":
            return None
        pending = _pending_attempt()
        if pending is None:
            return None

        commerce = app.extensions.get("square_commerce")
        if commerce is None:
            return None
        try:
            state = commerce.gift_card_checkout_state(str(pending["order_id"]))
        except SquareAPIError as exc:
            log_event(
                app.logger,
                "gift_card_checkout_recovery_failed",
                level=logging.WARNING,
                checkout_attempt_id=str(pending["attempt_id"]),
                square_order_id=str(pending["order_id"]),
                error_type=type(exc).__name__,
                ambiguous=exc.ambiguous,
            )
            return render_template(
                "gift_card_recovery.html",
                attempt_id=str(pending["attempt_id"]),
            ), 503

        attempt_id = str(pending["attempt_id"])
        if state["status"] == "CANCELED":
            session.pop("pending_square_checkout", None)
            return None
        if state["status"] == "COMPLETED":
            log_event(
                app.logger,
                "gift_card_checkout_recovered",
                checkout_attempt_id=attempt_id,
                square_order_id=str(pending["order_id"]),
                recovery_state="COMPLETED",
            )
            return redirect(
                url_for("storefront.checkout_complete", attempt=attempt_id),
                code=303 if request.method == "POST" else 302,
            )

        if state.get("payment_ids") or int(state.get("paid_cents", 0)) > 0:
            log_event(
                app.logger,
                "gift_card_checkout_recovered",
                checkout_attempt_id=attempt_id,
                square_order_id=str(pending["order_id"]),
                recovery_state="PARTIAL",
            )
            return redirect(
                url_for("storefront.gift_card_checkout", attempt=attempt_id),
                code=303 if request.method == "POST" else 302,
            )
        return None
