from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.scheduling import (
    parse_pickup_schedule,
    pickup_service_dates,
    pickup_slot_capacity,
    pickup_slots_for_date,
    service_dates,
    slots_for_date,
)


HOURS = {
    3: ("16:00", "20:00"),
    4: ("16:00", "20:00"),
    5: ("11:00", "20:00"),
    6: ("11:00", "16:00"),
}


def test_service_dates_include_open_days_up_to_seven_days_ahead():
    now = datetime(2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    dates = service_dates(HOURS, "America/New_York", 7, now)
    assert dates == [
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 8),
        date(2026, 8, 9),
    ]


def test_thursday_has_fifteen_minute_slots_through_closing_time():
    now = datetime(2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    slots = slots_for_date(date(2026, 8, 6), HOURS, "America/New_York", 15, now)
    assert len(slots) == 17
    assert slots[0].strftime("%H:%M") == "16:00"
    assert slots[-1].strftime("%H:%M") == "20:00"


def test_pickup_schedule_sets_weekday_windows_and_per_slot_capacity():
    now = datetime(2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    schedule = parse_pickup_schedule(
        {
            "thursday": [
                {"start": "16:00", "end": "16:30", "pizzas": 2},
                {"start": "17:00", "end": "17:30", "pizzas": 3},
            ],
            "sunday": [
                {"start": "14:00", "end": "17:00", "pizzas": 2},
            ],
        },
        15,
    )

    thursday = pickup_slots_for_date(
        date(2026, 8, 6), HOURS, schedule, 3, "America/New_York", 15, now
    )
    assert [(slot.at.strftime("%H:%M"), slot.capacity) for slot in thursday] == [
        ("16:00", 2),
        ("16:15", 2),
        ("16:30", 2),
        ("17:00", 3),
        ("17:15", 3),
        ("17:30", 3),
    ]

    sunday = pickup_slots_for_date(
        date(2026, 8, 9), HOURS, schedule, 3, "America/New_York", 15, now
    )
    assert sunday[0].at.strftime("%H:%M") == "14:00"
    assert sunday[-1].at.strftime("%H:%M") == "17:00"
    assert {slot.capacity for slot in sunday} == {2}


def test_date_override_replaces_weekday_schedule_and_can_close_a_date():
    now = datetime(2026, 8, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    schedule = parse_pickup_schedule(
        {
            "thursday": [
                {"start": "16:00", "end": "20:00", "pizzas": 2},
            ],
            "2026-08-06": [
                {"start": "17:00", "end": "17:30", "pizzas": 3},
            ],
            "2026-08-07": [],
        },
        15,
    )

    slots = pickup_slots_for_date(
        date(2026, 8, 6), HOURS, schedule, 3, "America/New_York", 15, now
    )
    assert [(slot.at.strftime("%H:%M"), slot.capacity) for slot in slots] == [
        ("17:00", 3),
        ("17:15", 3),
        ("17:30", 3),
    ]
    dates = pickup_service_dates(
        HOURS, schedule, 3, "America/New_York", 7, now
    )
    assert date(2026, 8, 7) not in dates
    assert pickup_slot_capacity(
        slots[0].at, HOURS, schedule, 3, "America/New_York", 7, 15, now
    ) == 3


@pytest.mark.parametrize(
    "value, message",
    [
        ('{"sunday":', "valid JSON"),
        ({"sunday": {"start": "14:00"}}, "must be a list"),
        (
            {
                "sunday": [
                    {"start": "14:10", "end": "17:00", "pizzas": 2}
                ]
            },
            "15-minute pickup interval",
        ),
        (
            {
                "sunday": [
                    {"start": "14:00", "end": "15:00", "pizzas": 2},
                    {"start": "15:00", "end": "16:00", "pizzas": 3},
                ]
            },
            "cannot overlap",
        ),
    ],
)
def test_invalid_pickup_schedule_is_rejected(value, message):
    with pytest.raises(RuntimeError, match=message):
        parse_pickup_schedule(value, 15)
