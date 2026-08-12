from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse
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
CHECKOUT_REFERENCE_PREFIX = "PMOC-"
GIFT_CARD_REFERENCE_PREFIX = "PMGC-"
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
        self.environment = environment
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
    ) -> dict:
        try:
            response = self._client.request(
                method, path, params=params, json=json_body
            )
        except httpx.RequestError as exc:
            raise SquareAPIError(
                "Square could not be reached. Please try again.", ambiguous=True
            ) from exc

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

    def batch_retrieve_inventory_counts(
        self, *, catalog_object_ids: list[str], location_id: str
    ) -> list[dict]:
        if not catalog_object_ids:
            return []
        counts: list[dict] = []
        for start in range(0, len(catalog_object_ids), 1000):
            object_ids = catalog_object_ids[start : start + 1000]
            cursor = None
            while True:
                body = {
                    "catalog_object_ids": object_ids,
                    "location_ids": [location_id],
                    "states": ["IN_STOCK"],
                    "limit": 1000,
                }
                if cursor:
                    body["cursor"] = cursor
                payload = self.request(
                    "POST",
                    "/v2/inventory/counts/batch-retrieve",
                    json_body=body,
                )
                counts.extend(payload.get("counts", []))
                cursor = payload.get("cursor")
                if not cursor:
                    break
        return counts

    def calculate_order(self, order: dict) -> dict:
        payload = self.request(
            "POST", "/v2/orders/calculate", json_body={"order": order}
        )
        return payload["order"]

    def create_payment_link(self, request_body: dict) -> dict:
        payload = self.request(
            "POST",
            "/v2/online-checkout/payment-links",
            json_body=request_body,
        )
        return payload

    def create_order(self, request_body: dict) -> dict:
        return self.request(
            "POST",
            "/v2/orders",
            json_body=request_body,
        )

    def update_order(self, order_id: str, request_body: dict) -> dict:
        return self.request(
            "PUT",
            f"/v2/orders/{order_id}",
            json_body=request_body,
        )

    def create_payment(self, request_body: dict) -> dict:
        return self.request(
            "POST",
            "/v2/payments",
            json_body=request_body,
        )

    def pay_order(self, order_id: str, request_body: dict) -> dict:
        return self.request(
            "POST",
            f"/v2/orders/{order_id}/pay",
            json_body=request_body,
        )

    def retrieve_order(self, order_id: str) -> dict:
        payload = self.request("GET", f"/v2/orders/{order_id}")
        return payload["order"]

    def retrieve_payment(self, payment_id: str) -> dict:
        payload = self.request("GET", f"/v2/payments/{payment_id}")
        return payload["payment"]

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


def _tracks_inventory(data: dict, location_id: str) -> bool:
    override = _location_override(data, location_id)
    return bool(override.get("track_inventory", data.get("track_inventory", False)))


def _inventory_alert(data: dict, location_id: str) -> tuple[str | None, int | None]:
    override = _location_override(data, location_id)
    alert_type = override.get("inventory_alert_type")
    threshold = override.get("inventory_alert_threshold")
    if alert_type is None:
        alert_type = data.get("inventory_alert_type")
    if threshold is None:
        threshold = data.get("inventory_alert_threshold")
    try:
        parsed_threshold = int(threshold) if threshold is not None else None
    except (TypeError, ValueError):
        parsed_threshold = None
    return alert_type, parsed_threshold


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
            objects = self.client.list_catalog()
            tracked_ids = self._tracked_variation_ids(objects)
            inventory_counts = self.client.batch_retrieve_inventory_counts(
                catalog_object_ids=tracked_ids,
                location_id=self.location_id,
            )
            snapshot = self._build(objects, inventory_counts)
            self._cached = snapshot
            self._cached_at = now
            return snapshot

    def cached_snapshot(self) -> MenuSnapshot | None:
        """Return the menu already shown to this process without a network read."""
        return self._cached

    def _tracked_variation_ids(self, objects: list[dict]) -> list[str]:
        category_ids = {
            obj["id"]
            for obj in objects
            if obj.get("type") == "CATEGORY"
            and not obj.get("is_deleted", False)
            and obj.get("category_data", {}).get("name")
            in self.allowed_category_names
        }
        return [
            variation["id"]
            for item_object in objects
            if item_object.get("type") == "ITEM"
            and not item_object.get("is_deleted", False)
            and any(
                category_id in category_ids
                for category_id in _category_ids(item_object.get("item_data", {}))
            )
            for variation in item_object.get("item_data", {}).get("variations", [])
            if variation.get("id")
            and not variation.get("is_deleted", False)
            and _present_at_location(variation, self.location_id)
            and _tracks_inventory(
                variation.get("item_variation_data", {}), self.location_id
            )
        ]

    def _build(
        self, objects: list[dict], inventory_counts: list[dict] | None = None
    ) -> MenuSnapshot:
        stock_by_variation: dict[str, int] = {}
        for count in inventory_counts or []:
            if (
                count.get("state") != "IN_STOCK"
                or count.get("location_id") != self.location_id
                or not count.get("catalog_object_id")
            ):
                continue
            try:
                quantity = int(Decimal(str(count.get("quantity", "0"))))
            except (ArithmeticError, ValueError):
                continue
            stock_by_variation[count["catalog_object_id"]] = max(0, quantity)
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
                tracks_inventory = _tracks_inventory(
                    variation_data, self.location_id
                )
                stock_quantity = (
                    stock_by_variation.get(variation["id"])
                    if tracks_inventory
                    else None
                )
                alert_type, alert_threshold = _inventory_alert(
                    variation_data, self.location_id
                )
                sold_out = _sold_out(variation_data, self.location_id)
                if stock_quantity == 0:
                    sold_out = True
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
                        available=not sold_out,
                        stock_quantity=stock_quantity,
                        low_stock=bool(
                            not sold_out
                            and alert_type == "LOW_QUANTITY"
                            and alert_threshold is not None
                            and stock_quantity is not None
                            and stock_quantity <= alert_threshold
                        ),
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


