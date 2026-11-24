"""Tests for the board-profile layer (host CPython)."""

import importlib
import json

import pytest


def _reload_boards():
    import src.boards as boards
    return importlib.reload(boards)


def test_host_defaults_to_rp2040():
    """On CPython (no rp2/esp32 platform) the Pico profile keeps dev + tests
    deterministic and byte-compatible with the pre-refactor constants."""
    boards = _reload_boards()
    assert boards.BOARD_NAME == "rp2040"
    assert boards.DEFAULT_RELAY_PIN == 15
    assert boards.TIMER_ID is None
    assert boards.LOGICAL_TO_GPIO[0] == 15
    assert boards.LOGICAL_TO_GPIO[1] == 14
    assert len(boards.LOGICAL_TO_GPIO) == 26


@pytest.mark.parametrize("platform,machine,expected", [
    ("rp2", "Raspberry Pi Pico W with RP2040", "rp2040"),
    ("esp32", "ESP32 module with ESP32", "esp32"),
    ("esp32", "ESP32S3 module with ESP32S3", "esp32s3"),
    ("esp32", "ESP32C3 module with ESP32C3", "esp32c3"),
    ("darwin", "arm64", "rp2040"),  # host fallback
])
def test_detect_profile_name(monkeypatch, platform, machine, expected):
    import src.boards as boards
    monkeypatch.setattr(boards.sys, "platform", platform, raising=False)
    monkeypatch.setattr(boards, "_machine_string", lambda: machine)
    assert boards.detect_profile_name() == expected


def test_every_profile_is_well_formed():
    boards = _reload_boards()
    for name, prof in boards._PROFILES.items():
        pins = prof["logical_to_gpio"]
        assert prof["name"] == name
        assert isinstance(prof["default_relay_pin"], int)
        assert len(pins) >= 1
        assert all(isinstance(p, int) and p >= 0 for p in pins)
        assert len(set(pins)) == len(pins), "duplicate GPIO in %s" % name
        # RP2040 uses a no-arg virtual timer; ESP32 ports need an id.
        if name == "rp2040":
            assert prof["timer_id"] is None
        else:
            assert isinstance(prof["timer_id"], int)


def test_board_json_override(tmp_path, monkeypatch):
    cfg = tmp_path / "board.json"
    cfg.write_text(json.dumps({
        "profile": "esp32c3",
        "default_relay_pin": 7,
        "logical_to_gpio": [7, 6, 5],
        "timer_id": 1,
    }))
    monkeypatch.setenv("BOARD_CONFIG_FILE", str(cfg))
    boards = _reload_boards()
    assert boards.BOARD_NAME == "esp32c3"
    assert boards.DEFAULT_RELAY_PIN == 7
    assert boards.LOGICAL_TO_GPIO == (7, 6, 5)
    assert boards.TIMER_ID == 1
    # restore defaults for the rest of the suite
    monkeypatch.delenv("BOARD_CONFIG_FILE")
    _reload_boards()


def test_board_json_missing_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("BOARD_CONFIG_FILE", str(tmp_path / "does_not_exist.json"))
    boards = _reload_boards()
    assert boards.BOARD_NAME == "rp2040"
    monkeypatch.delenv("BOARD_CONFIG_FILE")
    _reload_boards()


def test_board_json_malformed_is_ignored(tmp_path, monkeypatch):
    cfg = tmp_path / "board.json"
    cfg.write_text("{not valid json")
    monkeypatch.setenv("BOARD_CONFIG_FILE", str(cfg))
    boards = _reload_boards()
    assert boards.BOARD_NAME == "rp2040"  # falls back, does not raise
    monkeypatch.delenv("BOARD_CONFIG_FILE")
    _reload_boards()


def test_board_profile_env_forces_profile(monkeypatch):
    """The WS harness --board flag sets BOARD_PROFILE; it must win over
    detection on the host."""
    monkeypatch.setenv("BOARD_PROFILE", "esp32s3")
    boards = _reload_boards()
    assert boards.BOARD_NAME == "esp32s3"
    assert boards.DEFAULT_RELAY_PIN == 1
    assert boards.TIMER_ID == 0
    monkeypatch.delenv("BOARD_PROFILE")
    _reload_boards()


def test_board_profile_env_beats_board_json(tmp_path, monkeypatch):
    cfg = tmp_path / "board.json"
    cfg.write_text(json.dumps({"profile": "esp32c3"}))
    monkeypatch.setenv("BOARD_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("BOARD_PROFILE", "esp32")  # env outranks the file
    boards = _reload_boards()
    assert boards.BOARD_NAME == "esp32"
    monkeypatch.delenv("BOARD_PROFILE")
    monkeypatch.delenv("BOARD_CONFIG_FILE")
    _reload_boards()


def test_invalid_board_profile_env_falls_back(monkeypatch):
    monkeypatch.setenv("BOARD_PROFILE", "esp8266")  # not a known profile
    boards = _reload_boards()
    assert boards.BOARD_NAME == "rp2040"  # falls back to detection, no raise
    monkeypatch.delenv("BOARD_PROFILE")
    _reload_boards()


def test_new_timer_uses_profile_id(monkeypatch):
    boards = _reload_boards()

    class FakeTimer:
        def __init__(self, *args):
            self.args = args

    class FakeMachine:
        Timer = FakeTimer

    # RP2040-style: no id
    monkeypatch.setattr(boards, "TIMER_ID", None)
    assert boards.new_timer(FakeMachine()).args == ()

    # ESP32-style: explicit id
    monkeypatch.setattr(boards, "TIMER_ID", 0)
    assert boards.new_timer(FakeMachine()).args == (0,)
