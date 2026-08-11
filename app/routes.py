from __future__ import annotations

import logging
import secrets
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .capacity import SlotUnavailableError
from .cart import CartLimitError, cart_totals, validate_cart
from .menu import MenuItem, MenuSnapshot
from .operations import log_event
from .scheduling import (
    pickup_service_dates,
    pickup_slot_capacity,
    pickup_slots_for_date,
)
from .square import SquareAPIError, SquareConfigurationError, new_attempt_id


storefront = Blueprint("storefront", __name__)

PLACEMENT_LABELS = {
    "whole": "Whole pie",
    "first_half": "First half",
    "second_half": "Second half",
}


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _percentage_cents(cents: int, percent: int) -> int:
    return int(
        (Decimal(cents) * Decimal(percent) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _normalize_phone(value: str) -> str:
    if not value.strip():
        raise ValueError("Enter a valid US phone number (10 digits or +1).")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    raise ValueError("Enter a valid US phone number (10 digits or +1).")


def _checkout_return_url(attempt_id: str) -> str:
    path = url_for("storefront.checkout_complete", attempt=attempt_id)
    return f"{current_app.config['PUBLIC_BASE_URL']}{path}"


def _now() -> datetime:
    configured = current_app.config.get("TEST_NOW")
    if configured:
        return configured
    return datetime.now(ZoneInfo(current_app.config["TIMEZONE"]))


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


def _require_csrf() -> None:
    provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if not provided or not secrets.compare_digest(provided, _csrf_token()):
        abort(400, "Invalid form token")


def _cart() -> list[dict]:
    return list(session.get("cart", []))


def _save_cart(lines: list[dict]) -> None:
    session["cart"] = lines
    session.modified = True


def _menu(*, allow_stale: bool = False) -> MenuSnapshot:
    provider = current_app.extensions["menu_provider"]
    if allow_stale:
        cached_snapshot = getattr(provider, "cached_snapshot", None)
        cached = cached_snapshot() if cached_snapshot else None
        if cached is not None:
            return cached
    return provider.snapshot()


def _date_choices() -> list[dict]:
    days = pickup_service_dates(
        current_app.config["SERVICE_HOURS"],
        current_app.config["PICKUP_SCHEDULE"],
        current_app.config["PIZZA_SLOT_CAPACITY"],
        current_app.config["TIMEZONE"],
        current_app.config["ADVANCE_DAYS"],
        _now(),
    )
    return [
        {
            "iso": day.isoformat(),
            "weekday": day.strftime("%a"),
            "month_day": day.strftime("%b %-d"),
            "long": day.strftime("%A, %B %-d"),
        }
        for day in days
    ]


def _pizza_counts(menu: MenuSnapshot) -> dict[str, int]:
    if not current_app.config["USE_SQUARE_DATA"]:
        return current_app.extensions["capacity_store"].counts_by_slot()
    return current_app.extensions["square_commerce"].pizza_counts_by_slot(
        variation_ids=menu.pizza_catalog_object_ids,
        now=_now(),
    )


def _remaining(service_at: datetime, menu: MenuSnapshot) -> int:
    capacity = _slot_capacity(service_at)
    if capacity is None:
        return 0
    confirmed = _pizza_counts(menu).get(service_at.isoformat(), 0)
    return max(0, capacity - confirmed)


def _slot_capacity(service_at: datetime) -> int | None:
    return pickup_slot_capacity(
        service_at,
        current_app.config["SERVICE_HOURS"],
        current_app.config["PICKUP_SCHEDULE"],
        current_app.config["PIZZA_SLOT_CAPACITY"],
        current_app.config["TIMEZONE"],
        current_app.config["ADVANCE_DAYS"],
        current_app.config["SLOT_INTERVAL_MINUTES"],
        _now(),
    )


def _slots(
    day: date,
    menu: MenuSnapshot | None = None,
    counts: dict[str, int] | None = None,
) -> list[dict]:
    menu = menu or _menu()
    items_by_id = menu.items_by_id
    required_pizzas = max(1, cart_totals(_cart(), items_by_id)["pizza_count"])
    counts = _pizza_counts(menu) if counts is None else counts
    results = []
    for slot in pickup_slots_for_date(
        day,
        current_app.config["SERVICE_HOURS"],
        current_app.config["PICKUP_SCHEDULE"],
        current_app.config["PIZZA_SLOT_CAPACITY"],
        current_app.config["TIMEZONE"],
        current_app.config["SLOT_INTERVAL_MINUTES"],
        _now(),
    ):
        remaining = max(0, slot.capacity - counts.get(slot.at.isoformat(), 0))
        results.append(
            {
                "iso": slot.at.isoformat(),
                "time": slot.at.strftime("%-I:%M %p"),
                "remaining": remaining,
                "required": required_pizzas,
                "available": remaining >= required_pizzas,
            }
        )
    return results


def _slot_payload(
    day: date,
    menu: MenuSnapshot,
    counts: dict[str, int],
) -> list[dict]:
    payload = []
    for slot in _slots(day, menu, counts):
        remaining = slot["remaining"]
        status = (
            "Full"
            if remaining == 0
            else f"{remaining} pizza{'s' if remaining != 1 else ''} available"
        )
        payload.append(
            {
                "iso": slot["iso"],
                "time": slot["time"],
                "available": slot["available"],
                "remaining": remaining,
                "status": status,
            }
        )
    return payload


def _slots_by_date(
    menu: MenuSnapshot,
    date_choices: list[dict],
    counts: dict[str, int],
) -> dict[str, list[dict]]:
    return {
        choice["iso"]: _slot_payload(
            date.fromisoformat(choice["iso"]), menu, counts
        )
        for choice in date_choices
    }


def _selected_slot(
    menu: MenuSnapshot | None = None,
    *,
    refresh_availability: bool = True,
    counts: dict[str, int] | None = None,
) -> datetime | None:
    raw = session.get("service_at")
    selected = None
    if raw:
        try:
            candidate = datetime.fromisoformat(raw)
            if _slot_capacity(candidate) is not None:
                selected = candidate
                if not refresh_availability:
                    return selected
        except ValueError:
            pass

    menu = menu or _menu()
    counts = _pizza_counts(menu) if counts is None else counts
    available_slots = [
        slot
        for day in [date.fromisoformat(choice["iso"]) for choice in _date_choices()]
        for slot in _slots(day, menu, counts)
        if slot["available"]
    ]
    selected_slot = next(
        (
            slot
            for slot in available_slots
            if selected is not None and slot["iso"] == selected.isoformat()
        ),
        None,
    )
    if selected_slot is not None:
        session["service_at_remaining"] = selected_slot["remaining"]
        return selected

    first = next(
        (
            slot
            for slot in available_slots
            if selected is not None
            and datetime.fromisoformat(slot["iso"]) > selected
        ),
        available_slots[0] if available_slots else None,
    )
    if first:
        selected = datetime.fromisoformat(first["iso"])
        session["service_at"] = selected.isoformat()
        session["service_at_remaining"] = first["remaining"]
        return selected

    session.pop("service_at", None)
    session.pop("service_at_remaining", None)
    return None


def _selected_slot_remaining(
    selected: datetime,
    menu: MenuSnapshot,
) -> int:
    known = session.get("service_at_remaining")
    if isinstance(known, int) and known >= 0:
        return known
    remaining = _remaining(selected, menu)
    session["service_at_remaining"] = remaining
    return remaining


def _validated_modifiers(item: MenuItem, payload: dict) -> list[dict]:
    requested_additions = payload.get("additions", [])
    if not isinstance(requested_additions, list):
        raise ValueError("Choose valid item options.")

    additions = {addition.id: addition for addition in item.additions}
    modifiers = []
    seen_additions = set()
    for requested in requested_additions:
        if not isinstance(requested, dict):
            raise ValueError("Choose valid item options.")
        addition = additions.get(requested.get("id"))
        placement = requested.get("placement")
        option = addition.placements.get(placement) if addition else None
        if not addition or not option or addition.id in seen_additions:
            raise ValueError("Choose valid item options.")
        seen_additions.add(addition.id)
        modifiers.append(
            {
                "kind": "addition",
                "id": addition.id,
                "catalog_object_id": option.id,
                "placement": placement,
            }
        )

    requested_groups = payload.get("modifier_selections", {})
    if not isinstance(requested_groups, dict):
        raise ValueError("Choose valid item options.")
    groups = {group.id: group for group in item.modifier_groups}
    if set(requested_groups) - set(groups):
        raise ValueError("Choose valid item options.")
    for group in item.modifier_groups:
        requested_ids = requested_groups.get(
            group.id,
            [option.id for option in group.options if option.on_by_default],
        )
        if not isinstance(requested_ids, list) or len(requested_ids) != len(
            set(requested_ids)
        ):
            raise ValueError(f"Choose valid options for {group.name}.")
        if len(requested_ids) < group.min_selected:
            raise ValueError(
                f"Choose at least {group.min_selected} option for {group.name}."
            )
        if group.max_selected is not None and len(requested_ids) > group.max_selected:
            raise ValueError(
                f"Choose no more than {group.max_selected} options for {group.name}."
            )
        options = {option.id: option for option in group.options}
        for option_id in requested_ids:
            option = options.get(option_id)
            if not option:
                raise ValueError(f"Choose valid options for {group.name}.")
            modifiers.append(
                {
                    "kind": "square_modifier",
                    "catalog_object_id": option.id,
                    "group_id": group.id,
                }
            )
    return modifiers


def _hydrated_lines(
    lines: list[dict], items_by_id: dict[str, MenuItem]
) -> list[dict]:
    hydrated = []
    for line in lines:
        item = items_by_id.get(line["item_id"])
        if item is None:
            raise SquareConfigurationError(
                "An item in your cart is no longer in the Square menu. Please start a new cart."
            )
        resolved_modifiers = []
        for stored in line.get("modifiers", []):
            if not isinstance(stored, dict):
                raise SquareConfigurationError(
                    f"An option on {item.name} is no longer available."
                )
            if stored.get("kind") == "addition":
                addition = next(
                    (
                        candidate
                        for candidate in item.additions
                        if candidate.id == stored.get("id")
                    ),
                    None,
                )
                placement = stored.get("placement")
                option = addition.placements.get(placement) if addition else None
                if not addition or not option or stored.get(
                    "catalog_object_id", option.id
                ) != option.id:
                    raise SquareConfigurationError(
                        f"An addition on {item.name} is no longer available."
                    )
                resolved_modifiers.append(
                    {
                        **stored,
                        "catalog_object_id": option.id,
                        "catalog_version": option.catalog_version,
                        "name": addition.name,
                        "display": f"{addition.name} · {PLACEMENT_LABELS[placement]}",
                        "price_cents": option.price_cents,
                    }
                )
            elif stored.get("kind") == "square_modifier":
                group = next(
                    (
                        candidate
                        for candidate in item.modifier_groups
                        if candidate.id == stored.get("group_id")
                    ),
                    None,
                )
                option = next(
                    (
                        candidate
                        for candidate in group.options
                        if candidate.id == stored.get("catalog_object_id")
                    ),
                    None,
                ) if group else None
                if not group or not option:
                    raise SquareConfigurationError(
                        f"An option on {item.name} is no longer available."
                    )
                resolved_modifiers.append(
                    {
                        **stored,
                        "catalog_version": option.catalog_version,
                        "name": option.name,
                        "display": f"{group.name}: {option.name}",
                        "price_cents": option.price_cents,
                    }
                )
            else:
                # Supports a cart created by v0.6 until that browser session expires.
                resolved_modifiers.append(stored)
        hydrated.append({**line, "modifiers": resolved_modifiers})
    return hydrated


def _cart_payload(lines: list[dict], items_by_id: dict[str, MenuItem]) -> dict:
    decorated = []
    hydrated = _hydrated_lines(lines, items_by_id)
    for line in hydrated:
        item = items_by_id[line["item_id"]]
        modifiers = line.get("modifiers", [])
        modifier_cents = sum(
            int(modifier.get("price_cents", 0))
            for modifier in modifiers
            if isinstance(modifier, dict)
        )
        decorated.append(
            {
                **line,
                "name": item.name,
                "capacity_category": item.capacity_category,
                "modifiers": [
                    modifier.get("display", modifier.get("name", ""))
                    if isinstance(modifier, dict)
                    else str(modifier)
                    for modifier in modifiers
                ],
                "price": _money(
                    (item.price_cents + modifier_cents) * int(line["quantity"])
                ),
            }
        )
    return {"lines": decorated, "totals": cart_totals(hydrated, items_by_id)}


def _tip_cents(subtotal_cents: int) -> tuple[int, str]:
    choice = request.form.get("tip_choice")
    if choice is None:
        choice = request.form.get("tip_percent", "15")
    if choice in {"0", "15", "20", "25"}:
        percent = int(choice)
        return _percentage_cents(subtotal_cents, percent), (
            "No tip" if percent == 0 else f"{percent}%"
        )
    if choice != "custom":
        raise ValueError("Choose a valid tip amount.")

    raw = request.form.get("custom_tip", "").strip().replace("$", "")
    try:
        dollars = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid custom tip amount.") from exc
    if not dollars.is_finite():
        raise ValueError("Enter a valid custom tip amount.")
    cents = int((dollars * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents < 0 or cents > 100_000:
        raise ValueError("Enter a custom tip between $0 and $1,000.")
    return cents, "Custom"


def _pricing(lines: list[dict], items_by_id: dict[str, MenuItem]) -> dict:
    lines = _hydrated_lines(lines, items_by_id)
    if not current_app.config["USE_SQUARE_DATA"]:
        subtotal = cart_totals(lines, items_by_id)["subtotal_cents"]
        tax = int(
            (
                Decimal(subtotal)
                * Decimal(str(current_app.config["SALES_TAX_RATE"]))
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        return {
            "subtotal_cents": subtotal,
            "tip_basis_cents": subtotal,
            "tax_cents": tax,
            "discount_cents": 0,
            "order_total_cents": subtotal + tax,
            "tax_label": f"Sales tax ({current_app.config['SALES_TAX_RATE'] * 100:g}%)",
        }
    quote = current_app.extensions["square_commerce"].quote(
        lines=lines, items_by_id=items_by_id
    )
    quote["tax_label"] = "Sales tax"
    return quote


def _checkout_totals(pricing: dict, tip_cents: int, tip_label: str) -> dict:
    total_cents = pricing["order_total_cents"] + tip_cents
    discount_cents = pricing.get("discount_cents", 0)
    return {
        "subtotal_cents": pricing["subtotal_cents"],
        "subtotal": _money(pricing["subtotal_cents"]),
        "tip_basis_cents": pricing.get(
            "tip_basis_cents", pricing["subtotal_cents"]
        ),
        "tax_cents": pricing["tax_cents"],
        "tax": _money(pricing["tax_cents"]),
        "tax_label": pricing["tax_label"],
        "discount_cents": discount_cents,
        "discount": _money(discount_cents),
        "order_total_cents": pricing["order_total_cents"],
        "tip_cents": tip_cents,
        "tip": _money(tip_cents),
        "tip_label": tip_label,
        "total_cents": total_cents,
        "total": _money(total_cents),
    }


@storefront.app_errorhandler(SquareConfigurationError)
@storefront.app_errorhandler(SquareAPIError)
def square_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": str(error)}), 503
    return render_template("square_error.html", error=str(error)), 503


@storefront.get("/")
def index():
    if not current_app.config["ORDERING_ENABLED"]:
        return render_template("ordering_paused.html")
    menu = _menu()
    date_choices = _date_choices()
    counts = _pizza_counts(menu)
    selected = _selected_slot(menu, counts=counts)
    selected_display = None
    if selected:
        selected_display = {
            "iso": selected.isoformat(),
            "date": selected.strftime("%A, %B %-d"),
            "time": selected.strftime("%-I:%M %p"),
        }
    return render_template(
        "index.html",
        menu_groups=menu.groups,
        menu_json={item.id: item.public_dict() for item in menu.items},
        date_choices=date_choices,
        slots_by_date=_slots_by_date(menu, date_choices, counts),
        selected=selected_display,
        csrf_token=_csrf_token(),
        cart_payload=_cart_payload(_cart(), menu.items_by_id),
        pizza_limit=current_app.config["CATEGORY_LIMITS"]["pizza"],
        total_limit=current_app.config["CART_TOTAL_LIMIT"],
    )


@storefront.get("/health")
def health():
    response = jsonify(
        {
            "status": "ok",
            "version": current_app.config["APP_VERSION"],
            "ordering_enabled": current_app.config["ORDERING_ENABLED"],
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@storefront.get("/api/slots")
def api_slots():
    try:
        day = date.fromisoformat(request.args["date"])
    except (KeyError, ValueError):
        return jsonify({"error": "Choose a valid service date."}), 400
    allowed = {choice["iso"] for choice in _date_choices()}
    if day.isoformat() not in allowed:
        return jsonify({"error": "That date is not available for ordering."}), 400
    menu = _menu(allow_stale=True)
    slots = _slot_payload(day, menu, _pizza_counts(menu))
    return jsonify({"date": day.isoformat(), "slots": slots})


@storefront.post("/api/selected-slot")
def api_select_slot():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    try:
        selected = datetime.fromisoformat(data["service_at"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Choose a valid pickup time."}), 400

    if _slot_capacity(selected) is None:
        return jsonify({"error": "That pickup time is no longer available."}), 400

    menu = _menu(allow_stale=True)
    remaining = _remaining(selected, menu)
    required_pizzas = max(1, cart_totals(_cart(), menu.items_by_id)["pizza_count"])
    if remaining < required_pizzas:
        return jsonify(
            {"error": "That pickup time can no longer accommodate your order."}
        ), 409

    session["service_at"] = selected.isoformat()
    session["service_at_remaining"] = remaining
    return jsonify(
        {
            "service_at": selected.isoformat(),
            "date": selected.strftime("%A, %B %-d"),
            "time": selected.strftime("%-I:%M %p"),
        }
    )


@storefront.get("/api/cart")
def api_get_cart():
    return jsonify(
        _cart_payload(_cart(), _menu(allow_stale=True).items_by_id)
    )


@storefront.post("/api/cart")
def api_add_to_cart():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    menu = _menu(allow_stale=True)
    items_by_id = menu.items_by_id
    item = items_by_id.get(data.get("item_id"))
    if not item or not item.available:
        return jsonify({"error": "That item is not currently available."}), 400

    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a valid quantity."}), 400
    if quantity < 1 or quantity > current_app.config["CART_TOTAL_LIMIT"]:
        return jsonify({"error": "Choose a valid quantity."}), 400

    try:
        modifiers = _validated_modifiers(item, data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    lines = _cart()
    lines.append(
        {
            "id": str(uuid.uuid4()),
            "item_id": item.id,
            "quantity": quantity,
            "modifiers": modifiers,
        }
    )
    try:
        validate_cart(
            lines,
            items_by_id,
            current_app.config["CART_TOTAL_LIMIT"],
            current_app.config["CATEGORY_LIMITS"],
        )
    except CartLimitError as error:
        return jsonify({"error": str(error)}), 409

    selected = _selected_slot(menu, refresh_availability=False)
    pizza_count = cart_totals(lines, items_by_id)["pizza_count"]
    if (
        selected
        and pizza_count
        and _selected_slot_remaining(selected, menu) < pizza_count
    ):
        return jsonify(
            {"error": "Choose another pickup time before adding this item."}
        ), 409

    _save_cart(lines)
    return jsonify(_cart_payload(lines, items_by_id)), 201


@storefront.delete("/api/cart/<line_id>")
def api_remove_from_cart(line_id: str):
    _require_csrf()
    lines = [line for line in _cart() if line["id"] != line_id]
    _save_cart(lines)
    return jsonify(
        _cart_payload(lines, _menu(allow_stale=True).items_by_id)
    )


@storefront.patch("/api/cart/<line_id>")
def api_update_cart_quantity(line_id: str):
    _require_csrf()
    data = request.get_json(silent=True) or {}
    try:
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose a valid quantity."}), 400
    if quantity < 1 or quantity > current_app.config["CART_TOTAL_LIMIT"]:
        return jsonify({"error": "Choose a valid quantity."}), 400

    menu = _menu(allow_stale=True)
    items_by_id = menu.items_by_id
    lines = _cart()
    if not any(line["id"] == line_id for line in lines):
        return jsonify({"error": "That cart item is no longer available."}), 404
    updated_lines = [
        {**line, "quantity": quantity} if line["id"] == line_id else line
        for line in lines
    ]
    try:
        validate_cart(
            updated_lines,
            items_by_id,
            current_app.config["CART_TOTAL_LIMIT"],
            current_app.config["CATEGORY_LIMITS"],
        )
    except CartLimitError as error:
        return jsonify({"error": str(error)}), 409

    selected = _selected_slot(menu, refresh_availability=False)
    pizza_count = cart_totals(updated_lines, items_by_id)["pizza_count"]
    if (
        selected
        and pizza_count
        and _selected_slot_remaining(selected, menu) < pizza_count
    ):
        return jsonify(
            {"error": "Choose another pickup time before increasing this item."}
        ), 409

    _save_cart(updated_lines)
    return jsonify(_cart_payload(updated_lines, items_by_id))


@storefront.post("/api/checkout-quote")
def api_checkout_quote():
    _require_csrf()
    lines = _cart()
    if not lines:
        return jsonify({"error": "Your cart is empty."}), 400
    menu = _menu(allow_stale=True)
    pricing = _pricing(lines, menu.items_by_id)
    if not current_app.config["DEMO_MODE"]:
        return jsonify(_checkout_totals(pricing, 0, "Choose on Square"))
    data = request.get_json(silent=True) or {}
    choice = str(data.get("tip_choice", "15"))
    if choice in {"0", "15", "20", "25"}:
        percent = int(choice)
        tip_cents = _percentage_cents(pricing["tip_basis_cents"], percent)
        tip_label = "No tip" if percent == 0 else f"{percent}%"
    elif choice == "custom":
        try:
            tip_cents = int(data.get("custom_tip_cents", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Enter a valid custom tip amount."}), 400
        if tip_cents < 0 or tip_cents > 100_000:
            return jsonify({"error": "Enter a valid custom tip amount."}), 400
        tip_label = "Custom"
    else:
        return jsonify({"error": "Choose a valid tip amount."}), 400
    return jsonify(_checkout_totals(pricing, tip_cents, tip_label))


@storefront.route("/checkout", methods=["GET", "POST"])
def checkout():
    if not current_app.config["ORDERING_ENABLED"]:
        return render_template("ordering_paused.html"), 503

    lines = _cart()
    if not lines:
        return redirect(url_for("storefront.index"))
    menu = _menu(allow_stale=request.method == "POST")
    items_by_id = menu.items_by_id
    availability_counts = (
        _pizza_counts(menu) if request.method == "GET" else None
    )
    selected = _selected_slot(
        menu,
        refresh_availability=request.method == "GET",
        counts=availability_counts,
    )
    if not selected:
        return redirect(url_for("storefront.index"))

    pricing = _pricing(lines, items_by_id)
    error = session.pop("checkout_error", None)
    selected_tip = request.form.get("tip_choice", "15")
    custom_tip_value = request.form.get("custom_tip", "")
    payment_method = request.form.get("payment_method", "hosted")
    if current_app.config["DEMO_MODE"]:
        tip_cents = _percentage_cents(pricing["tip_basis_cents"], 15)
        tip_label = "15%"
    else:
        tip_cents = 0
        tip_label = "Choose on Square"
    if request.method == "POST":
        _require_csrf()
        attempt_id = new_attempt_id()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        name = " ".join(part for part in (first_name, last_name) if part)
        email = request.form.get("email", "").strip()
        raw_phone = request.form.get("phone", "").strip()
        notes = request.form.get("notes", "").strip()[:550]
        if not first_name or not last_name or "@" not in email:
            error = "Enter your first name, last name, and a valid email address."
        elif (
            not current_app.config["DEMO_MODE"]
            and payment_method not in {"hosted", "gift_card"}
        ):
            error = "Choose a valid payment method."
        elif (
            payment_method == "gift_card"
            and not current_app.config.get("SQUARE_GIFT_CARDS_ENABLED")
        ):
            error = "Square gift-card checkout is not available right now."
        try:
            phone = _normalize_phone(raw_phone)
        except ValueError as exc:
            if error is None:
                error = str(exc)
            phone = ""
        if current_app.config["DEMO_MODE"]:
            try:
                tip_cents, tip_label = _tip_cents(pricing["tip_basis_cents"])
            except ValueError as exc:
                error = str(exc)
                tip_cents = 0
                tip_label = "Custom"

        checkout_totals = _checkout_totals(pricing, tip_cents, tip_label)
        try:
            browser_total = int(request.form.get("verification_total_cents", ""))
        except ValueError:
            browser_total = -1
        if (
            not current_app.config["DEMO_MODE"]
            and browser_total != checkout_totals["total_cents"]
        ):
            error = "Your total changed. Review the updated total and submit again."

        if error is None:
            customer = {"name": name, "email": email, "phone": phone}
            try:
                if current_app.config["DEMO_MODE"]:
                    selected_capacity = _slot_capacity(selected)
                    if selected_capacity is None:
                        raise SlotUnavailableError(
                            "That pickup time is no longer available."
                        )
                    order = current_app.extensions[
                        "capacity_store"
                    ].confirm_demo_order(
                        service_at=selected.isoformat(),
                        pizza_count=cart_totals(lines, items_by_id)["pizza_count"],
                        capacity=selected_capacity,
                    )
                    receipt_url = None
                else:
                    commerce = current_app.extensions["square_commerce"]
                    pending_checkout = {
                        "attempt_id": attempt_id,
                        "mode": payment_method,
                        "service_at": selected.isoformat(),
                        "customer_name": name,
                        "customer_email": email,
                        "customer_phone": phone,
                        "created_at": _now().isoformat(),
                    }
                    hydrated_lines = _hydrated_lines(lines, items_by_id)
                    if payment_method == "gift_card":
                        result = commerce.create_gift_card_checkout(
                            attempt_id=attempt_id,
                            lines=hydrated_lines,
                            items_by_id=items_by_id,
                            service_at=selected,
                            customer=customer,
                            notes=notes,
                        )
                        pending_checkout["order_id"] = result["order"]["id"]
                        session["pending_square_checkout"] = pending_checkout
                        session.modified = True
                        log_event(
                            current_app.logger,
                            "checkout_created",
                            checkout_attempt_id=attempt_id,
                            square_order_id=result["order"]["id"],
                            payment_mode=payment_method,
                            pickup_at=selected.isoformat(),
                        )
                        return redirect(
                            url_for(
                                "storefront.gift_card_checkout",
                                attempt=attempt_id,
                            ),
                            code=303,
                        )

                    result = commerce.create_checkout(
                        attempt_id=attempt_id,
                        lines=hydrated_lines,
                        items_by_id=items_by_id,
                        service_at=selected,
                        customer=customer,
                        notes=notes,
                        redirect_url=_checkout_return_url(attempt_id),
                    )
                    pending_checkout.update(
                        {
                            "order_id": result["payment_link"]["order_id"],
                            "payment_link_id": result["payment_link"]["id"],
                            "checkout_url": result["checkout_url"],
                        }
                    )
                    session["pending_square_checkout"] = pending_checkout
                    session.modified = True
                    log_event(
                        current_app.logger,
                        "checkout_created",
                        checkout_attempt_id=attempt_id,
                        square_order_id=result["payment_link"]["order_id"],
                        payment_mode=payment_method,
                        pickup_at=selected.isoformat(),
                    )
                    return redirect(result["checkout_url"], code=303)
            except (SlotUnavailableError, SquareAPIError) as exc:
                log_event(
                    current_app.logger,
                    "checkout_creation_failed",
                    level=logging.WARNING,
                    checkout_attempt_id=attempt_id,
                    payment_mode=payment_method,
                    error_type=type(exc).__name__,
                    ambiguous=bool(getattr(exc, "ambiguous", False)),
                )
                error = str(exc)
            else:
                session.pop("cart", None)
                return render_template(
                    "confirmation.html",
                    order=order,
                    customer_name=name,
                    customer_email=email,
                    selected=selected,
                    checkout=checkout_totals,
                    demo_mode=current_app.config["DEMO_MODE"],
                    receipt_url=receipt_url,
                )
    checkout_totals = _checkout_totals(pricing, tip_cents, tip_label)
    date_choices = _date_choices()
    return render_template(
        "checkout.html",
        cart=_cart_payload(lines, items_by_id),
        checkout=checkout_totals,
        selected=selected,
        selected_tip=selected_tip,
        custom_tip_value=custom_tip_value,
        date_choices=date_choices,
        slots_by_date=(
            _slots_by_date(menu, date_choices, availability_counts)
            if availability_counts is not None
            else {}
        ),
        csrf_token=_csrf_token(),
        pizza_limit=current_app.config["CATEGORY_LIMITS"]["pizza"],
        total_limit=current_app.config["CART_TOTAL_LIMIT"],
        sales_tax_rate=current_app.config["SALES_TAX_RATE"],
        error=error,
        demo_mode=current_app.config["DEMO_MODE"],
        square_data_enabled=current_app.config["USE_SQUARE_DATA"],
        gift_cards_enabled=bool(
            current_app.config.get("SQUARE_GIFT_CARDS_ENABLED")
        ),
        payment_method=payment_method,
    )


def _pickup_recipient(order: dict) -> dict:
    for fulfillment in order.get("fulfillments", []):
        if fulfillment.get("type") == "PICKUP":
            return fulfillment.get("pickup_details", {}).get("recipient", {})
    return {}


def _pending_matches_attempt(pending: object, attempt_id: str) -> bool:
    return bool(
        isinstance(pending, dict)
        and attempt_id
        and secrets.compare_digest(
            str(pending.get("attempt_id", "")), attempt_id
        )
    )


@storefront.get("/checkout/gift-card")
def gift_card_checkout():
    pending = session.get("pending_square_checkout")
    attempt_id = request.args.get("attempt", "")
    if (
        not _pending_matches_attempt(pending, attempt_id)
        or pending.get("mode") != "gift_card"
        or not current_app.config.get("SQUARE_GIFT_CARDS_ENABLED")
    ):
        return redirect(url_for("storefront.index"))

    state = current_app.extensions[
        "square_commerce"
    ].gift_card_checkout_state(str(pending["order_id"]))
    if state["status"] == "CANCELED":
        session.pop("pending_square_checkout", None)
        session["checkout_error"] = (
            "That gift-card checkout was canceled. Your cart is unchanged."
        )
        return redirect(url_for("storefront.checkout"))
    if state["status"] == "COMPLETED":
        return redirect(
            url_for("storefront.checkout_complete", attempt=attempt_id)
        )
    if state["remaining_cents"] == 0 and state["payment_ids"]:
        return render_template(
            "square_error.html",
            error=(
                "Square authorized the payment but did not finish the order. "
                "Please contact Pizzeria Mari before trying another payment."
            ),
        ), 503

    full_name = str(pending.get("customer_name", "")).strip().split(maxsplit=1)
    given_name = full_name[0] if full_name else ""
    family_name = full_name[1] if len(full_name) > 1 else ""
    square_sdk_url = (
        "https://sandbox.web.squarecdn.com/v1/square.js"
        if current_app.config["SQUARE_ENVIRONMENT"] == "sandbox"
        else "https://web.squarecdn.com/v1/square.js"
    )
    return render_template(
        "gift_card_payment.html",
        pending=pending,
        selected=datetime.fromisoformat(pending["service_at"]),
        csrf_token=_csrf_token(),
        square_application_id=current_app.config["SQUARE_APPLICATION_ID"],
        square_location_id=current_app.config["SQUARE_LOCATION_ID"],
        square_sdk_url=square_sdk_url,
        payment_state={
            "giftCardApplied": state["gift_card_applied"],
            "paidCents": state["paid_cents"],
            "remainingCents": state["remaining_cents"],
            "totalCents": state["total_cents"],
        },
        billing_contact={
            "givenName": given_name,
            "familyName": family_name,
            "email": pending.get("customer_email", ""),
            "phone": pending.get("customer_phone", ""),
        },
    )


@storefront.post("/api/gift-card/payment")
def api_gift_card_payment():
    _require_csrf()
    if not current_app.config.get("SQUARE_GIFT_CARDS_ENABLED"):
        return jsonify({"error": "Square gift-card checkout is unavailable."}), 404
    pending = session.get("pending_square_checkout")
    data = request.get_json(silent=True) or {}
    attempt_id = str(data.get("attempt_id", ""))
    if (
        not _pending_matches_attempt(pending, attempt_id)
        or pending.get("mode") != "gift_card"
    ):
        return jsonify({"error": "This gift-card checkout expired."}), 409

    source_id = data.get("source_id")
    payment_method = data.get("payment_method")
    if (
        not isinstance(source_id, str)
        or not source_id
        or len(source_id) > 512
    ):
        return jsonify({"error": "Square did not return a valid payment token."}), 400
    if payment_method not in {"gift_card", "card"}:
        return jsonify({"error": "Choose a valid payment method."}), 400

    try:
        result = current_app.extensions["square_commerce"].apply_gift_card_payment(
            order_id=str(pending["order_id"]),
            attempt_id=attempt_id,
            payment_method=payment_method,
            source_id=source_id,
        )
    except SquareAPIError as exc:
        log_event(
            current_app.logger,
            "gift_card_payment_failed",
            level=logging.WARNING,
            checkout_attempt_id=attempt_id,
            square_order_id=str(pending["order_id"]),
            payment_mode=payment_method,
            error_type=type(exc).__name__,
            ambiguous=exc.ambiguous,
        )
        return jsonify({"error": str(exc)}), 503 if exc.ambiguous else 409

    log_event(
        current_app.logger,
        "gift_card_payment_updated",
        checkout_attempt_id=attempt_id,
        square_order_id=str(pending["order_id"]),
        payment_mode=payment_method,
        order_status=result["status"],
        remaining_cents=result.get("remaining_cents", 0),
    )

    if result["status"] == "COMPLETED":
        return jsonify(
            {
                "status": "COMPLETED",
                "remaining_cents": 0,
                "redirect_url": url_for(
                    "storefront.checkout_complete", attempt=attempt_id
                ),
            }
        )
    return jsonify(
        {
            "status": "PARTIAL",
            "applied_cents": result["applied_cents"],
            "paid_cents": result["paid_cents"],
            "remaining_cents": result["remaining_cents"],
        }
    )


@storefront.get("/checkout/complete")
def checkout_complete():
    pending = session.get("pending_square_checkout")
    attempt_id = request.args.get("attempt", "")
    if not _pending_matches_attempt(pending, attempt_id):
        return redirect(url_for("storefront.index"))

    payment_mode = pending.get("mode", "hosted")
    commerce = current_app.extensions["square_commerce"]
    if payment_mode == "gift_card":
        result = commerce.gift_card_checkout_state(str(pending["order_id"]))
    else:
        result = commerce.checkout_result(str(pending["order_id"]))
    if result["status"] in {"CANCELED", "FAILED"}:
        session.pop("pending_square_checkout", None)
        session["checkout_error"] = (
            "Square did not complete that payment. Your cart is unchanged."
        )
        return redirect(url_for("storefront.checkout"))
    if result["status"] != "COMPLETED":
        if payment_mode == "gift_card":
            return redirect(
                url_for("storefront.gift_card_checkout", attempt=attempt_id)
            )
        return render_template(
            "payment_pending.html",
            pending=pending,
            selected=datetime.fromisoformat(pending["service_at"]),
            csrf_token=_csrf_token(),
        ), 202

    order_data = result["order"]
    payment = result["payment"]
    recipient = _pickup_recipient(order_data)
    customer_name = recipient.get("display_name") or pending["customer_name"]
    customer_email = (
        (payment or {}).get("buyer_email_address")
        or recipient.get("email_address")
        or pending["customer_email"]
    )
    if payment_mode == "gift_card":
        total_cents = int(result["total_cents"])
    else:
        total_cents = int(
            payment.get("total_money", payment.get("amount_money", {})).get(
                "amount", 0
            )
        )
    selected = datetime.fromisoformat(pending["service_at"])
    order = SimpleNamespace(
        id=order_data["id"],
        confirmation_code=order_data["id"][-8:].upper(),
        service_at=selected.isoformat(),
    )
    session.pop("cart", None)
    session.pop("service_at", None)
    session.pop("pending_square_checkout", None)
    log_event(
        current_app.logger,
        "checkout_completed",
        checkout_attempt_id=attempt_id,
        square_order_id=str(order_data["id"]),
        payment_mode=payment_mode,
        pickup_at=selected.isoformat(),
    )
    return render_template(
        "confirmation.html",
        order=order,
        customer_name=customer_name,
        customer_email=customer_email,
        selected=selected,
        checkout={"total": _money(total_cents)},
        demo_mode=False,
        receipt_url=(payment or {}).get("receipt_url"),
        payment_mode=payment_mode,
    )
