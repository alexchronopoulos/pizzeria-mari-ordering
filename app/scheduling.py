from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


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
    start = datetime.combine(service_date, time.fromisoformat(hours[0]), zone)
    end = datetime.combine(service_date, time.fromisoformat(hours[1]), zone)
    slots: list[datetime] = []
    cursor = start
    while cursor <= end:
        if cursor > local_now:
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
