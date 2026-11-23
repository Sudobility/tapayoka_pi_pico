"""Set the system clock from an app-provided UTC time.

The Pico W has no battery-backed RTC and (on-device) runs BLE-only with no
Wi-Fi/NTP, so its clock sits at the boot epoch. That makes the
``signing_timestamp`` the device stamps into its signed device-info years off
real time, and the backend rejects the proof ("Device signing data mismatch")
because it is outside the freshness window.

The vendor app sends the current time (SET_TIME) right after connecting, before
reading device-info, so the re-signed device-info carries a real timestamp. On
CPython (the WS dev harness) the host clock is already correct, so this is a
best-effort no-op.

The unix -> civil-date conversion is done by hand rather than via
``time.gmtime`` so it is correct regardless of the MicroPython port's time epoch
(rp2 counts from 2000-01-01, the unix port from 1970-01-01); we set the RTC with
an absolute calendar date, which both ports read back consistently.
"""

try:
    import machine
except ImportError:  # CPython / WS dev harness
    machine = None

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _civil_from_unix(ts):
    """unix seconds (UTC, 1970 epoch) -> (year, month, day, hour, minute, second, weekday)."""
    days, secs = divmod(int(ts), 86400)
    hour, rem = divmod(secs, 3600)
    minute, second = divmod(rem, 60)
    # 1970-01-01 was a Thursday; RTC weekday here is Mon=0..Sun=6 (non-critical,
    # the signed timestamp only uses y/m/d h:m:s).
    weekday = (days + 3) % 7
    year = 1970
    while True:
        year_days = 366 if _is_leap(year) else 365
        if days < year_days:
            break
        days -= year_days
        year += 1
    month = 0
    while True:
        month_days = _DAYS_IN_MONTH[month]
        if month == 1 and _is_leap(year):
            month_days = 29
        if days < month_days:
            break
        days -= month_days
        month += 1
    return (year, month + 1, days + 1, hour, minute, second, weekday)


def set_utc_from_unix(ts):
    """Best-effort: set the machine RTC to the given UTC time.

    ``ts`` is unix time in seconds (milliseconds are accepted and scaled down).
    Returns True if the clock was set, False when there is no settable RTC
    (e.g. CPython dev) or on error. Never raises.
    """
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False
    if ts > 100000000000:  # looks like milliseconds
        ts = ts // 1000
    if ts <= 0:
        return False
    if machine is None:
        return False
    try:
        year, month, day, hour, minute, second, weekday = _civil_from_unix(ts)
        # RTC.datetime tuple: (year, month, day, weekday, hours, minutes, seconds, subseconds)
        machine.RTC().datetime((year, month, day, weekday, hour, minute, second, 0))
        return True
    except Exception as exc:  # pragma: no cover - hardware-specific
        print("[Clock] set_utc_from_unix failed:", exc)
        return False
