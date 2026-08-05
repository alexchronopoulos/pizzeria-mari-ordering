from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator
from zoneinfo import ZoneInfo

import httpx

from .menu import (
    Addition,
    MenuItem,
    MenuSnapshot,
    ModifierGroup,
    ModifierOption,
)


SQUARE_API_VERSION = "2026-07-15"
SQUARE_BASE_URLS = {
    "sandbox": "https://connect.squareupsandbox.com",
    "production": "https://connect.squareup.com",
}
SQUARE_JS_URLS = {
    "sandbox": "https://sandbox.web.squarecdn.com/v1/square.js",
    "production": "https://web.squarecdn.com/v1/square.js",
}
class SquareAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        errors: list[dict] | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.ambiguous = ambiguous


class SquareConfigurationError(RuntimeError):
    pass


class SquareClient:
    def __init__(
        self,
        *,
        access_token: str,
        environment: str,
        api_version: str = SQUARE_API_VERSION,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if environment not in SQUARE_BASE_URLS:
            raise SquareConfigurationError(
                "SQUARE_ENVIRONMENT must be either sandbox or production."
            )
        self.base_url = SQUARE_BASE_URLS[environment]
        self.api_version = api_version
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(12.0),
            transport=transport,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Square-Version": api_version,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        retry_safe: bool = False,
    ) -> dict:
        attempts = 2 if retry_safe else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method, path, params=params, json=json_body
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    continue
                raise SquareAPIError(
                    "Square could not be reached. Please try again.", ambiguous=True
                ) from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                continue
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}
            if response.is_success:
                return payload

            errors = payload.get("errors", []) if isinstance(payload, dict) else []
            detail = next(
                (
                    error.get("detail")
                    for error in errors
                    if isinstance(error, dict) and error.get("detail")
                ),
                None,
            )
            if response.status_code in {401, 403}:
                message = "Square rejected the configured credentials."
            elif response.status_code == 404:
                message = "A Square catalog item used by this order is no longer available."
            elif response.status_code == 409:
                message = "Square reported a conflicting update. Please try again."
            elif response.status_code == 429:
                message = "Square is temporarily busy. Please try again."
            elif detail and response.status_code < 500:
                message = detail
            else:
                message = "Square could not process the request. Please try again."
            raise SquareAPIError(message, errors=errors)

        raise SquareAPIError(
            "Square could not be reached. Please try again.",
            ambiguous=True,
        ) from last_error

    def list_catalog(self) -> list[dict]:
        objects: list[dict] = []
        cursor = None
        while True:
            params = {
                "types": "ITEM,CATEGORY,MODIFIER_LIST,IMAGE",
                "include_deleted_objects": "true",
            }
            if cursor:
                params["cursor"] = cursor
            payload = self.request("GET", "/v2/catalog/list", params=params)
            objects.extend(payload.get("objects", []))
            cursor = payload.get("cursor")
            if not cursor:
                return objects

    def calculate_order(self, order: dict) -> dict:
        payload = self.request(
            "POST", "/v2/orders/calculate", json_body={"order": order}
        )
        return payload["order"]

    def create_order(self, order: dict, idempotency_key: str) -> dict:
        payload = self.request(
            "POST",
            "/v2/orders",
            json_body={"order": order, "idempotency_key": idempotency_key},
            retry_safe=True,
        )
        return payload["order"]

    def create_payment(self, payment: dict) -> dict:
        payload = self.request(
            "POST",
            "/v2/payments",
            json_body=payment,
            retry_safe=True,
        )
        return payload["payment"]

    def cancel_order(self, order: dict, idempotency_key: str) -> None:
        self.request(
            "PUT",
            f"/v2/orders/{order['id']}",
            json_body={
                "order": {
                    "location_id": order["location_id"],
                    "version": order["version"],
                    "state": "CANCELED",
                },
                "idempotency_key": idempotency_key,
            },
            retry_safe=True,
        )

    def search_orders(self, *, location_id: str, created_after: datetime) -> list[dict]:
        orders: list[dict] = []
        cursor = None
        while True:
            body = {
                "location_ids": [location_id],
                "limit": 500,
                "return_entries": False,
                "query": {
                    "filter": {
                        "state_filter": {"states": ["OPEN", "COMPLETED"]},
                        "date_time_filter": {
                            "created_at": {
                                "start_at": created_after.astimezone(timezone.utc)
                                .isoformat()
                                .replace("+00:00", "Z")
                            }
                        },
                    },
                    "sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"},
                },
            }
            if cursor:
                body["cursor"] = cursor
            payload = self.request("POST", "/v2/orders/search", json_body=body)
            orders.extend(payload.get("orders", []))
            cursor = payload.get("cursor")
            if not cursor:
                return orders


