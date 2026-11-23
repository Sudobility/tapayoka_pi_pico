"""Tests for the app-provided clock sync (SET_TIME support)."""

import datetime
import random

from unittest.mock import MagicMock, patch

from src import clock


def _utc(ts):
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.weekday())


def test_civil_from_unix_matches_datetime_across_range():
    cases = [
        0,
        1,
        946684799,   # just before 2000-01-01 (rp2 epoch boundary)
        946684800,   # 2000-01-01
        1582934400,  # within leap year 2020
        1763700000,  # 2025-11-21
        4102444800,  # 2100-01-01 (non-leap century)
    ]
    cases += [random.randint(0, 4102444800) for _ in range(5000)]
    for ts in cases:
        assert clock._civil_from_unix(ts) == _utc(ts), ts


def test_set_without_machine_is_a_safe_noop():
    # On CPython (the WS dev harness) there is no settable RTC.
    with patch.object(clock, "machine", None):
        assert clock.set_utc_from_unix(1763700000) is False


def test_set_rejects_bad_input_without_raising():
    with patch.object(clock, "machine", None):
        assert clock.set_utc_from_unix(None) is False
        assert clock.set_utc_from_unix("not-a-number") is False
        assert clock.set_utc_from_unix(0) is False
        assert clock.set_utc_from_unix(-5) is False


def test_set_programs_rtc_with_utc_calendar():
    fake_rtc = MagicMock()
    fake_machine = MagicMock()
    fake_machine.RTC.return_value = fake_rtc
    with patch.object(clock, "machine", fake_machine):
        assert clock.set_utc_from_unix(1763700000) is True
    # RTC.datetime((year, month, day, weekday, hours, minutes, seconds, subsec))
    (dt_tuple,) = fake_rtc.datetime.call_args[0]
    assert dt_tuple[:3] == (2025, 11, 21)   # y, m, d
    assert dt_tuple[4:7] == (4, 40, 0)       # h, m, s (UTC)


def test_set_accepts_milliseconds():
    fake_rtc = MagicMock()
    fake_machine = MagicMock()
    fake_machine.RTC.return_value = fake_rtc
    with patch.object(clock, "machine", fake_machine):
        assert clock.set_utc_from_unix(1763700000000) is True
    (dt_tuple,) = fake_rtc.datetime.call_args[0]
    assert dt_tuple[:3] == (2025, 11, 21)
