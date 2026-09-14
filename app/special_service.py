from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from datetime import date, datetime

from .menu import MenuItem


ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _setting_values(value: object, name: str) -> tuple[object, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Iterable):
        return tuple(value)
    raise RuntimeError(f"{name} must be a comma-separated list.")


def parse_special_service_dates(value: object) -> frozenset[date]:
    dates: set[date] = set()
    for raw_date in _setting_values(value, "SPECIAL_SERVICE_DATES"):
        if isinstance(raw_date, datetime):
            parsed = raw_date.date()
        elif isinstance(raw_date, date):
            parsed = raw_date
        else:
            text = str(raw_date).strip()
            if not ISO_DATE_PATTERN.fullmatch(text):
                raise RuntimeError(
                    "SPECIAL_SERVICE_DATES must contain dates in YYYY-MM-DD format."
                )
            try:
                parsed = date.fromisoformat(text)
            except ValueError as exc:
                raise RuntimeError(
                    "SPECIAL_SERVICE_DATES must contain valid calendar dates in "
                    "YYYY-MM-DD format."
                ) from exc
        dates.add(parsed)
    return frozenset(dates)


def parse_special_service_categories(value: object) -> tuple[str, ...]:
    categories = []
    for raw_category in _setting_values(value, "SPECIAL_SERVICE_CATEGORIES"):
        category = str(raw_category).strip()
        if category and category not in categories:
            categories.append(category)
    return tuple(categories)


def format_special_service_categories(categories: Collection[str]) -> str:
    names = list(categories)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return f"{', '.join(names[:-1])}, or {names[-1]}"


def service_date(service_at: object) -> date | None:
    if isinstance(service_at, datetime):
        return service_at.date()
    if not isinstance(service_at, str):
        return None
    try:
        return datetime.fromisoformat(service_at).date()
    except ValueError:
        return None


def item_special_service_error(
    item: MenuItem,
    pickup_date: date | None,
    special_dates: Collection[date],
    special_categories: Collection[str],
) -> str | None:
    if (
        pickup_date is None
        or pickup_date not in special_dates
        or item.category_label in special_categories
    ):
        return None
    categories = format_special_service_categories(special_categories)
    pickup_label = pickup_date.strftime("%A, %B %-d")
    return (
        f"Only items from {categories} are available for pickup on {pickup_label}. "
        f"Change your pickup day to add {item.name} to your order."
    )


def cart_special_service_error(
    lines: list[dict],
    items_by_id: dict[str, MenuItem],
    pickup_date: date | None,
    special_dates: Collection[date],
    special_categories: Collection[str],
) -> str | None:
    for line in lines:
        item = items_by_id.get(line.get("item_id"))
        if item is None:
            continue
        error = item_special_service_error(
            item,
            pickup_date,
            special_dates,
            special_categories,
        )
        if error:
            return error
    return None
