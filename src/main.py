"""Tapayoka Pico W - peripheral entry point (BLE on device, WS for local dev)."""

import os
import sys

# os.getenv is absent in MicroPython -> getattr default makes _TRANSPORT "ble"
# on-device, so the ws branch (and all CPython-only imports) is unreachable.
_getenv = getattr(os, "getenv", lambda k, d=None: d)

# --- WS dev-harness CLI flags ------------------------------------------------
# Promote a few "--flag value" / "--flag=value" args to env vars so they are
# picked up by config/boards at import time. This is a CPython-only convenience
# for the local harness: on the device MicroPython's `os` has no `environ`
# (and argv is empty), so the whole block is skipped and has no on-device cost.
#   --board <rp2040|esp32|esp32s3|esp32c3>   simulate a chip's pin map
#   --board-config <path>                    load a full /board.json-style file
#   --transport <ws|ble>                     same as TRANSPORT=...
_FLAG_TO_ENV = {
    "--board": "BOARD_PROFILE",
    "--board-config": "BOARD_CONFIG_FILE",
    "--transport": "TRANSPORT",
}
if hasattr(os, "environ"):
    _args = list(getattr(sys, "argv", []))[1:]
    _i = 0
    while _i < len(_args):
        _tok = _args[_i]
        _val = None
        if _tok[:2] == "--" and "=" in _tok:
            _tok, _val = _tok.split("=", 1)
        if _tok in _FLAG_TO_ENV:
            if _val is None:
                _i += 1
                _val = _args[_i] if _i < len(_args) else ""
            os.environ[_FLAG_TO_ENV[_tok]] = _val
        _i += 1

_TRANSPORT = _getenv("TRANSPORT", "ble").lower()

# In WS dev mode (CPython on a laptop) redirect wallet + kiosk storage off the
# filesystem root so we neither need root nor pollute "/". This block is only
# reachable under CPython, where os.environ exists.
if _TRANSPORT == "ws":
    os.environ.setdefault("WALLET_KEY_FILE", "./dev_state/wallet_key.json")
    os.environ.setdefault("SERVER_WALLET_FILE", "./dev_state/server_wallet.txt")
    os.environ.setdefault("KIOSK_STATE_DIR", "./dev_state")
    os.makedirs(os.environ["KIOSK_STATE_DIR"], exist_ok=True)

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .boards import BOARD_NAME
from .config import BLE_DEVICE_NAME_PREFIX, DEFAULT_RELAY_PIN
from .eth_wallet import EthWallet
from .gpio_control import RelayController
from .ble_peripheral import TapayokaPicoBle


def main():
    print("=" * 40)
    print("Tapayoka Pico W -", _TRANSPORT.upper())
    print("[Board]", BOARD_NAME, "| relay pin", DEFAULT_RELAY_PIN)
    print("=" * 40)

    wallet = EthWallet()
    relay = RelayController(pin_num=DEFAULT_RELAY_PIN)

    server_wallet = EthWallet.load_server_wallet()
    if server_wallet:
        print("[Config] Server wallet:", server_wallet[:10], "...")
    else:
        print("[Config] No server wallet (awaiting setup via " + _TRANSPORT.upper() + ")")

    if _TRANSPORT == "ws":
        from .kiosk_server import start_kiosk_server
        from .kiosk_state import generate_deep_link, update_kiosk_state
        from .ws_peripheral import TapayokaPicoWs

        state_dir = os.environ["KIOSK_STATE_DIR"]
        kiosk_port = int(_getenv("KIOSK_PORT", "8026"))
        start_kiosk_server(state_dir, port=kiosk_port)

        device_name = BLE_DEVICE_NAME_PREFIX + wallet.address_short
        deep_link = generate_deep_link(
            transport="ws",
            wallet_address=wallet.address,
            device_name=device_name,
            state_dir=state_dir,
        )
        update_kiosk_state(os.path.join(state_dir, "state.json"),
                           status="QR", qr_url=deep_link, message="Scan to connect")

        peripheral = TapayokaPicoWs(wallet, relay, state_dir)
        try:
            peripheral.start()
        except KeyboardInterrupt:
            print("Shutting down...")
            relay.cleanup()
    else:
        ble = TapayokaPicoBle(wallet, relay)
        try:
            asyncio.run(ble.start())
        except KeyboardInterrupt:
            print("Shutting down...")
            relay.cleanup()


if __name__ == "__main__":
    main()
