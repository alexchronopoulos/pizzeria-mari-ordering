from __future__ import annotations

import json

import pytest

from app.scheduling import parse_pickup_schedule
from scripts.generate_pickup_schedule import (
    ScheduleInputError,
    distribute_capacity,
    generate_schedule,
    main,
)


def test_capacity_alternates_lighter_and_heavier_periods_when_possible():
    capacities = distribute_capacity(
        online_capacity=15,
        slot_count=6,
        minimum=2,
        maximum=3,
    )

    assert capacities == (2, 3, 2, 3, 2, 3)


def test_generator_produces_ready_to_parse_pickup_schedule_json():
    generated = generate_schedule(
        day="thursday",
        start="16:00",
        end="17:15",
        dough_balls=15,
        walk_in_reserve=0,
        slice_pie_reserve=0,
        minimum=2,
        maximum=3,
    )

    assert generated.capacities == (2, 3, 2, 3, 2, 3)
    assert sum(generated.capacities) == 15
    assert generated.windows == (
        {"start": "16:00", "end": "16:00", "pizzas": 2},
        {"start": "16:15", "end": "16:15", "pizzas": 3},
        {"start": "16:30", "end": "16:30", "pizzas": 2},
        {"start": "16:45", "end": "16:45", "pizzas": 3},
        {"start": "17:00", "end": "17:00", "pizzas": 2},
        {"start": "17:15", "end": "17:15", "pizzas": 3},
    )
    encoded = json.dumps(generated.payload(), separators=(",", ":"))
    assert parse_pickup_schedule(encoded, 15)["thursday"] == tuple(
        (window["start"], window["end"], window["pizzas"])
        for window in generated.windows
    )


def test_walk_in_reserve_is_not_published_as_online_capacity():
    generated = generate_schedule(
        day="2026-08-27",
        start="16:00",
        end="17:15",
        dough_balls=18,
        walk_in_reserve=3,
        slice_pie_reserve=0,
        minimum=2,
        maximum=3,
    )

    assert generated.online_capacity == 15
    assert sum(generated.capacities) == 15


def test_slice_pie_reserve_is_not_published_as_online_capacity():
    generated = generate_schedule(
        day="saturday",
        start="11:00",
        end="12:15",
        dough_balls=20,
        walk_in_reserve=2,
        slice_pie_reserve=3,
        minimum=2,
        maximum=3,
    )

    assert generated.online_capacity == 15
    assert sum(generated.capacities) == 15


def test_zero_minimum_can_create_evenly_spaced_closed_periods():
    generated = generate_schedule(
        day="sunday",
        start="11:00",
        end="12:00",
        dough_balls=2,
        walk_in_reserve=0,
        slice_pie_reserve=0,
        minimum=0,
        maximum=1,
    )

    assert generated.capacities == (0, 1, 0, 1, 0)
    assert generated.windows == (
        {"start": "11:15", "end": "11:15", "pizzas": 1},
        {"start": "11:45", "end": "11:45", "pizzas": 1},
    )


def test_adjacent_equal_capacities_are_compacted_into_one_window():
    generated = generate_schedule(
        day="friday",
        start="16:00",
        end="17:00",
        dough_balls=10,
        walk_in_reserve=0,
        slice_pie_reserve=0,
        minimum=2,
        maximum=2,
    )

    assert generated.windows == (
        {"start": "16:00", "end": "17:00", "pizzas": 2},
    )


def test_impossible_total_explains_the_valid_range():
    with pytest.raises(
        ScheduleInputError,
        match="Choose a total between 12 and 18",
    ):
        distribute_capacity(
            online_capacity=20,
            slot_count=6,
            minimum=2,
            maximum=3,
        )


