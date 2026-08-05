from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.scheduling import service_dates, slots_for_date


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
