"""Tests for Pico BLE peripheral command dispatch (mock mode)."""

import json
from unittest.mock import patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from src.eth_wallet import EthWallet
from src.gpio_control import RelayController


@pytest.fixture
def temp_wallet_dir(tmp_path):
    with (
        patch("src.eth_wallet.WALLET_KEY_FILE", str(tmp_path / "device_key.json")),
        patch("src.eth_wallet.SERVER_WALLET_FILE", str(tmp_path / "server_wallet.txt")),
    ):
        yield tmp_path


@pytest.fixture
def ble(temp_wallet_dir):
    from src.ble_peripheral import TapayokaPicoBle
    return TapayokaPicoBle(EthWallet(), RelayController())


def test_invalid_json(ble):
    assert ble._handle_command(b"not json")["status"] == "ERROR"


def test_unknown_command(ble):
    assert ble._handle_command(json.dumps({"command": "BOGUS"}).encode())["status"] == "ERROR"


def test_execute_without_server_wallet(ble):
    server = Account.create()
    data = {"orderId": "o1", "offeringType": "TIMED", "seconds": 1, "nonce": "n", "exp": 9999999999}
    message = json.dumps(data)
    signed = server.sign_message(encode_defunct(text=message))
    sig = signed.signature.hex()
    env = {
        "command": "EXECUTE",
        "data": data,
        "signing": {"walletAddress": server.address, "message": message,
                    "signature": sig if sig.startswith("0x") else "0x" + sig},
    }
    assert ble._handle_command(json.dumps(env).encode())["status"] == "NOT_CONFIGURED"


def test_device_info_envelope_recovers_to_device(ble):
    # device_info is served as a single GATT read (capped ~512B), so the Pico
    # emits only the compact signed envelope — {walletAddress, message,
    # signature} — not a {data, signing} shape that would carry the payload
    # twice. The app reconstructs `data` from the signed `message`.
    env = ble._build_device_info()
    assert set(env) == {"walletAddress", "message", "signature"}
    recovered = Account.recover_message(
        encode_defunct(text=env["message"]),
        signature=env["signature"],
    )
    assert recovered.lower() == ble._wallet.address.lower()
    # The signed message is the full device-info payload (what the app parses
    # back into `data`), plus a signing_timestamp.
    decoded = json.loads(env["message"])
    signing_ts = decoded.pop("signing_timestamp")
    assert isinstance(signing_ts, str)
    assert decoded["walletAddress"].lower() == ble._wallet.address.lower()
    assert decoded["firmwareVersion"] == "0.1.0-pico"
    assert "hasServerWallet" in decoded
    # The envelope advertises the device's chunk size so the app sizes its
    # writes to us; default when no connection MTU is known.
    assert decoded["maxDataSize"] == 180


def test_device_info_reports_requested_max_data_size(ble):
    env = ble._build_device_info(96)
    decoded = json.loads(env["message"])
    assert decoded["maxDataSize"] == 96


class _FakeChar:
    """Captures each framed notification the peripheral sends. Mirrors aioble's
    notify(connection, data) where data is the exact frame to transmit."""

    def __init__(self):
        self.frames = []

    def notify(self, _connection, data=None):
        self.frames.append(bytes(data))


def test_send_frames_a_large_message_that_reassembles(ble):
    from src.ble_framing import MessageReassembler

    char = _FakeChar()
    payload = {"status": "OK", "message": "x" * 500}  # > one 180B frame
    ble._send(char, object(), payload, 180)

    assert len(char.frames) >= 2  # actually chunked
    reassembler = MessageReassembler()
    out = None
    for frame in char.frames:
        out = reassembler.push(frame)
    assert out is not None
    assert json.loads(out.decode()) == payload


def test_frame_payload_defaults_without_mtu(ble):
    class _NoMtu:
        pass

    assert ble._frame_payload(_NoMtu()) == 180

    class _SmallMtu:
        mtu = 100  # ATT payload 97 -> 96 data bytes/frame

    assert ble._frame_payload(_SmallMtu()) == 96