def test_interactive_prompts_combine_multiple_days_into_one_json(
    monkeypatch, capsys
):
    answers = iter(
        (
            "thursday",
            "16:00",
            "17:15",
            "18",
            "3",
            "0",
            "",
            "",
            "none",
            "y",
            "friday",
            "16:00",
            "17:15",
            "20",
            "2",
            "3",
            "2",
            "3",
            "none",
            "n",
        )
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main([]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output.strip().splitlines()[-1])
    assert list(payload) == ["thursday", "friday"]
    assert sum(window["pizzas"] for window in payload["thursday"]) == 15
    assert sum(window["pizzas"] for window in payload["friday"]) == 15
    assert "3 slice pie reserve" in output


def test_extra_capacity_goes_to_peak_times_before_other_times():
    generated = generate_schedule(
        day="friday", start="16:00", end="17:15",
        dough_balls=20, walk_in_reserve=2, slice_pie_reserve=3,
        minimum=2, maximum=3, peak_start="16:45", peak_end="17:15",
    )

    assert generated.capacities == (2, 2, 2, 3, 3, 3)
    assert generated.windows == (
        {"start": "16:00", "end": "16:30", "pizzas": 2},
        {"start": "16:45", "end": "17:15", "pizzas": 3},
    )
    assert parse_pickup_schedule(json.dumps(generated.payload()), 15)["friday"] == (
        ("16:00", "16:30", 2), ("16:45", "17:15", 3),
    )


@pytest.mark.parametrize("minimum,maximum", [(0, 1), (0, 4), (2, 3), (2, 5), (3, 3)])
@pytest.mark.parametrize("peak_start,peak_end", [
    ("16:00", "16:00"), ("16:15", "16:45"),
    ("17:15", "17:15"), ("16:00", "17:15"),
])
def test_peak_allocation_preserves_totals_limits_and_priority(
    minimum, maximum, peak_start, peak_end
):
    for online_capacity in range(max(1, 6 * minimum), 6 * maximum + 1):
        generated = generate_schedule(
            day="friday", start="16:00", end="17:15",
            dough_balls=online_capacity + 5, walk_in_reserve=2, slice_pie_reserve=3,
            minimum=minimum, maximum=maximum,
            peak_start=peak_start, peak_end=peak_end,
        )
        peak = [capacity for value, capacity in zip(generated.times, generated.capacities)
                if peak_start <= value <= peak_end]
        other = [capacity for value, capacity in zip(generated.times, generated.capacities)
                 if not peak_start <= value <= peak_end]
        assert sum(generated.capacities) == online_capacity
        assert all(minimum <= value <= maximum for value in generated.capacities)
        assert max(peak) - min(peak) <= 1
        if other:
            assert max(other) - min(other) <= 1
            assert min(peak) == maximum or max(other) == minimum
        else:
            assert generated.capacities == distribute_capacity(
                online_capacity, 6, minimum, maximum
            )


@pytest.mark.parametrize("peak_start,peak_end,message", [
    ("16:30", None, "supplied together"),
    (None, "17:00", "supplied together"),
    ("17:00", "16:30", "before start time"),
    ("15:45", "17:00", "within the service window"),
    ("17:00", "17:30", "within the service window"),
    ("16:05", "17:00", "15-minute interval"),
    ("bad", "17:00", "HH:MM"),
    ("16:00+01:00", "17:00", "local 24-hour"),
])
def test_invalid_peak_range_has_a_clear_error(peak_start, peak_end, message):
    with pytest.raises(ScheduleInputError, match=message):
        generate_schedule(
            day="friday", start="16:00", end="17:15",
            dough_balls=15, walk_in_reserve=0, slice_pie_reserve=0,
            minimum=2, maximum=3, peak_start=peak_start, peak_end=peak_end,
        )


def _cli_args(*extra, start="16:00", end="20:00", dough_balls="40"):
    return [
        "--day", "friday", "--start", start, "--end", end,
        "--dough-balls", dough_balls, "--walk-in-reserve", "0",
        "--slice-pie-reserve", "0", "--min-per-slot", "2", "--max-per-slot", "3",
        *extra,
    ]


def test_cli_defaults_to_17_to_19_peak_without_prompting(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected prompt"))
    assert main(_cli_args()) == 0
    output = capsys.readouterr().out
    assert "Peak pickup range: 5:00 PM–7:00 PM (inclusive)" in output
    payload = json.loads(output.strip().splitlines()[-1])
    windows = parse_pickup_schedule(json.dumps(payload), 15)["friday"]
    assert windows[0] == ("16:00", "16:45", 2)
    assert windows[-1] == ("19:15", "20:00", 2)
    assert sum(
        (int(end[:2]) * 60 + int(end[3:]) - int(start[:2]) * 60 - int(start[3:]))
        // 15 * capacity + capacity
        for start, end, capacity in windows
    ) == 40


@pytest.mark.parametrize("extra", [(), ("--no-peak",)])
def test_cli_does_not_apply_default_peak_outside_service(extra, capsys):
    assert main(_cli_args(*extra, start="11:00", end="12:15", dough_balls="15")) == 0
    output = capsys.readouterr().out
    assert "Peak pickup range" not in output
    payload = json.loads(output.strip().splitlines()[-1])
    assert [window["pizzas"] for window in payload["friday"]] == [2, 3, 2, 3, 2, 3]


def test_no_peak_flag_keeps_even_distribution_during_default_peak(capsys):
    assert main(_cli_args("--no-peak")) == 0
    output = capsys.readouterr().out
    assert "Peak pickup range" not in output
    payload = json.loads(output.strip().splitlines()[-1])
    expected = generate_schedule(
        day="friday", start="16:00", end="20:00", dough_balls=40,
        walk_in_reserve=0, slice_pie_reserve=0, minimum=2, maximum=3,
    )
    assert payload == expected.payload()


def test_cli_custom_peak_and_json_only(capsys):
    assert main(_cli_args(
        "--peak-start", "16:45", "--peak-end", "17:15", "--json-only",
        end="17:15", dough_balls="15",
    )) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["friday"] == [
        {"start": "16:00", "end": "16:30", "pizzas": 2},
        {"start": "16:45", "end": "17:15", "pizzas": 3},
    ]


@pytest.mark.parametrize("extra,message", [
    (("--peak-start", "17:00"), "supplied together"),
    (("--peak-end", "19:00"), "supplied together"),
    (("--no-peak", "--peak-start", "17:00"), "cannot be combined"),
])
def test_cli_reports_incomplete_or_conflicting_peak_flags(extra, message, capsys):
    assert main(_cli_args(*extra)) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("peak_answers,expected_start,expected_end", [
    (("", ""), "17:00", "19:00"),
    (("18:00", "19:30"), "18:00", "19:30"),
    (("none",), None, None),
])
def test_interactive_peak_defaults_overrides_and_skip(
    peak_answers, expected_start, expected_end, monkeypatch, capsys
):
    answers = iter(("friday", "", "", "40", "0", "0", "", "", *peak_answers, "n"))
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert main([]) == 0
    assert any("[17:00]" in prompt for prompt in prompts)
    output = capsys.readouterr().out
    expected = generate_schedule(
        day="friday", start="16:00", end="20:00", dough_balls=40,
        walk_in_reserve=0, slice_pie_reserve=0, minimum=2, maximum=3,
        peak_start=expected_start, peak_end=expected_end,
    )
    assert json.loads(output.strip().splitlines()[-1]) == expected.payload()
