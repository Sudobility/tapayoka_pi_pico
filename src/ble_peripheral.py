"""BLE GATT peripheral for Pico W using aioble."""

import json
import time

try:
    import aioble
    import bluetooth
    import asyncio
    AIOBLE_AVAILABLE = True
except ImportError:
    AIOBLE_AVAILABLE = False
    print("[BLE] aioble not available")

from .config import (
    BLE_CHAR_COMMAND_UUID, BLE_CHAR_DEVICE_INFO_UUID, BLE_CHAR_RESPONSE_UUID,
    BLE_DEVICE_NAME_PREFIX, BLE_SERVICE_UUID,
)
from .command_handler import CommandHandler
from .control import (
    DISCONNECT, DISCONNECTED, PING, is_control_message, make_disconnect,
    make_disconnected, make_pong,
)
from .ble_framing import frame_message, MessageReassembler, DEFAULT_FRAME_PAYLOAD
from .eth_wallet import _hex, _random_bytes
from tapayoka_pi_core import build_device_info_data

# How long the device waits for the app's DISCONNECTED ack after sending a
# DISCONNECT during a graceful teardown, before dropping the link anyway (ms).
_DISCONNECT_ACK_MS = 250

# Request command (app -> device) asking the device to send its signed
# device_info envelope back over the framed RESPONSE channel (so it is not
# bounded by the single-read 512-byte attribute cap). Mirrors
# tapayoka_types BLE_GET_DEVICE_INFO.
_GET_DEVICE_INFO = "GET_DEVICE_INFO"


def _uuid(s):
    if AIOBLE_AVAILABLE:
        return bluetooth.UUID(s)
    return s


