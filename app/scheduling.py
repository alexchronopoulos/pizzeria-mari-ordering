from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True)
class PickupSlot:
    at: datetime
    capacity: int


PickupWindow = tuple[str, str, int]
PickupSchedule = dict[str, tuple[PickupWindow, ...]]
PICKUP_LEAD_TIME = timedelta(minutes=15)


def _schedule_time(value: object, field: str, interval_minutes: int) -> time:
    if not isinstance(value, str):
        raise RuntimeError(f"PICKUP_SCHEDULE {field} must use HH:MM time.")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(
            f"PICKUP_SCHEDULE {field} must use HH:MM time."
        ) from exc
    if parsed.second or parsed.microsecond or parsed.minute % interval_minutes:
        raise RuntimeError(
            f"PICKUP_SCHEDULE {field} must align to the "
            f"{interval_minutes}-minute pickup interval."
        )
    return parsed


def _schedule_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            "PICKUP_SCHEDULE keys must be weekday names or YYYY-MM-DD dates."
        )
    key = value.strip().lower()
    if key in WEEKDAY_KEYS:
        return key
    try:
        parsed = date.fromisoformat(key)
    except ValueError as exc:
        raise RuntimeError(
            "PICKUP_SCHEDULE keys must be weekday names or YYYY-MM-DD dates."
        ) from exc
    if parsed.isoformat() != key:
        raise RuntimeError(
            "PICKUP_SCHEDULE date keys must use YYYY-MM-DD format."
        )
    return key


