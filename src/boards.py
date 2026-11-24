"""Board profiles: per-chip relay pin maps and timer configuration.

The same firmware runs on the Raspberry Pi Pico W (RP2040) and on ESP32 /
ESP32-S3 / ESP32-C3. Each chip has a different GPIO layout -- the Pico's raw
pin numbers mean nothing on an ESP32, and several ESP32 pins are unsafe as
outputs (SPI flash, input-only pins, strapping pins that constrain boot). This
module isolates every hardware-pin / timer difference behind one active
``PROFILE`` so the rest of the firmware stays board-agnostic.

Detection runs at import time:
  * ``sys.platform == "rp2"``   -> RP2040 (Pico W / Pico 2 W)
  * ``sys.platform == "esp32"`` -> inspect ``os.uname().machine`` for "S3"/"C3"
  * anything else (CPython dev / tests) -> RP2040 profile, so host behaviour
    and the 44-test suite stay deterministic.

An optional on-device override file (default ``/board.json``) lets an installer
force a profile and/or remap relay pins WITHOUT reflashing code, e.g.::

    {"profile": "esp32s3", "default_relay_pin": 10, "logical_to_gpio": [10, 11, 12]}

All keys are optional; unknown keys are ignored. In CPython dev mode the path is
env-overridable via ``BOARD_CONFIG_FILE`` (``os.getenv`` is absent on the
device, so on-device it always resolves to ``/board.json``).

Device-path module: stays within the MicroPython subset -- no ``__future__``,
no PEP-448 ``{**d}`` dict-unpacking, no ``json.dumps(sort_keys=...)``.
"""

import sys

try:
    import os
except ImportError:  # pragma: no cover - os always present on supported ports
    os = None

try:
    import json
except ImportError:  # pragma: no cover
    json = None

_getenv = getattr(os, "getenv", lambda k, d=None: d) if os else (lambda k, d=None: d)

BOARD_CONFIG_FILE = _getenv("BOARD_CONFIG_FILE", "/board.json")


# --- Built-in profiles -------------------------------------------------------
#
# ``logical_to_gpio`` is the buyer-facing logical pin (1..N) -> physical GPIO
# map; its length sets MAX_LOGICAL_PIN. Pins are ordered easy/safe first and
# deliberately exclude GPIOs that are unsafe as relay outputs on each chip
# (SPI flash, input-only, native-USB, UART0/REPL, and the riskier strapping
# pins). These defaults are conservative starting points -- confirm against the
# exact module and wiring, or override them in /board.json.
#
# ``timer_id``: None -> ``Timer()`` (RP2040 virtual timer); an int -> ``Timer(id)``
# (ESP32 requires an explicit id).

_RP2040 = {
    "name": "rp2040",
    "default_relay_pin": 15,
    # Full RP2040 user-GPIO range, flash-safe (flash is on dedicated QSPI pins).
    "logical_to_gpio": (
        15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
        16, 17, 18, 19, 20, 21, 22, 26, 27, 28,
    ),
    "timer_id": None,
}

_ESP32 = {
    "name": "esp32",
    # Classic ESP32 (WROOM-32). Excludes flash (6-11), input-only (34-39),
    # UART0 (1,3) and the riskier strapping pins (0,2,5,12,15) by default.
    "default_relay_pin": 4,
    "logical_to_gpio": (
        4, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 13, 14,
    ),
    "timer_id": 0,
}

_ESP32S3 = {
    "name": "esp32s3",
    # ESP32-S3. Excludes strapping (0,3,45,46), native USB (19,20),
    # UART0 (43,44) and the flash/PSRAM range (26-42, which varies by module;
    # add 38-42 via board.json on quad-PSRAM/no-PSRAM modules).
    "default_relay_pin": 1,
    "logical_to_gpio": (
        1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
        21, 47, 48,
    ),
    "timer_id": 0,
}

_ESP32C3 = {
    "name": "esp32c3",
    # ESP32-C3 (single-core RISC-V). Few free pins: excludes SPI flash (12-17),
    # USB-JTAG (18,19), UART0 (20,21), VDD_SPI (11) and strapping (2,8,9).
    "default_relay_pin": 3,
    "logical_to_gpio": (0, 1, 3, 4, 5, 6, 7, 10),
    "timer_id": 0,
}

_PROFILES = {
    "rp2040": _RP2040,
    "esp32": _ESP32,
    "esp32s3": _ESP32S3,
    "esp32c3": _ESP32C3,
}


def _machine_string():
    """os.uname().machine, or '' when unavailable (host without uname)."""
    try:
        return os.uname().machine
    except Exception:  # pragma: no cover - port-specific (no uname on host)
        return ""


def detect_profile_name():
    """Resolve the built-in profile name for the running board."""
    platform = getattr(sys, "platform", "")
    if platform == "rp2":
        return "rp2040"
    if platform == "esp32":
        machine = _machine_string().upper()
        if "S3" in machine:
            return "esp32s3"
        if "C3" in machine:
            return "esp32c3"
        return "esp32"
    # CPython dev / tests / unix MicroPython: behave like the Pico so pin
    # mappings and the host test-suite stay deterministic.
    return "rp2040"


def _load_override():
    """Read the optional board-config file. Returns {} when absent/invalid."""
    if json is None:
        return {}
    try:
        f = open(BOARD_CONFIG_FILE)
    except OSError:
        return {}
    except Exception:  # pragma: no cover - defensive
        return {}
    try:
        try:
            data = json.load(f)
        finally:
            f.close()
    except Exception as exc:  # pragma: no cover - malformed file
        print("[Board] ignoring invalid", BOARD_CONFIG_FILE, "-", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _resolve():
    """Pick the base profile and apply any /board.json overrides.

    Precedence (highest first):
      1. ``BOARD_PROFILE`` env var -- CPython dev only (``os.getenv`` is absent
         on-device), set by the WS harness ``--board`` flag to simulate a chip.
      2. ``"profile"`` in /board.json.
      3. auto-detection from ``sys.platform`` / ``os.uname()``.
    """
    override = _load_override()
    forced = _getenv("BOARD_PROFILE")
    if forced and forced not in _PROFILES:
        print("[Board] unknown BOARD_PROFILE", repr(forced), "- detecting instead")
        forced = None

    name = forced or (override.get("profile") if override else None)
    if name not in _PROFILES:
        name = detect_profile_name()

    base = _PROFILES[name]
    profile = dict(base)  # shallow copy; no PEP-448 unpacking on-device

    if override:
        if "default_relay_pin" in override:
            profile["default_relay_pin"] = int(override["default_relay_pin"])
        if "logical_to_gpio" in override:
            profile["logical_to_gpio"] = tuple(
                int(p) for p in override["logical_to_gpio"]
            )
        if "timer_id" in override:
            tid = override["timer_id"]
            profile["timer_id"] = None if tid is None else int(tid)

    # Normalise to a tuple regardless of source.
    profile["logical_to_gpio"] = tuple(profile["logical_to_gpio"])
    return profile


PROFILE = _resolve()

BOARD_NAME = PROFILE["name"]
DEFAULT_RELAY_PIN = PROFILE["default_relay_pin"]
LOGICAL_TO_GPIO = PROFILE["logical_to_gpio"]
TIMER_ID = PROFILE["timer_id"]


def new_timer(machine):
    """Construct a machine.Timer for the active board.

    RP2040 uses a no-arg virtual timer; ESP32 ports require an explicit id.
    ``machine`` is passed in so this stays importable on the host (no machine).
    """
    if TIMER_ID is None:
        return machine.Timer()
    return machine.Timer(TIMER_ID)
