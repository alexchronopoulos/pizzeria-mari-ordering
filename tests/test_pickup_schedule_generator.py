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
            "y",
            "friday",
            "16:00",
            "17:15",
            "20",
            "2",
            "3",
            "2",
            "3",
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