def _present_at_location(obj: dict, location_id: str) -> bool:
    if location_id in obj.get("absent_at_location_ids", []):
        return False
    if obj.get("present_at_all_locations"):
        return True
    explicit = obj.get("present_at_location_ids")
    return location_id in explicit if explicit is not None else True


def _location_override(data: dict, location_id: str) -> dict:
    return next(
        (
            override
            for override in data.get("location_overrides", [])
            if override.get("location_id") == location_id
        ),
        {},
    )


def _price_cents(data: dict, location_id: str) -> int | None:
    override = _location_override(data, location_id)
    money = override.get("price_money") or data.get("price_money")
    if not money or money.get("currency", "USD") != "USD":
        return None
    return int(money.get("amount", 0))


def _sold_out(data: dict, location_id: str) -> bool:
    return bool(_location_override(data, location_id).get("sold_out", False))


def _category_ids(item_data: dict) -> list[str]:
    ids = [entry.get("id") for entry in item_data.get("categories", [])]
    if item_data.get("category_id"):
        ids.append(item_data["category_id"])
    return [category_id for category_id in ids if category_id]


def _addition_key(name: str) -> str:
    normalized = " ".join(name.casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _normalized_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _canonical_addition_name(value: str) -> str:
    """Normalize harmless placement text used in parallel Square lists."""
    words = _normalized_name(value).split()
    placement_words = {
        "whole",
        "pie",
        "pizza",
        "first",
        "1st",
        "second",
        "2nd",
        "half",
    }
    reduced = [word for word in words if word not in placement_words]
    return " ".join(reduced) or " ".join(words)


def _placement_for_list_name(name: str) -> str | None:
    """Recognize Square addition-list names without requiring exact punctuation."""
    words = set(_normalized_name(name).split())
    if not words.intersection({"addition", "additions"}):
        return None
    if "whole" in words:
        return "whole"
    if "half" in words and words.intersection({"first", "1st"}):
        return "first_half"
    if "half" in words and words.intersection({"second", "2nd"}):
        return "second_half"
    return None


def _selection_limits(
    info: dict, list_data: dict, selection_type: str
) -> tuple[int, int | None]:
    """Resolve Square's item overrides, including its -1 inheritance sentinel."""
    item_min = int(info.get("min_selected_modifiers", -1))
    item_max = int(info.get("max_selected_modifiers", -1))
    if item_min == -1 and item_max == -1:
        raw_min = int(list_data.get("min_selected_modifiers", 0))
        raw_max = int(list_data.get("max_selected_modifiers", 0))
    else:
        raw_min = item_min
        raw_max = item_max

    min_selected = max(0, raw_min)
    max_selected = raw_max if raw_max > 0 else None
    if selection_type == "SINGLE":
        max_selected = 1
    return min_selected, max_selected


class SquareCatalogProvider:
    def __init__(
        self,
        *,
        client: SquareClient,
        location_id: str,
        allowed_category_names: tuple[str, ...],
        pizza_category_names: tuple[str, ...],
        excluded_modifier_list_names: tuple[str, ...] = (),
        cache_seconds: int = 30,
    ) -> None:
        self.client = client
        self.location_id = location_id
        self.allowed_category_names = allowed_category_names
        self.pizza_category_names = set(pizza_category_names)
        self.excluded_modifier_list_names = {
            _normalized_name(name) for name in excluded_modifier_list_names
        }
        self.cache_seconds = cache_seconds
        self._cached: MenuSnapshot | None = None
        self._cached_at = 0.0
        self._lock = threading.Lock()

    def snapshot(self) -> MenuSnapshot:
        now = time.monotonic()
        if self._cached and now - self._cached_at < self.cache_seconds:
            return self._cached
        with self._lock:
            now = time.monotonic()
            if self._cached and now - self._cached_at < self.cache_seconds:
                return self._cached
            snapshot = self._build(self.client.list_catalog())
            self._cached = snapshot
            self._cached_at = now
            return snapshot

    def _build(self, objects: list[dict]) -> MenuSnapshot:
        categories = {
            obj["id"]: obj
            for obj in objects
            if obj.get("type") == "CATEGORY"
            and not obj.get("is_deleted", False)
            and obj.get("category_data", {}).get("category_type", "REGULAR_CATEGORY")
            == "REGULAR_CATEGORY"
        }
        categories_by_name = {
            obj.get("category_data", {}).get("name", ""): obj
            for obj in categories.values()
        }
        missing = [
            name for name in self.allowed_category_names if name not in categories_by_name
        ]
        if missing:
            raise SquareConfigurationError(
                "Square is missing configured menu categories: " + ", ".join(missing)
            )

        allowed = {
            categories_by_name[name]["id"]: (index, name)
            for index, name in enumerate(self.allowed_category_names)
        }
        modifier_lists = {
            obj["id"]: obj
            for obj in objects
            if obj.get("type") == "MODIFIER_LIST"
            and not obj.get("is_deleted", False)
            and _present_at_location(obj, self.location_id)
        }
        images = {
            obj["id"]: obj.get("image_data", {}).get("url")
            for obj in objects
            if obj.get("type") == "IMAGE"
            and not obj.get("is_deleted", False)
        }
        grouped: dict[str, list[MenuItem]] = {category_id: [] for category_id in allowed}
        capacity_object_ids: set[str] = set()

        for item_object in objects:
            if item_object.get("type") != "ITEM":
                continue
            item_data = item_object.get("item_data", {})
            category_id = next(
                (category_id for category_id in _category_ids(item_data) if category_id in allowed),
                None,
            )
            if not category_id:
                continue
            category_name = allowed[category_id][1]
            if category_name in self.pizza_category_names:
                capacity_object_ids.update(
                    variation.get("id")
                    for variation in item_data.get("variations", [])
                    if variation.get("id")
                )
            if (
                item_object.get("is_deleted", False)
                or item_data.get("is_archived")
                or not _present_at_location(item_object, self.location_id)
            ):
                continue
            additions, modifier_groups = self._modifiers(
                item_data.get("modifier_list_info", []), modifier_lists
            )
            variations = [
                variation
                for variation in item_data.get("variations", [])
                if _present_at_location(variation, self.location_id)
            ]
            for variation in variations:
                variation_data = variation.get("item_variation_data", {})
                price_cents = _price_cents(variation_data, self.location_id)
                if price_cents is None or variation_data.get("sellable") is False:
                    continue
                variation_name = variation_data.get("name", "").strip()
                item_name = item_data.get("name", "Unnamed item").strip()
                name = (
                    f"{item_name} · {variation_name}"
                    if len(variations) > 1 and variation_name.casefold() not in {"", "regular"}
                    else item_name
                )
                image_url = next(
                    (images.get(image_id) for image_id in item_data.get("image_ids", []) if images.get(image_id)),
                    None,
                )
                grouped[category_id].append(
                    MenuItem(
                        id=variation["id"],
                        name=name,
                        category=category_id,
                        category_label=category_name,
                        capacity_category=(
                            "pizza" if category_name in self.pizza_category_names else None
                        ),
                        price_cents=price_cents,
                        description=item_data.get("description_plaintext")
                        or item_data.get("description")
                        or "",
                        additions=additions,
                        modifier_groups=modifier_groups,
                        available=not _sold_out(variation_data, self.location_id),
                        image_url=image_url,
                        catalog_object_id=variation["id"],
                        catalog_version=variation.get("version"),
                    )
                )

        groups = []
        all_items: list[MenuItem] = []
        for category_id, (_, label) in sorted(allowed.items(), key=lambda pair: pair[1][0]):
            items = tuple(grouped[category_id])
            groups.append({"id": category_id, "label": label, "items": items})
            all_items.extend(items)
        return MenuSnapshot(
            groups=tuple(groups),
            items=tuple(all_items),
            capacity_object_ids=frozenset(capacity_object_ids),
        )

    def _modifiers(
        self, infos: list[dict], modifier_lists: dict[str, dict]
    ) -> tuple[tuple[Addition, ...], tuple[ModifierGroup, ...]]:
        addition_lists: dict[str, list[ModifierOption]] = {}
        groups: list[ModifierGroup] = []
        for info in infos:
            if info.get("enabled") is False:
                continue
            modifier_list = modifier_lists.get(info.get("modifier_list_id"))
            if not modifier_list:
                continue
            list_data = modifier_list.get("modifier_list_data", {})
            list_name = list_data.get("name", "Options").strip()
            if _normalized_name(list_name) in self.excluded_modifier_list_names:
                continue
            placement = _placement_for_list_name(list_name)
            overrides = {
                override.get("modifier_id"): override
                for override in info.get("modifier_overrides", [])
            }
            options = []
            for modifier in list_data.get("modifiers", []):
                if not _present_at_location(modifier, self.location_id):
                    continue
                modifier_data = modifier.get("modifier_data", {})
                if modifier_data.get("hidden_online"):
                    continue
                if _sold_out(modifier_data, self.location_id):
                    continue
                price = _price_cents(modifier_data, self.location_id)
                if price is None:
                    price = 0
                override = overrides.get(modifier.get("id"), {})
                option = ModifierOption(
                    id=modifier["id"],
                    name=modifier_data.get("name", "Option"),
                    price_cents=price,
                    catalog_version=modifier.get("version"),
                    on_by_default=override.get(
                        "on_by_default", modifier_data.get("on_by_default", False)
                    ),
                )
                if placement:
                    addition_lists.setdefault(placement, []).append(option)
                else:
                    options.append(option)

            if not placement and options:
                selection_type = list_data.get("selection_type", "MULTIPLE")
                min_selected, max_selected = _selection_limits(
                    info, list_data, selection_type
                )
                groups.append(
                    ModifierGroup(
                        id=modifier_list["id"],
                        name=list_name,
                        selection_type=selection_type,
                        min_selected=min_selected,
                        max_selected=max_selected,
                        options=tuple(options),
                    )
                )

        # The whole-pie list is the customer-facing source of truth. The half
        # lists provide alternate Square IDs and prices for those same options;
        # they must never create duplicate or half-only rows in the UI.
        half_lists = {
            placement: addition_lists.get(placement, [])
            for placement in ("first_half", "second_half")
        }
        placement_indexes = {
            placement: {
                _canonical_addition_name(option.name): option
                for option in options
            }
            for placement, options in half_lists.items()
        }
        additions = []
        whole_options = addition_lists.get("whole", [])
        for index, whole_option in enumerate(whole_options):
            key = _canonical_addition_name(whole_option.name)
            placements = {"whole": whole_option}
            for placement in ("first_half", "second_half"):
                half_option = placement_indexes[placement].get(key)
                # Square merchants commonly keep the three parallel lists in
                # the same order while adding placement wording to option
                # names. If the names still do not normalize to the same key,
                # aligned lists provide a safe final correspondence.
                placement_options = half_lists[placement]
                if (
                    not half_option
                    and len(placement_options) == len(whole_options)
                    and index < len(placement_options)
                ):
                    half_option = placement_options[index]
                if half_option:
                    placements[placement] = half_option
            additions.append(
                Addition(
                    id=_addition_key(whole_option.name),
                    name=whole_option.name,
                    placements=placements,
                )
            )
        return tuple(additions), tuple(groups)


class CheckoutLockManager:
    """Single-process lease used locally; AWS replaces this with a DynamoDB lease."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def acquire(self, service_at: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(service_at, threading.Lock())
        with lock:
            yield


class SquareCommerce:
    def __init__(
        self, *, client: SquareClient, location_id: str, timezone_name: str
    ) -> None:
        self.client = client
        self.location_id = location_id
        self.timezone = ZoneInfo(timezone_name)

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None

    def pizza_counts_by_slot(
        self, *, variation_ids: set[str], now: datetime
    ) -> dict[str, int]:
        if not variation_ids:
            return {}
        orders = self.client.search_orders(
            location_id=self.location_id,
            created_after=now - timedelta(days=31),
        )
        counts: dict[str, int] = {}
        for order in orders:
            pickup_at = None
            for fulfillment in order.get("fulfillments", []):
                if fulfillment.get("type") == "PICKUP":
                    pickup_at = fulfillment.get("pickup_details", {}).get("pickup_at")
                    if pickup_at:
                        break
            parsed = self._parse_datetime(pickup_at) if pickup_at else None
            if not parsed:
                continue
            slot = parsed.astimezone(self.timezone).replace(
                second=0, microsecond=0
            ).isoformat()
            pizzas = 0
            for line in order.get("line_items", []):
                if line.get("catalog_object_id") in variation_ids:
                    try:
                        pizzas += int(Decimal(str(line.get("quantity", "0"))))
                    except (ValueError, ArithmeticError):
                        continue
            counts[slot] = counts.get(slot, 0) + pizzas
        return counts

    @staticmethod
    def _order_line_items(lines: list[dict], items_by_id: dict[str, MenuItem]) -> list[dict]:
        order_lines = []
        for line in lines:
            item = items_by_id[line["item_id"]]
            if not item.catalog_object_id:
                raise SquareConfigurationError(
                    f"{item.name} is missing its Square variation ID."
                )
            modifiers = []
            for modifier in line.get("modifiers", []):
                catalog_id = modifier.get("catalog_object_id")
                if not catalog_id:
                    raise SquareConfigurationError(
                        f"A modifier on {item.name} is missing its Square catalog ID."
                    )
                modifiers.append(
                    {"catalog_object_id": catalog_id, "quantity": "1"}
                )
            for _ in range(int(line["quantity"])):
                order_line = {
                    "catalog_object_id": item.catalog_object_id,
                    "quantity": "1",
                }
                if modifiers:
                    order_line["modifiers"] = modifiers
                order_lines.append(order_line)
        return order_lines

    def order_payload(
        self,
        *,
        lines: list[dict],
        items_by_id: dict[str, MenuItem],
        service_at: datetime | None = None,
        customer: dict | None = None,
        notes: str = "",
        reference_id: str | None = None,
    ) -> dict:
        order = {
            "location_id": self.location_id,
            "line_items": self._order_line_items(lines, items_by_id),
            "pricing_options": {
                "auto_apply_taxes": True,
                "auto_apply_discounts": True,
            },
        }
        if reference_id:
            order["reference_id"] = reference_id[:40]
        if service_at and customer:
            recipient = {
                "display_name": customer["name"],
                "email_address": customer["email"],
            }
            if customer.get("phone"):
                recipient["phone_number"] = customer["phone"]
            pickup_details = {
                "recipient": recipient,
                "schedule_type": "SCHEDULED",
                "pickup_at": service_at.isoformat(),
            }
            if notes:
                pickup_details["note"] = notes
            order["fulfillments"] = [
                {
                    "type": "PICKUP",
                    "state": "PROPOSED",
                    "pickup_details": pickup_details,
                }
            ]
        return order

    def quote(self, *, lines: list[dict], items_by_id: dict[str, MenuItem]) -> dict:
        order = self.client.calculate_order(
            self.order_payload(lines=lines, items_by_id=items_by_id)
        )
        order_total = int(order.get("total_money", {}).get("amount", 0))
        tax = int(order.get("total_tax_money", {}).get("amount", 0))
        discount = int(order.get("total_discount_money", {}).get("amount", 0))
        return {
            "subtotal_cents": order_total - tax + discount,
            "tip_basis_cents": order_total - tax,
            "tax_cents": tax,
            "discount_cents": discount,
            "order_total_cents": order_total,
        }

    def place_order(
        self,
        *,
        source_id: str,
        attempt_id: str,
        lines: list[dict],
        items_by_id: dict[str, MenuItem],
        service_at: datetime,
        customer: dict,
        notes: str,
        tip_cents: int,
        expected_order_total_cents: int,
    ) -> dict:
        reference = attempt_id.replace("-", "")[:40]
        order = self.client.create_order(
            self.order_payload(
                lines=lines,
                items_by_id=items_by_id,
                service_at=service_at,
                customer=customer,
                notes=notes,
                reference_id=reference,
            ),
            f"order-{attempt_id}",
        )
        order_amount = int(order["total_money"]["amount"])
        if order_amount != expected_order_total_cents:
            try:
                self.client.cancel_order(order, f"changed-{attempt_id}")
            except SquareAPIError:
                pass
            raise SquareAPIError(
                "Your total changed before payment. Review the new total and try again."
            )
        payment_request = {
            "source_id": source_id,
            "idempotency_key": f"payment-{attempt_id}",
            "amount_money": {"amount": order_amount, "currency": "USD"},
            "order_id": order["id"],
            "location_id": self.location_id,
            "autocomplete": True,
            "buyer_email_address": customer["email"],
            "reference_id": reference,
        }
        if tip_cents:
            payment_request["tip_money"] = {
                "amount": tip_cents,
                "currency": "USD",
            }
        try:
            payment = self.client.create_payment(payment_request)
        except SquareAPIError as exc:
            if not exc.ambiguous:
                try:
                    self.client.cancel_order(order, f"cancel-{attempt_id}")
                except SquareAPIError:
                    pass
            raise
        return {"order": order, "payment": payment}


def new_attempt_id() -> str:
    return str(uuid.uuid4())