class SquareCommerce:
    def __init__(
        self,
        *,
        client: SquareClient,
        location_id: str,
        timezone_name: str,
        availability_cache_seconds: float = 5.0,
    ) -> None:
        self.client = client
        self.location_id = location_id
        self.timezone = ZoneInfo(timezone_name)
        self.availability_cache_seconds = max(0.0, availability_cache_seconds)
        self._pizza_counts_cache: dict[str, int] | None = None
        self._pizza_counts_cache_ids: frozenset[str] = frozenset()
        self._pizza_counts_cached_at = 0.0
        self._pizza_counts_lock = threading.Lock()

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
        cache_ids = frozenset(variation_ids)
        checked_at = time.monotonic()
        if (
            self._pizza_counts_cache is not None
            and cache_ids == self._pizza_counts_cache_ids
            and checked_at - self._pizza_counts_cached_at
            < self.availability_cache_seconds
        ):
            return dict(self._pizza_counts_cache)

        with self._pizza_counts_lock:
            checked_at = time.monotonic()
            if (
                self._pizza_counts_cache is not None
                and cache_ids == self._pizza_counts_cache_ids
                and checked_at - self._pizza_counts_cached_at
                < self.availability_cache_seconds
            ):
                return dict(self._pizza_counts_cache)
            counts = self._fetch_pizza_counts(
                variation_ids=variation_ids,
                now=now,
            )
            self._pizza_counts_cache = counts
            self._pizza_counts_cache_ids = cache_ids
            self._pizza_counts_cached_at = time.monotonic()
            return dict(counts)

    def _fetch_pizza_counts(
        self, *, variation_ids: set[str], now: datetime
    ) -> dict[str, int]:
        orders = self.client.search_orders(
            location_id=self.location_id,
            created_after=now - timedelta(days=31),
        )
        counts: dict[str, int] = {}
        for order in orders:
            state = order.get("state")
            if state not in {"OPEN", "COMPLETED"}:
                continue
            if (
                state == "OPEN"
                and str(order.get("reference_id", "")).startswith(
                    GIFT_CARD_REFERENCE_PREFIX
                )
            ):
                # App-created gift-card orders become COMPLETED when PayOrder
                # succeeds. Ignore unfinished orders just as we ignore hosted
                # checkout drafts; only paid orders consume displayed capacity.
                continue
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
        line_items = self._order_line_items(lines, items_by_id)
        if service_at and line_items:
            date_text = service_at.strftime("%A, %B")
            time_text = service_at.strftime("%I:%M %p").lstrip("0")
            line_items[0]["note"] = (
                f"Pickup: {date_text} {service_at.day} at {time_text}"
            )
        order = {
            "location_id": self.location_id,
            "line_items": line_items,
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

    @staticmethod
    def _checkout_url(payload: dict, environment: str) -> str:
        payment_link = payload.get("payment_link", {})
        checkout_url = payment_link.get("url", "")
        parsed = urlparse(checkout_url)
        expected_host = (
            "sandbox.square.link" if environment == "sandbox" else "square.link"
        )
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise SquareAPIError(
                "Square returned an invalid hosted checkout address. Please try again."
            )
        return checkout_url

    def create_checkout(
        self,
        *,
        attempt_id: str,
        lines: list[dict],
        items_by_id: dict[str, MenuItem],
        service_at: datetime,
        customer: dict,
        notes: str,
        redirect_url: str,
    ) -> dict:
        reference = f"{CHECKOUT_REFERENCE_PREFIX}{attempt_id.replace('-', '')}"[:40]
        request_body = {
            # Square requires this field. It identifies only this API call and
            # is not retained or reused by the application.
            "idempotency_key": str(uuid.uuid4()),
            "order": self.order_payload(
                lines=lines,
                items_by_id=items_by_id,
                service_at=service_at,
                customer=customer,
                notes=notes,
                reference_id=reference,
            ),
            "checkout_options": {
                "allow_tipping": True,
                "enable_coupon": True,
                "redirect_url": redirect_url,
            },
        }

        payload = self.client.create_payment_link(request_body)
        payment_link = payload.get("payment_link", {})
        if not payment_link.get("id") or not payment_link.get("order_id"):
            raise SquareAPIError(
                "Square did not return a complete hosted checkout. Please try again."
            )
        return {
            "payment_link": payment_link,
            "checkout_url": self._checkout_url(payload, self.client.environment),
        }

    def create_gift_card_checkout(
        self,
        *,
        attempt_id: str,
        lines: list[dict],
        items_by_id: dict[str, MenuItem],
        service_at: datetime,
        customer: dict,
        notes: str,
    ) -> dict:
        reference = (
            f"{GIFT_CARD_REFERENCE_PREFIX}{attempt_id.replace('-', '')}"[:40]
        )
        order = self.order_payload(
            lines=lines,
            items_by_id=items_by_id,
            service_at=service_at,
            customer=customer,
            notes=notes,
            reference_id=reference,
        )
        order["state"] = "DRAFT"
        payload = self.client.create_order(
            {
                "idempotency_key": str(uuid.uuid4()),
                "order": order,
            }
        )
        order = payload.get("order")
        if not order or not order.get("id"):
            raise SquareAPIError(
                "Square did not return a complete gift-card order. Please try again."
            )
        return {"order": order}

    def checkout_result(self, order_id: str) -> dict:
        order = self.client.retrieve_order(order_id)
        if order.get("state") == "CANCELED":
            return {
                "status": "CANCELED",
                "order": order,
                "payment": None,
                "payments": [],
            }
        if order.get("state") == "DRAFT":
            return {
                "status": "PENDING",
                "order": order,
                "payment": None,
                "payments": [],
            }
        payments = self._payments_for_order(order)
        payment = payments[-1] if payments else None
        if order.get("state") == "COMPLETED":
            status = "COMPLETED"
        elif any(item.get("status") == "COMPLETED" for item in payments):
            status = "COMPLETED"
        elif any(item.get("status") == "FAILED" for item in payments):
            status = "FAILED"
        else:
            status = "PENDING"
        return {
            "status": status,
            "order": order,
            "payment": payment,
            "payments": payments,
        }

    @staticmethod
    def _payment_id(tender: dict) -> str | None:
        return tender.get("payment_id") or tender.get("id")

    def _payments_for_order(self, order: dict) -> list[dict]:
        payments = []
        for tender in order.get("tenders", []):
            payment_id = self._payment_id(tender)
            if payment_id:
                payments.append(self.client.retrieve_payment(payment_id))
        return payments

    @staticmethod
    def _payment_amount(payment: dict) -> int:
        return int(payment.get("amount_money", {}).get("amount", 0))

    @staticmethod
    def _is_gift_card_payment(payment: dict) -> bool:
        card = payment.get("card_details", {}).get("card", {})
        return (
            payment.get("source_type") == "GIFT_CARD"
            or card.get("card_brand") == "SQUARE_GIFT_CARD"
        )

    def gift_card_checkout_state(self, order_id: str) -> dict:
        order = self.client.retrieve_order(order_id)
        payments = self._payments_for_order(order)
        authorized = [
            payment
            for payment in payments
            if payment.get("status") in {"APPROVED", "COMPLETED"}
        ]
        total_cents = int(order.get("total_money", {}).get("amount", 0))
        paid_cents = sum(self._payment_amount(payment) for payment in authorized)
        return {
            "status": (
                "CANCELED"
                if order.get("state") == "CANCELED"
                else "COMPLETED"
                if order.get("state") == "COMPLETED"
                else "PENDING"
            ),
            "order": order,
            "payments": payments,
            "payment": payments[-1] if payments else None,
            "payment_ids": [payment["id"] for payment in authorized],
            "total_cents": total_cents,
            "paid_cents": paid_cents,
            "remaining_cents": max(0, total_cents - paid_cents),
            "gift_card_applied": any(
                self._is_gift_card_payment(payment) for payment in authorized
            ),
        }

    def apply_gift_card_payment(
        self,
        *,
        order_id: str,
        attempt_id: str,
        payment_method: str,
        source_id: str,
    ) -> dict:
        state = self.gift_card_checkout_state(order_id)
        order = state["order"]
        expected_reference = (
            f"{GIFT_CARD_REFERENCE_PREFIX}{attempt_id.replace('-', '')}"[:40]
        )
        if order.get("reference_id") != expected_reference:
            raise SquareAPIError("That gift-card checkout is no longer valid.")
        if state["status"] == "CANCELED":
            raise SquareAPIError("That gift-card checkout was canceled.")
        if state["status"] == "COMPLETED":
            return state
        if state["remaining_cents"] == 0:
            raise SquareAPIError(
                "This payment is already awaiting completion in Square."
            )
        if payment_method not in {"gift_card", "card"}:
            raise SquareAPIError("Choose a valid payment method.")
        if payment_method == "gift_card" and state["gift_card_applied"]:
            raise SquareAPIError("A gift card has already been applied to this order.")
        if payment_method == "card" and not state["gift_card_applied"]:
            raise SquareAPIError("Apply a Square gift card before paying the remainder by card.")
        if order.get("state") == "DRAFT":
            version = order.get("version")
            if not isinstance(version, int):
                raise SquareAPIError(
                    "Square did not return a valid gift-card order. Please try again."
                )
            opened_payload = self.client.update_order(
                order_id,
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "order": {
                        # Square validates the location as part of the complete
                        # versioned Order object used for this state change.
                        "location_id": self.location_id,
                        "version": version,
                        "state": "OPEN",
                    },
                },
            )
            opened_order = opened_payload.get("order")
            if not opened_order or opened_order.get("state") != "OPEN":
                raise SquareAPIError(
                    "Square could not open the gift-card order. Please try again."
                )
            state = {**state, "order": opened_order}
            order = opened_order
        request_body = {
            "source_id": source_id,
            "idempotency_key": str(uuid.uuid4()),
            "amount_money": {
                "amount": state["remaining_cents"],
                "currency": "USD",
            },
            "autocomplete": False,
            "order_id": order_id,
            "location_id": self.location_id,
            "note": "Pizzeria Mari online gift-card checkout",
        }
        if payment_method == "gift_card":
            request_body["accept_partial_authorization"] = True

        payload = self.client.create_payment(request_body)
        payment = payload.get("payment")
        if not payment or not payment.get("id"):
            raise SquareAPIError(
                "Square did not return a complete payment. Please try again."
            )
        if payment.get("status") != "APPROVED":
            raise SquareAPIError(
                "Square did not authorize that payment. Please use another payment method."
            )
        if payment_method == "gift_card" and not self._is_gift_card_payment(payment):
            raise SquareAPIError("Enter a valid Pizzeria Mari Square gift card.")

        paid_cents = state["paid_cents"] + self._payment_amount(payment)
        remaining_cents = max(0, state["total_cents"] - paid_cents)
        payment_ids = [*state["payment_ids"], payment["id"]]
        if remaining_cents > 0:
            return {
                **state,
                "status": "PARTIAL",
                "applied_cents": self._payment_amount(payment),
                "paid_cents": paid_cents,
                "remaining_cents": remaining_cents,
                "payment_ids": payment_ids,
            }
        completed = self.complete_gift_card_checkout(
            order_id=order_id,
            attempt_id=attempt_id,
            state={
                **state,
                "paid_cents": paid_cents,
                "remaining_cents": 0,
                "payment_ids": payment_ids,
            },
        )
        completed["payment"] = payment
        return completed

    def complete_gift_card_checkout(
        self,
        *,
        order_id: str,
        attempt_id: str,
        state: dict | None = None,
    ) -> dict:
        state = state or self.gift_card_checkout_state(order_id)
        expected_reference = (
            f"{GIFT_CARD_REFERENCE_PREFIX}{attempt_id.replace('-', '')}"[:40]
        )
        if state["order"].get("reference_id") != expected_reference:
            raise SquareAPIError("That gift-card checkout is no longer valid.")
        if state["status"] == "COMPLETED":
            return state
        if state["status"] == "CANCELED":
            raise SquareAPIError("That gift-card checkout was canceled.")
        if state["remaining_cents"] != 0 or not state["payment_ids"]:
            raise SquareAPIError("This order still has an unpaid balance.")
        pay_body = {
            "idempotency_key": str(uuid.uuid4()),
            "payment_ids": state["payment_ids"],
        }
        paid_payload = self.client.pay_order(order_id, pay_body)
        paid_order = paid_payload.get("order")
        if not paid_order or paid_order.get("state") != "COMPLETED":
            raise SquareAPIError(
                "Square authorized the payments but has not completed the order yet. Please wait a moment and check again."
            )
        return {
            **state,
            "status": "COMPLETED",
            "order": paid_order,
            "remaining_cents": 0,
        }



def new_attempt_id() -> str:
    return str(uuid.uuid4())
