from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
from .menu import ITEMS, ITEMS_BY_ID, grouped_menu
from .scheduling import is_valid_slot, service_dates, slots_for_date


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


def _capacity_store():
    return current_app.extensions["capacity_store"]


def _date_choices() -> list[dict]:
    days = service_dates(
        current_app.config["SERVICE_HOURS"],
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


def _slots(day: date) -> list[dict]:
    capacity = current_app.config["PIZZA_SLOT_CAPACITY"]
    required_pizzas = max(1, cart_totals(_cart(), ITEMS_BY_ID)["pizza_count"])
    results = []
    for slot in slots_for_date(
        day,
        current_app.config["SERVICE_HOURS"],
        current_app.config["TIMEZONE"],
        current_app.config["SLOT_INTERVAL_MINUTES"],
        _now(),
    ):
        remaining = _capacity_store().remaining(slot.isoformat(), capacity)
        results.append(
            {
                "iso": slot.isoformat(),
                "time": slot.strftime("%-I:%M %p"),
                "remaining": remaining,
                "required": required_pizzas,
                "available": remaining >= required_pizzas,
            }
        )
    return results


def _selected_slot() -> datetime | None:
    raw = session.get("service_at")
    if raw:
        try:
            selected = datetime.fromisoformat(raw)
            if is_valid_slot(
                selected,
                current_app.config["SERVICE_HOURS"],
                current_app.config["TIMEZONE"],
                current_app.config["ADVANCE_DAYS"],
                current_app.config["SLOT_INTERVAL_MINUTES"],
                _now(),
            ):
                return selected
        except ValueError:
            pass

    for day in [date.fromisoformat(choice["iso"]) for choice in _date_choices()]:
        first = next((slot for slot in _slots(day) if slot["available"]), None)
        if first:
            selected = datetime.fromisoformat(first["iso"])
            session["service_at"] = selected.isoformat()
            return selected
    return None


def _validated_modifiers(item, payload: dict) -> list[dict]:
    requested_additions = payload.get("additions", [])
    if not isinstance(requested_additions, list):
        raise ValueError("Choose valid item options.")

    addition_options = {option.id: option for option in item.additions}
    modifiers = []
    seen_additions = set()
    for requested in requested_additions:
        if not isinstance(requested, dict):
            raise ValueError("Choose valid item options.")
        option = addition_options.get(requested.get("id"))
        placement = requested.get("placement")
        if (
            not option
            or placement not in PLACEMENT_LABELS
            or option.id in seen_additions
        ):
            raise ValueError("Choose valid item options.")
        seen_additions.add(option.id)
        price_cents = (
            option.whole_price_cents
            if placement == "whole"
            else option.half_price_cents
        )
        modifiers.append(
            {
                "kind": "addition",
                "id": option.id,
                "name": option.name,
                "placement": placement,
                "display": f"{option.name} · {PLACEMENT_LABELS[placement]}",
                "price_cents": price_cents,
            }
        )
    return modifiers


def _cart_payload(lines: list[dict]) -> dict:
    decorated = []
    for line in lines:
        item = ITEMS_BY_ID[line["item_id"]]
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
    return {"lines": decorated, "totals": cart_totals(lines, ITEMS_BY_ID)}


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


def _checkout_totals(subtotal_cents: int, tip_cents: int, tip_label: str) -> dict:
    tax_cents = int(
        (
            Decimal(subtotal_cents)
            * Decimal(str(current_app.config["SALES_TAX_RATE"]))
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    total_cents = subtotal_cents + tax_cents + tip_cents
    return {
        "subtotal_cents": subtotal_cents,
        "subtotal": _money(subtotal_cents),
        "tax_cents": tax_cents,
        "tax": _money(tax_cents),
        "tax_rate": f"{current_app.config['SALES_TAX_RATE'] * 100:g}%",
        "tip_cents": tip_cents,
        "tip": _money(tip_cents),
        "tip_label": tip_label,
        "total_cents": total_cents,
        "total": _money(total_cents),
    }


@storefront.get("/")
def index():
    selected = _selected_slot()
    selected_display = None
    if selected:
        selected_display = {
            "iso": selected.isoformat(),
            "date": selected.strftime("%A, %B %-d"),
            "time": selected.strftime("%-I:%M %p"),
        }
    return render_template(
        "index.html",
        menu_groups=grouped_menu(),
        menu_json={item.id: item.public_dict() for item in ITEMS},
        date_choices=_date_choices(),
        selected=selected_display,
        csrf_token=_csrf_token(),
        cart_payload=_cart_payload(_cart()),
        pizza_limit=current_app.config["CATEGORY_LIMITS"]["pizza"],
        total_limit=current_app.config["CART_TOTAL_LIMIT"],
    )


@storefront.get("/api/slots")
def api_slots():
    try:
        day = date.fromisoformat(request.args["date"])
    except (KeyError, ValueError):
        return jsonify({"error": "Choose a valid service date."}), 400
    allowed = {choice["iso"] for choice in _date_choices()}
    if day.isoformat() not in allowed:
        return jsonify({"error": "That date is not available for ordering."}), 400
    slots = []
    for slot in _slots(day):
        status = None
        if not slot["available"]:
            status = "Full" if slot["remaining"] == 0 else "Unavailable"
        slots.append(
            {
                "iso": slot["iso"],
                "time": slot["time"],
                "available": slot["available"],
                "status": status,
            }
        )
    return jsonify({"date": day.isoformat(), "slots": slots})


@storefront.post("/api/selected-slot")
def api_select_slot():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    try:
        selected = datetime.fromisoformat(data["service_at"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Choose a valid pickup time."}), 400

    if not is_valid_slot(
        selected,
        current_app.config["SERVICE_HOURS"],
        current_app.config["TIMEZONE"],
        current_app.config["ADVANCE_DAYS"],
        current_app.config["SLOT_INTERVAL_MINUTES"],
        _now(),
    ):
        return jsonify({"error": "That pickup time is no longer available."}), 400

    remaining = _capacity_store().remaining(
        selected.isoformat(), current_app.config["PIZZA_SLOT_CAPACITY"]
    )
    required_pizzas = max(1, cart_totals(_cart(), ITEMS_BY_ID)["pizza_count"])
    if remaining < required_pizzas:
        return jsonify(
            {"error": "That pickup time can no longer accommodate your order."}
        ), 409

    session["service_at"] = selected.isoformat()
    return jsonify(
        {
            "service_at": selected.isoformat(),
            "date": selected.strftime("%A, %B %-d"),
            "time": selected.strftime("%-I:%M %p"),
        }
    )


@storefront.get("/api/cart")
def api_get_cart():
    return jsonify(_cart_payload(_cart()))


@storefront.post("/api/cart")
def api_add_to_cart():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    item = ITEMS_BY_ID.get(data.get("item_id"))
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
            ITEMS_BY_ID,
            current_app.config["CART_TOTAL_LIMIT"],
            current_app.config["CATEGORY_LIMITS"],
        )
    except CartLimitError as error:
        return jsonify({"error": str(error)}), 409

    selected = _selected_slot()
    pizza_count = cart_totals(lines, ITEMS_BY_ID)["pizza_count"]
    if selected and pizza_count:
        remaining = _capacity_store().remaining(
            selected.isoformat(), current_app.config["PIZZA_SLOT_CAPACITY"]
        )
        if remaining < pizza_count:
            return jsonify(
                {
                    "error": "Choose another pickup time before adding this item."
                }
            ), 409

    _save_cart(lines)
    return jsonify(_cart_payload(lines)), 201


@storefront.delete("/api/cart/<line_id>")
def api_remove_from_cart(line_id: str):
    _require_csrf()
    lines = [line for line in _cart() if line["id"] != line_id]
    _save_cart(lines)
    return jsonify(_cart_payload(lines))


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

    lines = _cart()
    matching_line = next((line for line in lines if line["id"] == line_id), None)
    if matching_line is None:
        return jsonify({"error": "That cart item is no longer available."}), 404

    updated_lines = [
        {**line, "quantity": quantity} if line["id"] == line_id else line
        for line in lines
    ]
    try:
        validate_cart(
            updated_lines,
            ITEMS_BY_ID,
            current_app.config["CART_TOTAL_LIMIT"],
            current_app.config["CATEGORY_LIMITS"],
        )
    except CartLimitError as error:
        return jsonify({"error": str(error)}), 409

    selected = _selected_slot()
    pizza_count = cart_totals(updated_lines, ITEMS_BY_ID)["pizza_count"]
    if selected and pizza_count:
        remaining = _capacity_store().remaining(
            selected.isoformat(), current_app.config["PIZZA_SLOT_CAPACITY"]
        )
        if remaining < pizza_count:
            return jsonify(
                {"error": "Choose another pickup time before increasing this item."}
            ), 409

    _save_cart(updated_lines)
    return jsonify(_cart_payload(updated_lines))


@storefront.route("/checkout", methods=["GET", "POST"])
def checkout():
    lines = _cart()
    if not lines:
        return redirect(url_for("storefront.index"))
    selected = _selected_slot()
    if not selected:
        return redirect(url_for("storefront.index"))

    totals = cart_totals(lines, ITEMS_BY_ID)
    error = None
    selected_tip = request.form.get("tip_choice", "15")
    custom_tip_value = request.form.get("custom_tip", "")
    tip_cents = _percentage_cents(totals["subtotal_cents"], 15)
    tip_label = "15%"
    if request.method == "POST":
        _require_csrf()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        notes = request.form.get("notes", "").strip()[:550]
        if not name or "@" not in email:
            error = "Enter your name and a valid email address."
        try:
            tip_cents, tip_label = _tip_cents(totals["subtotal_cents"])
        except ValueError as exc:
            error = str(exc)
            tip_cents = 0
            tip_label = "Custom"

        if error is None:
            checkout_totals = _checkout_totals(
                totals["subtotal_cents"], tip_cents, tip_label
            )
            try:
                order = _capacity_store().confirm_demo_order(
                    service_at=selected.isoformat(),
                    pizza_count=totals["pizza_count"],
                    capacity=current_app.config["PIZZA_SLOT_CAPACITY"],
                    customer={"name": name, "email": email, "phone": phone},
                    notes=notes,
                    tip_cents=tip_cents,
                    cart=lines,
                    subtotal_cents=checkout_totals["subtotal_cents"],
                    tax_cents=checkout_totals["tax_cents"],
                    total_cents=checkout_totals["total_cents"],
                )
            except SlotUnavailableError as exc:
                error = str(exc)
            else:
                session.pop("cart", None)
                session["last_confirmation"] = order.confirmation_code
                return render_template(
                    "confirmation.html",
                    order=order,
                    customer_name=name,
                    customer_email=email,
                    selected=selected,
                    checkout=checkout_totals,
                    demo_mode=current_app.config["DEMO_MODE"],
                )

    decorated = _cart_payload(lines)
    checkout_totals = _checkout_totals(totals["subtotal_cents"], tip_cents, tip_label)
    return render_template(
        "checkout.html",
        cart=decorated,
        checkout=checkout_totals,
        selected=selected,
        selected_tip=selected_tip,
        custom_tip_value=custom_tip_value,
        date_choices=_date_choices(),
        csrf_token=_csrf_token(),
        pizza_limit=current_app.config["CATEGORY_LIMITS"]["pizza"],
        total_limit=current_app.config["CART_TOTAL_LIMIT"],
        sales_tax_rate=current_app.config["SALES_TAX_RATE"],
        error=error,
        demo_mode=current_app.config["DEMO_MODE"],
    )