def parse_pickup_schedule(
    value: object,
    interval_minutes: int,
) -> PickupSchedule:
    """Parse optional weekday/date pickup-window overrides from JSON."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError("PICKUP_SCHEDULE must be valid JSON.") from exc
    else:
        decoded = value
    if not isinstance(decoded, Mapping):
        raise RuntimeError("PICKUP_SCHEDULE must be a JSON object.")

    schedule: PickupSchedule = {}
    for raw_key, raw_windows in decoded.items():
        key = _schedule_key(raw_key)
        if not isinstance(raw_windows, list):
            raise RuntimeError(
                f"PICKUP_SCHEDULE {key} must be a list of pickup windows."
            )

        windows: list[PickupWindow] = []
        occupied: set[time] = set()
        for index, raw_window in enumerate(raw_windows, start=1):
            label = f"{key} window {index}"
            if not isinstance(raw_window, Mapping):
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} must be an object."
                )
            expected = {"start", "end", "pizzas"}
            if set(raw_window) != expected:
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} must contain only start, end, and pizzas."
                )

            start = _schedule_time(raw_window["start"], f"{label} start", interval_minutes)
            end = _schedule_time(raw_window["end"], f"{label} end", interval_minutes)
            if end < start:
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} end must not be before start."
                )
            pizzas = raw_window["pizzas"]
            if isinstance(pizzas, bool):
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} pizzas must be a positive whole number."
                )
            try:
                pizzas = int(pizzas)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} pizzas must be a positive whole number."
                ) from exc
            if pizzas < 1 or isinstance(raw_window["pizzas"], float) and not raw_window[
                "pizzas"
            ].is_integer():
                raise RuntimeError(
                    f"PICKUP_SCHEDULE {label} pizzas must be a positive whole number."
                )

            cursor = datetime.combine(date.min, start)
            last = datetime.combine(date.min, end)
            while cursor <= last:
                if cursor.time() in occupied:
                    raise RuntimeError(
                        f"PICKUP_SCHEDULE {key} pickup windows cannot overlap."
                    )
                occupied.add(cursor.time())
                cursor += timedelta(minutes=interval_minutes)
            windows.append(
                (
                    start.isoformat(timespec="minutes"),
                    end.isoformat(timespec="minutes"),
                    pizzas,
                )
            )
        schedule[key] = tuple(windows)
    return schedule


def _windows_for_date(
    service_date: date,
    service_hours: dict[int, tuple[str, str]],
    schedule: PickupSchedule,
    default_capacity: int,
) -> tuple[PickupWindow, ...]:
    date_key = service_date.isoformat()
    if date_key in schedule:
        return schedule[date_key]
    weekday_key = WEEKDAY_KEYS[service_date.weekday()]
    if weekday_key in schedule:
        return schedule[weekday_key]
    hours = service_hours.get(service_date.weekday())
    if not hours:
        return ()
    return ((hours[0], hours[1], default_capacity),)


def pickup_service_dates(
    service_hours: dict[int, tuple[str, str]],
    schedule: PickupSchedule,
    default_capacity: int,
    timezone: str,
    advance_days: int,
    now: datetime | None = None,
) -> list[date]:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    return [
        day
        for offset in range(advance_days + 1)
        if _windows_for_date(
            day := local_now.date() + timedelta(days=offset),
            service_hours,
            schedule,
            default_capacity,
        )
    ]


def pickup_slots_for_date(
    service_date: date,
    service_hours: dict[int, tuple[str, str]],
    schedule: PickupSchedule,
    default_capacity: int,
    timezone: str,
    interval_minutes: int,
    now: datetime | None = None,
) -> list[PickupSlot]:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    first_available_at = local_now + PICKUP_LEAD_TIME
    slots: list[PickupSlot] = []
    for start_value, end_value, capacity in _windows_for_date(
        service_date, service_hours, schedule, default_capacity
    ):
        start = datetime.combine(service_date, time.fromisoformat(start_value), zone)
        end = datetime.combine(service_date, time.fromisoformat(end_value), zone)
        cursor = start
        while cursor <= end:
            if cursor >= first_available_at:
                slots.append(PickupSlot(cursor, capacity))
            cursor += timedelta(minutes=interval_minutes)
    return sorted(slots, key=lambda slot: slot.at)


def pickup_slot_capacity(
    candidate: datetime,
    service_hours: dict[int, tuple[str, str]],
    schedule: PickupSchedule,
    default_capacity: int,
    timezone: str,
    advance_days: int,
    interval_minutes: int,
    now: datetime | None = None,
) -> int | None:
    dates = pickup_service_dates(
        service_hours,
        schedule,
        default_capacity,
        timezone,
        advance_days,
        now,
    )
    if candidate.date() not in dates:
        return None
    for slot in pickup_slots_for_date(
        candidate.date(),
        service_hours,
        schedule,
        default_capacity,
        timezone,
        interval_minutes,
        now,
    ):
        if candidate == slot.at:
            return slot.capacity
    return None


def service_dates(
    service_hours: dict[int, tuple[str, str]],
    timezone: str,
    advance_days: int,
    now: datetime | None = None,
) -> list[date]:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    return [
        day
        for offset in range(advance_days + 1)
        if (day := local_now.date() + timedelta(days=offset)).weekday() in service_hours
    ]


def slots_for_date(
    service_date: date,
    service_hours: dict[int, tuple[str, str]],
    timezone: str,
    interval_minutes: int,
    now: datetime | None = None,
) -> list[datetime]:
    hours = service_hours.get(service_date.weekday())
    if not hours:
        return []

    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    first_available_at = local_now + PICKUP_LEAD_TIME
    start = datetime.combine(service_date, time.fromisoformat(hours[0]), zone)
    end = datetime.combine(service_date, time.fromisoformat(hours[1]), zone)
    slots: list[datetime] = []
    cursor = start
    while cursor <= end:
        if cursor >= first_available_at:
            slots.append(cursor)
        cursor += timedelta(minutes=interval_minutes)
    return slots


def is_valid_slot(
    candidate: datetime,
    service_hours: dict[int, tuple[str, str]],
    timezone: str,
    advance_days: int,
    interval_minutes: int,
    now: datetime | None = None,
) -> bool:
    allowed_dates = service_dates(service_hours, timezone, advance_days, now)
    if candidate.date() not in allowed_dates:
        return False
    return candidate in slots_for_date(
        candidate.date(), service_hours, timezone, interval_minutes, now
    )
