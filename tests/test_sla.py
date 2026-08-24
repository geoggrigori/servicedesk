"""The business-hours clock is the part most worth pinning down."""

from datetime import date, time

import pytest

from tickets.sla import (
    BusinessCalendar,
    add_business_minutes,
    business_minutes_between,
)

from .conftest import at


def test_minutes_inside_one_working_day(calendar):
    assert add_business_minutes(at(2026, 3, 3, 10, 0), 90, calendar) == at(2026, 3, 3, 11, 30)


def test_work_rolls_over_to_the_next_morning(calendar):
    # 17:00 Tuesday leaves one hour today, the rest lands on Wednesday.
    assert add_business_minutes(at(2026, 3, 3, 17, 0), 120, calendar) == at(2026, 3, 4, 10, 0)


def test_friday_evening_lands_on_monday(calendar):
    assert add_business_minutes(at(2026, 3, 6, 17, 0), 120, calendar) == at(2026, 3, 9, 10, 0)


def test_a_ticket_filed_overnight_starts_at_opening(calendar):
    assert add_business_minutes(at(2026, 3, 3, 3, 0), 30, calendar) == at(2026, 3, 3, 9, 30)


def test_weekend_start_waits_for_monday(calendar):
    assert add_business_minutes(at(2026, 3, 7, 10, 0), 30, calendar) == at(2026, 3, 9, 9, 30)


def test_holidays_are_skipped():
    calendar = BusinessCalendar(
        holidays=frozenset({date(2026, 3, 4)}), timezone="America/Sao_Paulo"
    )
    assert add_business_minutes(at(2026, 3, 3, 17, 30), 60, calendar) == at(2026, 3, 5, 9, 30)


def test_zero_minutes_still_snaps_into_working_hours(calendar):
    assert add_business_minutes(at(2026, 3, 7, 10, 0), 0, calendar) == at(2026, 3, 9, 9, 0)


def test_negative_minutes_are_rejected(calendar):
    with pytest.raises(ValueError):
        add_business_minutes(at(2026, 3, 3, 10, 0), -1, calendar)


def test_elapsed_ignores_nights_and_weekends(calendar):
    assert business_minutes_between(at(2026, 3, 6, 17, 0), at(2026, 3, 9, 10, 0), calendar) == 120


def test_elapsed_is_zero_when_the_interval_is_inverted(calendar):
    assert business_minutes_between(at(2026, 3, 9, 10, 0), at(2026, 3, 6, 17, 0), calendar) == 0


def test_elapsed_over_a_full_weekend_counts_only_working_days(calendar):
    # Friday 09:00 to Monday 18:00 is one full Friday plus one full Monday.
    assert business_minutes_between(at(2026, 3, 6, 9, 0), at(2026, 3, 9, 18, 0), calendar) == 1080


def test_round_trip_add_then_measure(calendar):
    start = at(2026, 3, 3, 14, 20)
    end = add_business_minutes(start, 1000, calendar)
    assert business_minutes_between(start, end, calendar) == 1000


def test_a_calendar_that_closes_before_it_opens_is_invalid():
    with pytest.raises(ValueError):
        BusinessCalendar(start=time(18, 0), end=time(9, 0))


def test_a_calendar_with_no_working_days_is_invalid():
    with pytest.raises(ValueError):
        BusinessCalendar(workdays=frozenset())
