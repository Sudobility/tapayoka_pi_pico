# tapayoka_pi_pico

> **Git policy — never auto-commit or auto-push.** Leave your work in the working tree.
> Run `git commit`, `git push`, `gh pr create`, or `scripts/push_all.sh` **only when the user
> explicitly asks in that turn**. Approval for an earlier change does not carry forward, and
> finishing a task is not permission to commit it.

MicroPython BLE peripheral firmware for Raspberry Pi Pico W (RP2040 + CYW43439). Same BLE protocol as tapayoka_pi but for constrained hardware. Firmware version string: `0.1.0-pico`.

## Architecture

- **BLE**: aioble (MicroPython BLE library); GATT service + device-info/command/response chars in `src/config.py`. Device name `tapayoka-<addr[2:10]>`.
- **Crypto**: pure-Python secp256k1 + keccak256 + EIP-191 on device (no eth-account). eth-account is used ONLY in tests on the host as an independent oracle.
- **GPIO**: MicroPython `machine.Pin`/`Timer` (`src/gpio_control.py`). Falls back to a no-op mock when `machine` is absent (host/dev).
- **Shared policy**: `tapayoka_pi_core` is vendored under `src/tapayoka_pi_core/` (NOT installed from PyPI at runtime — the device must be self-contained). It is the same code as the sibling `../tapayoka_pi_core` repo; re-sync the local copy with `python scripts/sync_core.py` (copies `../tapayoka_pi_core/src/tapayoka_pi_core`). Do not hand-edit the vendored copy; edit upstream and re-sync.
- **No Docker / no build step**: firmware is flashed directly; just copy `src/` to the device.

## Commands

```bash
pip install -r requirements-dev.txt   # pytest ALONE is insufficient: tests import eth_account + websockets
pytest tests/ -v                       # 44 tests; run from repo root (tests import `src.*`), host CPython only
TRANSPORT=ws python -m src.main        # local WS+kiosk dev harness (see below)
```

## Local dev (WebSocket, no BLE/GPIO)

`TRANSPORT=ws python -m src.main` runs a CPython WebSocket transport
(`TapayokaPicoWs`) + a browser kiosk instead of BLE, so the firmware logic can
be driven on a Mac. WS on `8765`, kiosk on `8026`, state under `./dev_state/`.
All ws-mode modules (`ws_peripheral`, `kiosk_server`, `kiosk_state`) are
CPython-only and are never imported on the Pico (the `ws` branch is unreachable
in MicroPython, which has no `os.getenv`).

## Deployment

Flash MicroPython (with aioble) firmware to Pico W, then copy `src/` (including the vendored `tapayoka_pi_core`) to the device. `src/main.py` is the entry point. Default transport is `ble` (MicroPython has no `os.getenv`, so the `getattr`-defaulted `TRANSPORT` resolves to "ble" and the CPython-only ws branch is unreachable on-device).

## Gotchas / conventions

- **Relay pins**: default relay is GPIO 15 (`DEFAULT_RELAY_PIN`). Buyer-facing "logical" action pins (1..26) are remapped to physical GPIOs by `src/pin_mapping.py`; never pass a raw logical pin to `machine.Pin`.
- **Wallet bootstrap**: device self-generates its key on first boot (`/wallet_key.json`); the server wallet is set later via the signed `SETUP_SERVER` command (`/server_wallet.txt`). Paths are env-overridable only in CPython dev mode.
- **Offering types** (`command_handler.py`): `TRIGGER` = 1s pulse; `TIMED` = hold one pin for N seconds; `FIXED` = async multi-signal sequence. Commands are replay-protected (nonce cache + `exp` expiry) and require a valid server signature.
- **Mock relay** (host/ws dev): no `machine` module means timed `activate()` never auto-offs (no `Timer`); sequences and disconnect-safety still work.

## Git Workflow

- Do not use feature branches for code changes. Always stay on the current branch.
