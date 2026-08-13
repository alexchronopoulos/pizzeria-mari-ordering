from __future__ import annotations

from datetime import datetime
from html import escape
import re
from typing import TYPE_CHECKING

from flask import (
    g,
    jsonify,
    render_template,
    request,
    session,
    url_for,
)

if TYPE_CHECKING:
    from flask import Flask, Response
    from .menu import MenuSnapshot


COOKIE_SERVICE_DAYS = frozenset({4, 5, 6})  # Friday, Saturday, Sunday
COOKIE_UNAVAILABLE_MESSAGE = "Cookies are only available Friday through Sunday."


def _selected_weekday() -> int | None:
    raw_service_at = session.get("service_at")
    if not isinstance(raw_service_at, str):
        return None
    try:
        return datetime.fromisoformat(raw_service_at).weekday()
    except ValueError:
        return None


def _requested_weekday() -> int | None:
    data = request.get_json(silent=True) or {}
    try:
        return datetime.fromisoformat(str(data["service_at"])).weekday()
    except (KeyError, TypeError, ValueError):
        return None


def _cookie_item_ids(menu: MenuSnapshot) -> set[str]:
    return {item.id for item in menu.items if "cookie" in item.name.casefold()}


def _cart_contains_cookie(cookie_item_ids: set[str]) -> bool:
    cart = session.get("cart", [])
    if not isinstance(cart, list):
        return False
    return any(
        isinstance(line, dict) and line.get("item_id") in cookie_item_ids
        for line in cart
    )


def _is_cookie_line(cookie_item_ids: set[str]) -> bool:
    line_id = request.view_args.get("line_id") if request.view_args else None
    if not line_id:
        return False
    cart = session.get("cart", [])
    if not isinstance(cart, list):
        return False
    return any(
        isinstance(line, dict)
        and line.get("id") == line_id
        and line.get("item_id") in cookie_item_ids
        for line in cart
    )


def install_cookie_availability(app: Flask) -> None:
    """Show and sell cookies only for Friday-through-Sunday pickup."""

    @app.before_request
    def enforce_cookie_service_days():
        endpoint = request.endpoint
        guarded_endpoints = {
            "storefront.api_add_to_cart",
            "storefront.api_select_slot",
            "storefront.api_update_cart_quantity",
            "storefront.checkout",
        }
        if endpoint not in guarded_endpoints:
            return None

        menu = app.extensions["menu_provider"].snapshot()
        cookie_item_ids = _cookie_item_ids(menu)
        if not cookie_item_ids:
            return None

        if endpoint == "storefront.api_select_slot":
            if (
                _requested_weekday() not in COOKIE_SERVICE_DAYS
                and _cart_contains_cookie(cookie_item_ids)
            ):
                return jsonify({"error": COOKIE_UNAVAILABLE_MESSAGE}), 409
            return None

        if _selected_weekday() in COOKIE_SERVICE_DAYS:
            return None

        if endpoint == "storefront.api_add_to_cart":
            data = request.get_json(silent=True) or {}
            if data.get("item_id") in cookie_item_ids:
                return jsonify({"error": COOKIE_UNAVAILABLE_MESSAGE}), 409
            return None

        if endpoint == "storefront.api_update_cart_quantity":
            if _is_cookie_line(cookie_item_ids):
                return jsonify({"error": COOKIE_UNAVAILABLE_MESSAGE}), 409
            return None

        if endpoint == "storefront.checkout" and _cart_contains_cookie(
            cookie_item_ids
        ):
            return render_template("cookie_unavailable.html"), 409
        return None

    @app.after_request
    def load_cookie_availability_ui(response: Response):
        if (
            request.endpoint != "storefront.index"
            or response.status_code != 200
            or response.mimetype != "text/html"
        ):
            return response

        page = response.get_data(as_text=True)
        if "</body>" not in page:
            return response
        if _selected_weekday() not in COOKIE_SERVICE_DAYS:
            menu = app.extensions["menu_provider"].snapshot()
            for item_id in _cookie_item_ids(menu):
                encoded_item_id = re.escape(escape(item_id, quote=True))
                page = re.sub(
                    rf'(<button\b(?=[^>]*\bclass="menu-card")'
                    rf'(?=[^>]*\bdata-item-id="{encoded_item_id}")[^>]*)(>)',
                    r"\1 hidden\2",
                    page,
                    count=1,
                )
        script_url = url_for(
            "static",
            filename="cookie-availability.js",
            v=app.config["APP_VERSION"],
        )
        script = (
            f'<script nonce="{escape(g.get("csp_nonce", ""), quote=True)}" '
            f'src="{escape(script_url, quote=True)}" defer></script>'
        )
        response.set_data(page.replace("</body>", f"{script}\n  </body>", 1))
        return response
