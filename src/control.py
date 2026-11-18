"""Control messages for the streaming protocol (graceful disconnect).

These ride the same command (app->device) and response (device->app) channels
as business traffic but are distinguished by a ``type`` field (business commands
use ``command``, responses use ``status``), so the transport layer answers them
without passing them to CommandHandler. Mirrors the TypeScript
``BleControlMessage`` in tapayoka_types and the fuller ``control`` module in
tapayoka_pi; the Pico only needs the DISCONNECT/DISCONNECTED handshake, so
PING/PONG are recognised (for forward compatibility) but not generated here.

DISCONNECT/DISCONNECTED is the graceful-teardown handshake: a side tearing down
a live link sends a DISCONNECT and waits briefly for the peer's DISCONNECTED ack
before closing, so the peer returns to idle (the device back to advertising)
immediately. On receiving a DISCONNECT, a side replies DISCONNECTED then closes.
"""

import time

PING = "PING"
PONG = "PONG"
DISCONNECT = "DISCONNECT"
DISCONNECTED = "DISCONNECTED"

_CONTROL_TYPES = (PING, PONG, DISCONNECT, DISCONNECTED)


def is_control_message(msg):
    """True if msg is a control message (not business traffic)."""
    return (
        isinstance(msg, dict)
        and msg.get("type") in _CONTROL_TYPES
        and isinstance(msg.get("nonce"), str)
    )


def _now_ms():
    return int(time.time() * 1000)


# A disconnect nonce only has to match its ack within one handshake, so a
# per-session counter is enough — no RNG dependency (and nothing that differs
# between MicroPython on-device and CPython in the dev harness).
_counter = 0


def _nonce():
    global _counter
    _counter += 1
    return "d{}-{}".format(_now_ms(), _counter)


def make_disconnect(nonce=None):
    return {"type": DISCONNECT, "nonce": nonce or _nonce(), "ts": _now_ms()}


def make_disconnected(nonce):
    return {"type": DISCONNECTED, "nonce": nonce, "ts": _now_ms()}
