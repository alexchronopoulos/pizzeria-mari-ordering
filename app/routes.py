from __future__ import annotations

import secrets
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
from .scheduling import is_valid_slot, service_dates, slots_for_date
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


def _menu() -> MenuSnapshot:
    return current_app.extensions["menu_provider"].snapshot()


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


def _pizza_counts(menu: MenuSnapshot) -> dict[str, int]:
    if not current_app.config["USE_SQUARE_DATA"]:
        return current_app.extensions["capacity_store"].counts_by_slot()
    return current_app.extensions["square_commerce"].pizza_counts_by_slot(
        variation_ids=menu.pizza_catalog_object_ids,
        now=_now(),
    )


def _remaining(service_at: datetime, menu: MenuSnapshot) -> int:
    confirmed = _pizza_counts(menu).get(service_at.isoformat(), 0)
    return max(0, current_app.config["PIZZA_SLOT_CAPACITY"] - confirmed)


def _slots(day: date, menu: MenuSnapshot | None = None) -> list[dict]:
    menu = menu or _menu()
    items_by_id = menu.items_by_id
    required_pizzas = max(1, cart_totals(_cart(), items_by_id)["pizza_count"])
    capacity = current_app.config["PIZZA_SLOT_CAPACITY"]
    counts = _pizza_counts(menu)
    results = []
    for slot in slots_for_date(
        day,
        current_app.config["SERVICE_HOURS"],
        current_app.config["TIMEZONE"],
        current_app.config["SLOT_INTERVAL_MINUTES"],
        _now(),
    ):
        remaining = max(0, capacity - counts.get(slot.isoformat(), 0))
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


def _selected_slot(menu: MenuSnapshot | None = None) -> datetime | None:
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

    menu = menu or _menu()
    for day in [date.fromisoformat(choice["iso"]) for choice in _date_choices()]:
        first = next((slot for slot in _slots(day, menu) if slot["available"]), None)
        if first:
            selected = datetime.fromisoformat(first["iso"])
            session["service_at"] = selected.isoformat()
            return selected
    return None


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
    menu = _menu()
    selected = _selected_slot(menu)
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
        date_choices=_date_choices(),
        selected=selected_display,
        csrf_token=_csrf_token(),
        cart_payload=_cart_payload(_cart(), menu.items_by_id),
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

    menu = _menu()
    remaining = _remaining(selected, menu)
    required_pizzas = max(1, cart_totals(_cart(), menu.items_by_id)["pizza_count"])
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
    return jsonify(_cart_payload(_cart(), _menu().items_by_id))


@storefront.post("/api/cart")
def api_add_to_cart():
    _require_csrf()
    data = request.get_json(silent=True) or {}
    menu = _menu()
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

    selected = _selected_slot(menu)
    pizza_count = cart_totals(lines, items_by_id)["pizza_count"]
    if selected and pizza_count and _remaining(selected, menu) < pizza_count:
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
    return jsonify(_cart_payload(lines, _menu().items_by_id))


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

    menu = _menu()
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

    selected = _selected_slot(menu)
    pizza_count = cart_totals(updated_lines, items_by_id)["pizza_count"]
    if selected and pizza_count and _remaining(selected, menu) < pizza_count:
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
    menu = _menu()
    pricing = _pricing(lines, menu.items_by_id)
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
    lines = _cart()
    if not lines:
        return redirect(url_for("storefront.index"))
    menu = _menu()
    items_by_id = menu.items_by_id
    selected = _selected_slot(menu)
    if not selected:
        return redirect(url_for("storefront.index"))

    pricing = _pricing(lines, items_by_id)
    error = None
    selected_tip = request.form.get("tip_choice", "15")
    custom_tip_value = request.form.get("custom_tip", "")
    tip_cents = _percentage_cents(pricing["tip_basis_cents"], 15)
    tip_label = "15%"
    attempt_id = session.setdefault("checkout_attempt_id", new_attempt_id())
    if request.method == "POST":
        _require_csrf()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        notes = request.form.get("notes", "").strip()[:550]
        submitted_attempt = request.form.get("checkout_attempt_id", "")
        source_id = request.form.get("source_id", "").strip()
        if (
            not current_app.config["DEMO_MODE"]
            and submitted_attempt != attempt_id
        ):
            error = "This checkout page expired. Refresh it and try again."
        elif not name or "@" not in email:
            error = "Enter your name and a valid email address."
        elif not current_app.config["DEMO_MODE"] and not source_id:
            error = "Enter your card details before placing the order."
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
                with current_app.extensions["checkout_locks"].acquire(
                    selected.isoformat()
                ):
                    if _remaining(selected, menu) < pricing.get(
                        "pizza_count", cart_totals(lines, items_by_id)["pizza_count"]
                    ):
                        raise SlotUnavailableError(
                            "That pickup time can no longer accommodate your order. Please choose another."
                        )
                    if current_app.config["DEMO_MODE"]:
                        order = current_app.extensions[
                            "capacity_store"
                        ].confirm_demo_order(
                            service_at=selected.isoformat(),
                            pizza_count=cart_totals(lines, items_by_id)["pizza_count"],
                            capacity=current_app.config["PIZZA_SLOT_CAPACITY"],
                        )
                        receipt_url = None
                    else:
                        result = current_app.extensions["square_commerce"].place_order(
                            source_id=source_id,
                            attempt_id=attempt_id,
                            lines=_hydrated_lines(lines, items_by_id),
                            items_by_id=items_by_id,
                            service_at=selected,
                            customer=customer,
                            notes=notes,
                            tip_cents=tip_cents,
                            expected_order_total_cents=pricing[
                                "order_total_cents"
                            ],
                        )
                        square_order = result["order"]
                        payment = result["payment"]
                        order = SimpleNamespace(
                            id=square_order["id"],
                            confirmation_code=square_order["id"][-8:].upper(),
                            service_at=selected.isoformat(),
                        )
                        receipt_url = payment.get("receipt_url")
            except (SlotUnavailableError, SquareAPIError) as exc:
                error = str(exc)
                session["checkout_attempt_id"] = new_attempt_id()
                attempt_id = session["checkout_attempt_id"]
            else:
                session.pop("cart", None)
                session.pop("checkout_attempt_id", None)
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
    return render_template(
        "checkout.html",
        cart=_cart_payload(lines, items_by_id),
        checkout=checkout_totals,
        selected=selected,
        selected_tip=selected_tip,
        custom_tip_value=custom_tip_value,
        date_choices=_date_choices(),
        csrf_token=_csrf_token(),
        checkout_attempt_id=attempt_id,
        pizza_limit=current_app.config["CATEGORY_LIMITS"]["pizza"],
        total_limit=current_app.config["CART_TOTAL_LIMIT"],
        sales_tax_rate=current_app.config["SALES_TAX_RATE"],
        error=error,
        demo_mode=current_app.config["DEMO_MODE"],
        square_application_id=current_app.config.get("SQUARE_APPLICATION_ID"),
        square_location_id=current_app.config.get("SQUARE_LOCATION_ID"),
        square_js_url=current_app.config.get("SQUARE_JS_URL"),
        square_data_enabled=current_app.config["USE_SQUARE_DATA"],
    )
