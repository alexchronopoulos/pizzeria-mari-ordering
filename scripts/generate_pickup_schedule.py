#!/usr/bin/env python3
"""Generate a balanced PICKUP_SCHEDULE value for one service day."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import floor


INTERVAL_MINUTES = 15
WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
DEFAULT_HOURS = {
    "thursday": ("16:00", "20:00"),
    "friday": ("16:00", "20:00"),
    "saturday": ("11:00", "20:00"),
    "sunday": ("11:00", "16:00"),
}


class ScheduleInputError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedSchedule:
    key: str
    times: tuple[str, ...]
    capacities: tuple[int, ...]
    windows: tuple[dict[str, object], ...]
    total_dough_balls: int
    walk_in_reserve: int
    slice_pie_reserve: int

    @property
    def online_capacity(self) -> int:
        return (
            self.total_dough_balls
            - self.walk_in_reserve
            - self.slice_pie_reserve
        )

    def payload(self) -> dict[str, list[dict[str, object]]]:
        return {self.key: list(self.windows)}


def normalize_schedule_key(value: str) -> str:
    key = value.strip().lower()
    if key in WEEKDAY_KEYS:
        return key
    try:
        parsed = date.fromisoformat(key)
    except ValueError as exc:
        raise ScheduleInputError(
            "Day must be a weekday name or a date in YYYY-MM-DD format."
        ) from exc
    if parsed.isoformat() != key:
        raise ScheduleInputError("Dates must use YYYY-MM-DD format.")
    return key


def default_hours_for_key(key: str) -> tuple[str, str] | None:
    weekday = key
    if key not in WEEKDAY_KEYS:
        weekday = WEEKDAY_KEYS[date.fromisoformat(key).weekday()]
    return DEFAULT_HOURS.get(weekday)


def parse_slot_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ScheduleInputError("Times must use 24-hour HH:MM format.") from exc
    if (
        parsed.second
        or parsed.microsecond
        or parsed.minute % INTERVAL_MINUTES
    ):
        raise ScheduleInputError(
            f"Times must align to a {INTERVAL_MINUTES}-minute interval."
        )
    return parsed


def service_times(start: str, end: str) -> tuple[str, ...]:
    start_time = parse_slot_time(start)
    end_time = parse_slot_time(end)
    if end_time < start_time:
        raise ScheduleInputError("End time cannot be before start time.")
    cursor = datetime.combine(date.min, start_time)
    last = datetime.combine(date.min, end_time)
    values: list[str] = []
    while cursor <= last:
        values.append(cursor.strftime("%H:%M"))
        cursor += timedelta(minutes=INTERVAL_MINUTES)
    return tuple(values)


def distribute_capacity(
    online_capacity: int,
    slot_count: int,
    minimum: int,
    maximum: int,
) -> tuple[int, ...]:
    if slot_count < 1:
        raise ScheduleInputError("The service window must contain a pickup time.")
    if minimum < 0:
        raise ScheduleInputError("Minimum capacity cannot be negative.")
    if maximum < 1:
        raise ScheduleInputError("Maximum capacity must be at least 1.")
    if maximum < minimum:
        raise ScheduleInputError("Maximum capacity cannot be below the minimum.")

    smallest_total = slot_count * minimum
    largest_total = slot_count * maximum
    if not smallest_total <= online_capacity <= largest_total:
        raise ScheduleInputError(
            f"{online_capacity} online dough balls cannot fit in {slot_count} "
            f"pickup times at {minimum}–{maximum} per time. Choose a total "
            f"between {smallest_total} and {largest_total}, adjust the service "
            "window, or change the minimum/maximum."
        )

    remaining = online_capacity - smallest_total
    full_layers, spaced_remainder = divmod(remaining, slot_count)
    capacities = [minimum + full_layers] * slot_count

    # Place the final heavier periods at evenly spaced midpoints. This starts
    # with a lighter period when possible; for six times and three extras it
    # produces 2, 3, 2, 3, 2, 3.
    if spaced_remainder:
        heavier_indices = {
            min(
                slot_count - 1,
                floor((index + 0.5) * slot_count / spaced_remainder),
            )
            for index in range(spaced_remainder)
        }
        if len(heavier_indices) != spaced_remainder:
            raise RuntimeError("Could not spread pickup capacity evenly.")
        for index in heavier_indices:
            capacities[index] += 1

    if max(capacities) > maximum or sum(capacities) != online_capacity:
        raise RuntimeError("Generated pickup capacity did not satisfy its limits.")
    return tuple(capacities)


def schedule_windows(
    times: tuple[str, ...], capacities: tuple[int, ...]
) -> tuple[dict[str, object], ...]:
    windows: list[dict[str, object]] = []
    for pickup_time, capacity in zip(times, capacities, strict=True):
        if capacity == 0:
            continue
        if (
            windows
            and windows[-1]["pizzas"] == capacity
            and _minutes_between(str(windows[-1]["end"]), pickup_time)
            == INTERVAL_MINUTES
        ):
            windows[-1]["end"] = pickup_time
            continue
        windows.append(
            {"start": pickup_time, "end": pickup_time, "pizzas": capacity}
        )
    return tuple(windows)


def _minutes_between(first: str, second: str) -> int:
    first_time = datetime.combine(date.min, time.fromisoformat(first))
    second_time = datetime.combine(date.min, time.fromisoformat(second))
    return int((second_time - first_time).total_seconds() // 60)


def generate_schedule(
    *,
    day: str,
    start: str,
    end: str,
    dough_balls: int,
    walk_in_reserve: int,
    slice_pie_reserve: int,
    minimum: int,
    maximum: int,
) -> GeneratedSchedule:
    key = normalize_schedule_key(day)
    if dough_balls < 1:
        raise ScheduleInputError("Dough balls must be at least 1.")
    if walk_in_reserve < 0:
        raise ScheduleInputError("Walk-in reserve cannot be negative.")
    if slice_pie_reserve < 0:
        raise ScheduleInputError("Slice Pie Reserve cannot be negative.")
    total_reserve = walk_in_reserve + slice_pie_reserve
    if total_reserve >= dough_balls:
        raise ScheduleInputError(
            "Walk-in Reserve plus Slice Pie Reserve must be smaller than the "
            "total dough balls."
        )
    times = service_times(start, end)
    online_capacity = dough_balls - total_reserve
    capacities = distribute_capacity(
        online_capacity, len(times), minimum, maximum
    )
    return GeneratedSchedule(
        key=key,
        times=times,
        capacities=capacities,
        windows=schedule_windows(times, capacities),
        total_dough_balls=dough_balls,
        walk_in_reserve=walk_in_reserve,
        slice_pie_reserve=slice_pie_reserve,
    )


def _whole_number(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number") from exc
    return parsed


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    raise ScheduleInputError(f"{label} is required.")


def _prompt_integer(label: str, default: int | None = None) -> int:
    return _whole_number(_prompt(label, None if default is None else str(default)))


def _format_time(value: str) -> str:
    return datetime.strptime(value, "%H:%M").strftime("%-I:%M %p")


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Distribute dough-ball capacity across 15-minute pickup times and "
            "generate a ready-to-paste PICKUP_SCHEDULE JSON value."
        )
    )
    parser.add_argument("--day", help="Weekday name or YYYY-MM-DD date")
    parser.add_argument("--start", help="First pickup time in 24-hour HH:MM")
    parser.add_argument("--end", help="Last pickup time in 24-hour HH:MM")
    parser.add_argument("--dough-balls", type=_whole_number)
    parser.add_argument("--walk-in-reserve", type=_whole_number, default=None)
    parser.add_argument("--slice-pie-reserve", type=_whole_number, default=None)
    parser.add_argument("--min-per-slot", type=_whole_number)
    parser.add_argument("--max-per-slot", type=_whole_number)
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only compact JSON, suitable for command substitution.",
    )
    return parser.parse_args(argv)


def _collect_schedule(args: argparse.Namespace | None = None) -> GeneratedSchedule:
    day = args.day if args and args.day else _prompt(
        "Weekday or date (YYYY-MM-DD)"
    )
    key = normalize_schedule_key(day)
    default_hours = default_hours_for_key(key)
    start = args.start if args and args.start else _prompt(
        "First pickup time (HH:MM)",
        default_hours[0] if default_hours else None,
    )
    end = args.end if args and args.end else _prompt(
        "Last pickup time (HH:MM)",
        default_hours[1] if default_hours else None,
    )
    dough_balls = (
        args.dough_balls
        if args and args.dough_balls is not None
        else _prompt_integer("Total dough balls planned")
    )
    walk_in_reserve = (
        args.walk_in_reserve
        if args and args.walk_in_reserve is not None
        else _prompt_integer("Walk-in Reserve", 0)
    )
    slice_pie_reserve = (
        args.slice_pie_reserve
        if args and args.slice_pie_reserve is not None
        else _prompt_integer("Slice Pie Reserve", 0)
    )
    minimum = (
        args.min_per_slot
        if args and args.min_per_slot is not None
        else _prompt_integer("Minimum online pizzas per 15 minutes", 2)
    )
    maximum = (
        args.max_per_slot
        if args and args.max_per_slot is not None
        else _prompt_integer("Maximum online pizzas per 15 minutes", 3)
    )
    return generate_schedule(
        day=key,
        start=start,
        end=end,
        dough_balls=dough_balls,
        walk_in_reserve=walk_in_reserve,
        slice_pie_reserve=slice_pie_reserve,
        minimum=minimum,
        maximum=maximum,
    )


def _prompt_for_another_day() -> bool:
    while True:
        answer = input("Add another day? [y/N]: ").strip().casefold()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("Enter y or n.", file=sys.stderr)


def combined_payload(
    schedules: list[GeneratedSchedule],
) -> dict[str, list[dict[str, object]]]:
    return {
        generated.key: list(generated.windows)
        for generated in schedules
    }


def _print_plan(generated: GeneratedSchedule) -> None:
    print(f"Pickup capacity plan — {generated.key}")
    print("--------------------")
    for pickup_time, capacity in zip(
        generated.times, generated.capacities, strict=True
    ):
        label = "closed" if capacity == 0 else str(capacity)
        print(f"{_format_time(pickup_time):>8}  {label}")
    print()
    print(
        f"Online capacity: {generated.online_capacity} "
        f"({generated.total_dough_balls} total dough balls - "
        f"{generated.walk_in_reserve} walk-in reserve - "
        f"{generated.slice_pie_reserve} slice pie reserve)"
    )


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    interactive = not args.day
    schedules_by_key: dict[str, GeneratedSchedule] = {}
    try:
        while True:
            generated = _collect_schedule(args if not schedules_by_key else None)
            if generated.key in schedules_by_key:
                print(
                    f"Replacing the earlier {generated.key} schedule.",
                    file=sys.stderr,
                )
            schedules_by_key[generated.key] = generated
            if not interactive or not _prompt_for_another_day():
                break
    except (ScheduleInputError, argparse.ArgumentTypeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    schedules = list(schedules_by_key.values())
    compact_json = json.dumps(
        combined_payload(schedules), separators=(",", ":")
    )
    if args.json_only:
        print(compact_json)
        return 0

    if interactive:
        print()
    for index, generated in enumerate(schedules):
        if index:
            print()
        _print_plan(generated)
    print()
    print("PICKUP_SCHEDULE value:")
    print(compact_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
