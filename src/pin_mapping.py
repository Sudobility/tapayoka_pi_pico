"""Logical action-pin mapping for Pico relay outputs."""

# NOTE: no `from __future__ import annotations` here — MicroPython has no
# __future__ module, and this file declares no annotations that would need it.
# This module is on the device path (command_handler -> pin_mapping), so it has
# to import under MicroPython.

# The logical (buyer-facing) pin -> physical GPIO map is board-specific and
# comes from the active board profile (src/boards.py), which also applies any
# per-device /board.json override. On the Pico this is the full RP2040 user-GPIO
# range; on ESP32/S3/C3 it is that chip's safe-output pins.
from .boards import LOGICAL_TO_GPIO

MIN_LOGICAL_PIN = 1
MAX_LOGICAL_PIN = len(LOGICAL_TO_GPIO)


def logical_pin_to_gpio(pin):
    if pin < MIN_LOGICAL_PIN or pin > MAX_LOGICAL_PIN:
        raise ValueError("Logical pin out of range: {}".format(pin))
    return LOGICAL_TO_GPIO[pin - 1]


def map_signal(signal):
    mapped = dict(signal)
    mapped["pinNumber"] = logical_pin_to_gpio(int(signal["pinNumber"]))
    return mapped


def map_signals(signals):
    return [map_signal(signal) for signal in signals]
