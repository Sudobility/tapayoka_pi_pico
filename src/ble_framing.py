"""BLE message framing for the Pico (MicroPython).

Lets a single BLE characteristic carry arbitrary-size JSON, like the Pi and the
app. Mirrors tapayoka_pi/src/ble_framing.py and
tapayoka_bluetooth/src/transport/framing.ts byte-for-byte.

Frame layout (per BLE packet):
    byte 0   : flags - bit0 FINAL (last frame of a message)
                       bit1 START (first frame of a message)
    byte 1.. : this frame's slice of the UTF-8 message bytes
A single-frame message therefore has flags = START|FINAL = 0x03.

MicroPython notes: no `from __future__`, no PEP-604 `X | Y` / `list[int]`
annotations, and print() has no `flush` kwarg — so this module avoids all of
them (it also runs unchanged under host CPython for the test suite).
"""

FRAME_FLAG_FINAL = 0x01
FRAME_FLAG_START = 0x02

# Conservative DATA bytes per frame (the frame itself is +1 flag byte). Safe for
# any negotiated MTU >= 184, which iOS and (post-requestMtu) Android provide.
DEFAULT_FRAME_PAYLOAD = 180

FRAMING_DEBUG = False


def _flog(*args):
    if FRAMING_DEBUG:
        print("[BLE-frame]", *args)


def frame_message(data, chunk_size=DEFAULT_FRAME_PAYLOAD):
    """Split a bytes message into frames (each a bytes object ready to notify)."""
    size = chunk_size if chunk_size > 0 else 1
    if not data:
        return [bytes([FRAME_FLAG_START | FRAME_FLAG_FINAL])]
    frames = []
    total = len(data)
    off = 0
    while off < total:
        end = min(off + size, total)
        chunk = data[off:end]
        flags = 0
        if off == 0:
            flags |= FRAME_FLAG_START
        if end >= total:
            flags |= FRAME_FLAG_FINAL
        frames.append(bytes([flags]) + bytes(chunk))
        off = end
    _flog("encode", total, "B ->", len(frames), "frame(s) @", size, "B/frame")
    return frames


class MessageReassembler:
    """Reassembles incoming frames into complete messages.

    Feed each frame's bytes to :meth:`push`; it returns the complete message
    bytes when the FINAL frame arrives, otherwise ``None``.
    """

    def __init__(self, label="rx"):
        self._label = label
        self._buf = bytearray()
        self._receiving = False

    def push(self, frame):
        if not frame:
            return None
        flags = frame[0]
        start = bool(flags & FRAME_FLAG_START)
        final = bool(flags & FRAME_FLAG_FINAL)
        if start:
            self._buf = bytearray()
            self._receiving = True
        elif not self._receiving:
            _flog(self._label, "continuation with no START - dropping")
            return None
        self._buf.extend(frame[1:])
        if final:
            message = bytes(self._buf)
            self._buf = bytearray()
            self._receiving = False
            _flog(self._label, "COMPLETE", len(message), "B")
            return message
        return None

    def reset(self):
        self._buf = bytearray()
        self._receiving = False