class TapayokaPicoBle:
    def __init__(self, wallet, relay):
        self._wallet = wallet
        self._relay = relay
        self._handler = CommandHandler(wallet, relay)

    def _build_device_info(self, max_data_size=DEFAULT_FRAME_PAYLOAD):
        # Emit only the signed envelope: its `message` is the signed data
        # payload, which the app reconstructs into `data` via getDeviceInfo.
        # `maxDataSize` tells the app how big a frame the device accepts/sends
        # (data bytes per frame), so it chunks its writes to us accordingly.
        # Served two ways: the preferred framed GET_DEVICE_INFO reply (no size
        # cap), and the legacy static read characteristic (≤512B fallback).
        data = build_device_info_data(
            self._wallet.address,
            "0.1.0-pico",
            bool(self._wallet.load_server_wallet()),
            int(time.time()),
            _hex(_random_bytes(16)),
        )
        data["maxDataSize"] = max_data_size
        return self._wallet.sign_response(data)

    def _frame_payload(self, connection):
        """Data bytes per BLE frame for this connection: the negotiated ATT
        payload (MTU-3) minus the 1 flag byte, capped at DEFAULT_FRAME_PAYLOAD.
        Falls back to the default when the MTU is not exposed."""
        mtu = 0
        try:
            mtu = getattr(connection, "mtu", 0) or 0
        except Exception:
            mtu = 0
        size = DEFAULT_FRAME_PAYLOAD
        if mtu:
            avail = mtu - 3 - 1
            if avail < size:
                size = avail
        if size < 20:
            size = 20
        return size

    async def start(self):
        if not AIOBLE_AVAILABLE:
            print("[BLE] Cannot start - aioble not available")
            return

        device_name = BLE_DEVICE_NAME_PREFIX + self._wallet.address_short
        service = aioble.Service(_uuid(BLE_SERVICE_UUID))
        device_info_char = aioble.Characteristic(service, _uuid(BLE_CHAR_DEVICE_INFO_UUID), read=True)
        command_char = aioble.Characteristic(service, _uuid(BLE_CHAR_COMMAND_UUID), write=True, capture=True)
        response_char = aioble.Characteristic(service, _uuid(BLE_CHAR_RESPONSE_UUID), read=True, notify=True)
        aioble.register_services(service)

        # The default GATT value buffer is ~20 bytes, which silently TRUNCATES
        # inbound command writes (the Pico's real command-path bug). Enlarge the
        # command characteristic's receive buffer so a full frame (up to the ATT
        # payload) arrives intact; the app frames anything larger. append=False
        # so each write replaces the buffer rather than concatenating.
        try:
            bluetooth.BLE().gatts_set_buffer(command_char._value_handle, 512, False)
        except Exception as e:
            print("[BLE] gatts_set_buffer failed:", e)

        print("[BLE] Starting as:", device_name)

        while True:
            try:
                # Populate device_info BEFORE advertising so a central that
                # connects and immediately reads it never sees an empty value
                # (the value is a static GATT read; it can't be built on-demand
                # like the Pi's offset read-callback). Refreshed each advertise
                # cycle, so the signed timestamp/nonce is renewed per connection.
                device_info_char.write(json.dumps(self._build_device_info()).encode())

                connection = await aioble.advertise(250_000, name=device_name, services=[_uuid(BLE_SERVICE_UUID)])
                print("[BLE] Connected:", connection.device)

                # Per-connection framing state: inbound writes are reassembled
                # into whole messages, outbound notifications are chunked to the
                # negotiated frame size (also what we advertise as maxDataSize).
                chunk_size = self._frame_payload(connection)
                reassembler = MessageReassembler("cmd")

                while connection.is_connected():
                    try:
                        _, data = await asyncio.wait_for(command_char.written(), timeout=30)
                    except asyncio.TimeoutError:
                        continue  # idle tick; keep the connection

                    # The app writes every message framed; reassemble until a
                    # whole message is available.
                    message = reassembler.push(bytes(data))
                    if message is None:
                        continue

                    # Control messages ride the command channel; answer them here
                    # and never pass them to the (signing-checked) handler.
                    msg = self._parse(message)
                    if is_control_message(msg):
                        if msg.get("type") == DISCONNECT:
                            # App initiated teardown: ack, then drop the link so
                            # we return to advertising immediately.
                            print("[BLE] Peer requested disconnect; acking")
                            self._send(response_char, connection, make_disconnected(msg["nonce"]), chunk_size)
                            await connection.disconnect()
                            break
                        if msg.get("type") == PING:
                            # The app's held connection runs a heartbeat; answer
                            # its ping with a pong or it drops the link in seconds.
                            self._send(response_char, connection, make_pong(msg["nonce"]), chunk_size)
                            continue
                        # A stray DISCONNECTED / PONG: nothing to do.
                        continue

                    # device_info request: reply with the signed envelope over
                    # the framed channel (no 512B single-read cap).
                    if isinstance(msg, dict) and msg.get("command") == _GET_DEVICE_INFO:
                        self._send(response_char, connection, self._build_device_info(chunk_size), chunk_size)
                        continue

                    response = self._handle_command(message)
                    self._send(response_char, connection, response, chunk_size)
                    if response.get("status") == "NOT_CONFIGURED":
                        print("[BLE] Disconnecting: device not configured")
                        await self._graceful_disconnect(connection, command_char, response_char, reassembler, chunk_size)
                        break

                print("[BLE] Disconnected")
                if self._relay.is_active and not self._relay.is_sequence_running:
                    self._relay.deactivate()
            except Exception as e:
                print("[BLE] Error:", e)
                await asyncio.sleep(1)

    def _handle_command(self, data):
        try:
            return self._handler.handle(json.loads(data.decode()))
        except (ValueError, KeyError) as e:
            return {"status": "ERROR", "message": str(e)}

    def _parse(self, data):
        try:
            return json.loads(data.decode())
        except (ValueError, KeyError):
            return None

    def _send(self, response_char, connection, payload, chunk_size):
        """Notify a whole JSON message, framed into <= chunk_size data-byte
        frames so the app's reassembler can rebuild it regardless of size.

        Each frame is passed directly to notify() rather than via write()+notify:
        aioble's notify sends the characteristic's CURRENT stored value at
        transmit time, so a bare write()+notify() per frame races — a later
        frame's write can clobber the value an earlier notify is about to send.
        Passing the data to notify() sends that exact frame."""
        raw = json.dumps(payload).encode()
        for frame in frame_message(raw, chunk_size):
            response_char.notify(connection, frame)

    async def _graceful_disconnect(self, connection, command_char, response_char, reassembler, chunk_size):
        """Device-initiated graceful teardown: send DISCONNECT and wait up to
        _DISCONNECT_ACK_MS for the app's DISCONNECTED ack before dropping the
        link. The ack arrives (framed) on the same command_char — reassemble it
        the same way; we are tearing down regardless."""
        msg = make_disconnect()
        self._send(response_char, connection, msg, chunk_size)
        start = time.ticks_ms()
        while connection.is_connected():
            remaining = _DISCONNECT_ACK_MS - time.ticks_diff(time.ticks_ms(), start)
            if remaining <= 0:
                break
            try:
                _, data = await asyncio.wait_for(command_char.written(), timeout=remaining / 1000)
            except asyncio.TimeoutError:
                break
            message = reassembler.push(bytes(data))
            if message is None:
                continue
            reply = self._parse(message)
            if reply and reply.get("type") == DISCONNECTED and reply.get("nonce") == msg["nonce"]:
                break
        try:
            await connection.disconnect()
        except Exception:
            pass
