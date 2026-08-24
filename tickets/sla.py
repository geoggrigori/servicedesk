"""Business-hours arithmetic used to compute and check SLA deadlines.

Everything here is pure: give it datetimes and a calendar and it returns
datetimes. That keeps the rules testable without touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

#: Safety net so a misconfigured calendar cannot spin forever.
MAX_DAYS_SCANNED = 3650


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


@dataclass(frozen=True)
class BusinessCalendar:
    """Working days and hours, plus a set of dates that are days off."""

    workdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    start: time = time(9, 0)
    end: time = time(18, 0)
    holidays: frozenset[date] = field(default_factory=frozenset)
    timezone: str = "UTC"

    def __post_init__(self):
        if self.start >= self.end:
            raise ValueError("business day must start before it ends")
        if not self.workdays:
            raise ValueError("calendar needs at least one working day")

    @classmethod
    def from_settings(cls) -> BusinessCalendar:
        config = settings.BUSINESS_HOURS
        return cls(
            workdays=frozenset(int(day) for day in config["WORKDAYS"]),
            start=_parse_time(config["START"]),
            end=_parse_time(config["END"]),
            holidays=frozenset(_parse_date(day) for day in config["HOLIDAYS"]),
            timezone=settings.TIME_ZONE,
        )

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def minutes_per_day(self) -> int:
        opening = datetime.combine(date(2000, 1, 1), self.start)
        closing = datetime.combine(date(2000, 1, 1), self.end)
        return int((closing - opening).total_seconds() // 60)

    def is_workday(self, day: date) -> bool:
        return day.weekday() in self.workdays and day not in self.holidays

    def opens_at(self, day: date) -> datetime:
        return datetime.combine(day, self.start, tzinfo=self.tzinfo)

    def closes_at(self, day: date) -> datetime:
        return datetime.combine(day, self.end, tzinfo=self.tzinfo)

    def next_workday(self, day: date) -> date:
        cursor = day + timedelta(days=1)
        for _ in range(MAX_DAYS_SCANNED):
            if self.is_workday(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise ValueError("no working day found within the scan window")


def add_business_minutes(start: datetime, minutes: int, calendar: BusinessCalendar) -> datetime:
    """Return the moment `minutes` of working time after `start`.

    A start outside working hours is first pulled forward to the next time
    the desk is open, so a ticket filed at 3am is not already half late by
    the time anyone can look at it.
    """
    if minutes < 0:
        raise ValueError("minutes must not be negative")

    cursor = start.astimezone(calendar.tzinfo)
    remaining = minutes

    for _ in range(MAX_DAYS_SCANNED):
        day = cursor.date()
        if not calendar.is_workday(day):
            cursor = calendar.opens_at(calendar.next_workday(day))
            continue

        opens, closes = calendar.opens_at(day), calendar.closes_at(day)
        if cursor < opens:
            cursor = opens
        if cursor >= closes:
            cursor = calendar.opens_at(calendar.next_workday(day))
            continue

        if remaining == 0:
            return cursor

        available = int((closes - cursor).total_seconds() // 60)
        if available >= remaining:
            return cursor + timedelta(minutes=remaining)

        remaining -= available
        cursor = calendar.opens_at(calendar.next_workday(day))

    raise ValueError("deadline did not land within the scan window")


def business_minutes_between(start: datetime, end: datetime, calendar: BusinessCalendar) -> int:
    """Count working minutes in the interval, ignoring nights and days off."""
    if end <= start:
        return 0

    start = start.astimezone(calendar.tzinfo)
    end = end.astimezone(calendar.tzinfo)

    total = 0
    day = start.date()
    for _ in range(MAX_DAYS_SCANNED):
        if day > end.date():
            break
        if calendar.is_workday(day):
            window_start = max(start, calendar.opens_at(day))
            window_end = min(end, calendar.closes_at(day))
            if window_end > window_start:
                total += int((window_end - window_start).total_seconds() // 60)
        day += timedelta(days=1)
    else:
        raise ValueError("interval is longer than the scan window")

    return total


def deadline_from(
    start: datetime, minutes: int, calendar: BusinessCalendar, business_hours_only: bool
) -> datetime:
    """Pick between the working-hours clock and plain wall clock time."""
    if business_hours_only:
        return add_business_minutes(start, minutes, calendar)
    return start + timedelta(minutes=minutes)


def elapsed_from(
    start: datetime, end: datetime, calendar: BusinessCalendar, business_hours_only: bool
) -> int:
    if business_hours_only:
        return business_minutes_between(start, end, calendar)
    return max(0, int((end - start).total_seconds() // 60))
