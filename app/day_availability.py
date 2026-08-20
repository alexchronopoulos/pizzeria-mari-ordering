from __future__ import annotations

from datetime import datetime

from .menu import MenuItem, format_available_days


def service_weekday(service_at: object) -> int | None:
    if not isinstance(service_at, str):
        return None
    try:
        return datetime.fromisoformat(service_at).weekday()
    except ValueError:
        return None


def item_day_availability_error(
    item: MenuItem,
    weekday: int | None,
) -> str | None:
    if not item.days_available or weekday in item.days_available:
        return None
    allowed = format_available_days(item.days_available)
    return (
        f"{item.name} can only be ordered for pickup on {allowed}. "
        "Change your pickup day to add it to your order."
    )


def cart_day_availability_error(
    lines: list[dict],
    items_by_id: dict[str, MenuItem],
    weekday: int | None,
) -> str | None:
    for line in lines:
        item = items_by_id.get(line.get("item_id"))
        if item is None:
            continue
        error = item_day_availability_error(item, weekday)
        if error:
            return error
    return None
