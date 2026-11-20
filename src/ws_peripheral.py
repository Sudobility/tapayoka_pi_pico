"""WebSocket transport for the Tapayoka Pico (local development only).

CPython-only. main.py imports this only under TRANSPORT=ws, which is
unreachable on the Pico (MicroPython has no os.getenv), so websockets / the
kiosk modules are never imported on-device.
"""

import asyncio
import json
import os
import time

from tapayoka_pi_core import build_device_info_data

from .command_handler import CommandHandler
from .config import BLE_DEVICE_NAME_PREFIX, DEFAULT_RELAY_PIN
from .control import (
    DISCONNECT, DISCONNECTED, PING, is_control_message, make_disconnect,
    make_pong,
    make_disconnected,
)
from .eth_wallet import _hex, _random_bytes
from .kiosk_state import generate_deep_link, update_kiosk_state
from .pin_mapping import logical_pin_to_gpio, map_signals

# Seconds to wait for the app's DISCONNECTED ack during a graceful teardown.
_DISCONNECT_ACK_SECONDS = 0.25


class TapayokaPicoWs:
    """WebSocket server that mirrors the Pico BLE peripheral semantics."""

    def __init__(self, wallet, relay, kiosk_state_dir):
        self._wallet = wallet
        self._relay = relay
        self._kiosk_state_dir = kiosk_state_dir
        self._handler = CommandHandler(wallet, relay)
        # Sockets that have actually engaged the device (sent a message), as
        # opposed to bare discovery sockets that only read `announce`. The kiosk
        # reflects engagement, not raw socket state.
        self._engaged = set()
        # Cache the QR deep link so the QR view can be restored on disconnect.
        # The PNG is written once at startup by main.py (state_dir set there);
        # here state_dir=None so we only need the URL string.
        self._deep_link = generate_deep_link(
            transport="ws",
            wallet_address=wallet.address,
            device_name=self.device_name,
            state_dir=None,
        )

    @property
    def device_name(self):
        return BLE_DEVICE_NAME_PREFIX + self._wallet.address_short

    def _state_file(self):
        return os.path.join(self._kiosk_state_dir, "state.json")

    def _build_device_info(self):
        data = build_device_info_data(
            self._wallet.address,
            "0.1.0-pico",
            bool(self._wallet.load_server_wallet()),
            int(time.time()),
            _hex(_random_bytes(16)),
        )
        return {"data": data, "signing": self._wallet.sign_response(data)}

    def _show_running(self, cmd_env):
        """Emit a RUNNING kiosk state derived from an EXECUTE command envelope."""
        data = cmd_env.get("data", {})
        offering = data.get("offeringType", "TRIGGER")
        signals = data.get("signals")
        if offering == "FIXED" and signals:
            action_signals = map_signals(signals)
            pin = int(action_signals[0]["pinNumber"])
            duration = int(sum(s["duration"] for s in action_signals))
        elif offering == "TRIGGER":
            pin = DEFAULT_RELAY_PIN
            duration = 1
            action_signals = [{"pinNumber": pin, "duration": 1}]
        else:
            # TIMED: honor the transmitted pin when present, else the default.
            duration = int(data.get("seconds", 0))
            pin = DEFAULT_RELAY_PIN
            if isinstance(signals, list) and signals:
                try:
                    pin = logical_pin_to_gpio(int(signals[0]["pinNumber"]))
                except (TypeError, ValueError):
                    pin = DEFAULT_RELAY_PIN
            action_signals = [{"pinNumber": pin, "duration": duration}]
        update_kiosk_state(
            self._state_file(),
            status="RUNNING",
            offering_type=offering,
            signals=action_signals,
            pin=pin,
            duration_seconds=duration,
            started_at=int(time.time()),
        )

    async def _graceful_close(self, ws):
        """Device-initiated graceful disconnect: send DISCONNECT, wait briefly
        for the app's DISCONNECTED ack, then close. Best-effort — a send error,
        a closed socket, or a timeout all just proceed to close. The ack rides
        the command channel, so match on the inner payload."""
        from websockets.exceptions import ConnectionClosed

        try:
            await ws.send(json.dumps({"type": "response", "data": make_disconnect()}))
        except ConnectionClosed:
            return
        deadline = time.monotonic() + _DISCONNECT_ACK_SECONDS
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except (asyncio.TimeoutError, TimeoutError):
                    break
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                data = parsed.get("data") if isinstance(parsed, dict) else None
                if is_control_message(data) and data.get("type") == DISCONNECTED:
                    break
        except ConnectionClosed:
            pass
        finally:
            try:
                await ws.close()
            except ConnectionClosed:
                pass

    async def _handle_connection(self, ws):
        from websockets.exceptions import ConnectionClosed

        remote = ws.remote_address
        print("[WS] Client connected:", remote)
        # Do not flip the kiosk to CONNECTED on raw socket open. Discovery
        # clients (the buyer app's scan probe) open a socket just to read
        # `announce` and never engage; CONNECTED is set on the first real
        # message below.

        await ws.send(json.dumps({
            "type": "announce",
            "data": {
                "deviceName": self.device_name,
                "walletAddress": self._wallet.address,
            },
        }))

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError) as e:
                    await ws.send(json.dumps({
                        "type": "response",
                        "data": {"status": "ERROR", "message": str(e)},
                    }))
                    continue

                msg_type = msg.get("type", "")

                # Control messages ride the command channel; answer them without
                # counting a probe as engagement.
                if msg_type == "command" and is_control_message(msg.get("data")):
                    ctrl = msg["data"]
                    if ctrl.get("type") == PING:
                        # The app's held connection runs a heartbeat; answer its
                        # ping with a pong or it drops the link in seconds.
                        await ws.send(json.dumps({
                            "type": "response",
                            "data": make_pong(ctrl["nonce"]),
                        }))
                        continue
                    if ctrl.get("type") == DISCONNECT:
                        print("[WS] Peer requested disconnect; acking and closing")
                        await ws.send(json.dumps({
                            "type": "response",
                            "data": make_disconnected(ctrl["nonce"]),
                        }))
                        await ws.close()
                        break
                    # DISCONNECTED (ack to our own DISCONNECT) is consumed by
                    # _graceful_close while it waits; ignore it here.
                    continue

                # First real message from this socket = real engagement → kiosk Connected.
                if ws not in self._engaged:
                    self._engaged.add(ws)
                    update_kiosk_state(self._state_file(), status="CONNECTED",
                                       message="Connected")

                if msg_type == "read_device_info":
                    await ws.send(json.dumps({
                        "type": "device_info",
                        "data": self._build_device_info(),
                    }))
                elif msg_type == "command":
                    cmd_env = msg.get("data", {})
                    # device_info can also be requested as a command (the BLE
                    # path the app now prefers); answer it on the response
                    # channel instead of passing it to the signing handler.
                    if (isinstance(cmd_env, dict)
                            and cmd_env.get("command") == "GET_DEVICE_INFO"):
                        await ws.send(json.dumps({
                            "type": "response",
                            "data": self._build_device_info(),
                        }))
                        continue
                    result = self._handler.handle(cmd_env)
                    await ws.send(json.dumps({"type": "response", "data": result}))
                    if result.get("status") == "NOT_CONFIGURED":
                        print("[WS] Closing connection: device not configured")
                        await self._graceful_close(ws)
                        break
                    if (result.get("status") == "OK"
                            and str(cmd_env.get("command", "")).upper() == "EXECUTE"):
                        self._show_running(cmd_env)
                else:
                    await ws.send(json.dumps({
                        "type": "response",
                        "data": {"status": "ERROR", "message": "Unknown type: " + msg_type},
                    }))
        except ConnectionClosed:
            pass
        finally:
            print("[WS] Client disconnected:", remote)
            self._engaged.discard(ws)
            # Only return to QR / safety-deactivate when the last engaged client
            # leaves. A dangling discovery socket closing must not reset a kiosk
            # that an engaged client is still driving.
            if not self._engaged:
                update_kiosk_state(self._state_file(), status="QR",
                                   qr_url=self._deep_link, message="Scan to connect")
                if self._relay.is_active and not self._relay.is_sequence_running:
                    print("[WS] Safety deactivation on disconnect")
                    self._relay.deactivate()

    def start(self, host="0.0.0.0", port=8765):
        print("[WS] Starting WebSocket peripheral on ws://{}:{}".format(host, port))
        asyncio.run(self._serve(host, port))

    async def _serve(self, host, port):
        import websockets

        async with websockets.serve(self._handle_connection, host, port):
            print("[WS] Peripheral published:", self.device_name)
            await asyncio.Future()  # run forever

    def stop(self):
        self._relay.cleanup()
        print("[WS] Peripheral stopped")
