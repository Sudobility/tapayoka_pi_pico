"""Tests for the Pico BLE framing (chunk + reassemble)."""

from src.ble_framing import (
    frame_message,
    MessageReassembler,
    FRAME_FLAG_START,
    FRAME_FLAG_FINAL,
)


def _roundtrip(data, chunk_size):
    frames = frame_message(data, chunk_size)
    r = MessageReassembler()
    out = None
    for f in frames:
        out = r.push(f)
    return frames, out


def test_single_frame_for_small_message():
    frames, out = _roundtrip(b'{"a":1}', 180)
    assert len(frames) == 1
    assert frames[0][0] == (FRAME_FLAG_START | FRAME_FLAG_FINAL)  # 0x03
    assert out == b'{"a":1}'


def test_multi_frame_roundtrip():
    data = b"x" * 1000
    frames, out = _roundtrip(data, 180)
    assert len(frames) == 6  # ceil(1000/180)
    # first frame START only, last frame FINAL only, middle none
    assert frames[0][0] & FRAME_FLAG_START
    assert not (frames[0][0] & FRAME_FLAG_FINAL)
    assert frames[-1][0] & FRAME_FLAG_FINAL
    assert not (frames[-1][0] & FRAME_FLAG_START)
    assert out == data


def test_empty_message_is_one_final_frame():
    frames, out = _roundtrip(b"", 180)
    assert len(frames) == 1
    assert frames[0] == bytes([FRAME_FLAG_START | FRAME_FLAG_FINAL])
    assert out == b""


def test_reassembler_waits_for_final():
    r = MessageReassembler()
    frames = frame_message(b"y" * 400, 180)
    assert r.push(frames[0]) is None  # START, more to come
    assert r.push(frames[1]) is None
    assert r.push(frames[2]) == b"y" * 400  # FINAL completes


def test_continuation_without_start_is_dropped():
    r = MessageReassembler()
    # A non-START, non-FINAL frame with no prior START yields nothing.
    assert r.push(bytes([0x00]) + b"junk") is None


def test_start_resyncs_mid_message():
    r = MessageReassembler()
    first = frame_message(b"a" * 300, 180)  # 2 frames
    assert r.push(first[0]) is None  # START, still incomplete (no FINAL)
    # A fresh START arrives before the previous message finished → the partial
    # buffer is discarded and reassembly restarts.
    second = frame_message(b"z" * 300, 180)
    assert r.push(second[0]) is None
    assert r.push(second[1]) == b"z" * 300
