# Board support & configuration

The Tapayoka firmware runs unchanged on four MicroPython targets:

| Profile name | Chip | Example modules | Flash target |
|---|---|---|---|
| `rp2040`  | RP2040 / RP2350 | Raspberry Pi Pico W, Pico 2 W | `RPI_PICO_W`, `RPI_PICO2_W` |
| `esp32`   | ESP32 (Xtensa, dual-core) | WROOM-32, DevKitC | `ESP32_GENERIC` |
| `esp32s3` | ESP32-S3 (Xtensa, dual-core) | DevKitC-1 | `ESP32_GENERIC_S3` |
| `esp32c3` | ESP32-C3 (RISC-V, single-core) | DevKitM-1 | `ESP32_GENERIC_C3` |

Everything chip-specific — the relay GPIO numbers and the `machine.Timer`
construction — lives in **`src/boards.py`**. The rest of the firmware (BLE
protocol, crypto, command handling) is board-agnostic and never references a raw
pin number.

---

## 1. How a board is chosen

At import time `src/boards.py` resolves exactly one active `PROFILE`. Resolution
order, highest priority first:

1. **`BOARD_PROFILE` env var** — CPython/dev only (MicroPython has no
   `os.getenv`, so this is unreachable on the device). Set by the WS harness
   `--board` flag to *simulate* a chip on your Mac. See §4.
2. **`"profile"` in `/board.json`** — an on-device override file (§3).
3. **Auto-detection** — `sys.platform` gives `rp2` or `esp32`; for ESP32,
   `os.uname().machine` is inspected for `S3` / `C3` to pick the variant.
   Anything else (CPython dev, host tests, unix MicroPython) falls back to
   `rp2040` so development and the test-suite stay deterministic.

The resolved profile exposes four module-level values used everywhere else:

```python
from .boards import BOARD_NAME, DEFAULT_RELAY_PIN, LOGICAL_TO_GPIO, TIMER_ID
```

- `BOARD_NAME` — the profile name string.
- `DEFAULT_RELAY_PIN` — physical GPIO for the single default relay
  (re-exported as `config.DEFAULT_RELAY_PIN`).
- `LOGICAL_TO_GPIO` — tuple mapping buyer-facing **logical** pins `1..N` to
  physical GPIOs (consumed by `src/pin_mapping.py`; its length sets
  `MAX_LOGICAL_PIN`).
- `TIMER_ID` — `None` on RP2040 (no-arg virtual `Timer()`), an `int` on ESP32
  (hardware timer id). `boards.new_timer(machine)` builds the right one.

---

## 2. Built-in pin maps

Logical pin `1` is the default/primary output; higher logical pins are the
multi-signal sequence outputs. Pins are ordered "easy/safe first".

### rp2040 — 26 logical pins
All RP2040 user GPIOs are output-safe (flash is on dedicated QSPI pins).
```
logical  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26
GPIO    15 14 13 12 11 10  9  8  7  6  5  4  3  2  1  0 16 17 18 19 20 21 22 26 27 28
default relay = GPIO 15   timer = virtual
```

### esp32 (classic WROOM-32) — 15 logical pins
```
logical  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
GPIO     4 16 17 18 19 21 22 23 25 26 27 32 33 13 14
default relay = GPIO 4    timer = 0
```
Excluded: `6-11` (SPI flash), `34-39` (input-only, no output driver),
`1,3` (UART0/REPL), `0,2,5,12,15` (strapping — can block boot or glitch a relay
at reset).

### esp32s3 — 20 logical pins
```
logical  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20
GPIO     1  2  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 21 47 48
default relay = GPIO 1    timer = 0
```
Excluded: `0,3,45,46` (strapping), `19,20` (native USB D-/D+),
`43,44` (UART0), `26-42` (SPI flash / PSRAM; range varies by module). On a
quad-PSRAM or no-PSRAM S3 you can usually also free `38-42` — add them via
`/board.json` **only after confirming your module is not octal-PSRAM**.

### esp32c3 — 8 logical pins
```
logical  1  2  3  4  5  6  7  8
GPIO     0  1  3  4  5  6  7 10
default relay = GPIO 3    timer = 0
```
Excluded: `12-17` (SPI flash), `18,19` (USB-JTAG), `20,21` (UART0),
`11` (VDD_SPI), `2,8,9` (strapping). The C3 is pin-starved by design.

> ⚠️ **These lists are conservative starting points, not guarantees.** Safe
> GPIOs vary by *module* (WROVER vs WROOM, octal- vs quad-PSRAM S3, etc.) and by
> what else you have wired. Always verify against your board's datasheet/pinout
> before connecting a relay, and override with `/board.json` where your hardware
> differs. Driving a relay from a strapping pin can prevent the board from
> booting.

---

## 3. Per-device override: `/board.json`

To change pins **without reflashing code**, copy one of the samples in
[`docs/boards/`](./boards/) to the device filesystem root as `/board.json`:

```bash
mpremote connect <port> fs cp docs/boards/esp32s3.board.json :board.json
```

Schema (**all keys optional**):

| Key | Type | Effect |
|---|---|---|
| `profile` | string | Force a base profile (`rp2040`/`esp32`/`esp32s3`/`esp32c3`). Omit to keep auto-detection. An unknown name is ignored. |
| `default_relay_pin` | int | Physical GPIO for the default relay. |
| `logical_to_gpio` | int[] | Replace the entire logical→GPIO map. Its length becomes `MAX_LOGICAL_PIN`. |
| `timer_id` | int \| null | `machine.Timer` id (`null` = no-arg virtual timer). |

Keys you omit fall back to the detected/forced profile. Any `"_comment"` key (or
other unknown key) is ignored, so the samples document themselves. A missing or
malformed `/board.json` is ignored with a log line — the device still boots on
its detected profile.

**Common cases**

- *Same chip, relay on a different pin* — partial override, keep detection:
  ```json
  { "default_relay_pin": 22 }
  ```
  (see [`custom-example.board.json`](./boards/custom-example.board.json))
- *Full custom wiring* — set `profile` for the timer/defaults and give your own
  `logical_to_gpio`.

Precedence reminder: a `/board.json` `"profile"` is overridden by the dev-only
`BOARD_PROFILE` env var, but on-device (no `getenv`) `/board.json` wins over
detection.

---

## 4. Simulating a board on your Mac (WS dev harness)

The WebSocket dev harness runs the firmware logic on CPython with no BLE/GPIO
(see the main README / CLAUDE.md). You can make it resolve any board profile so
you can watch the mock relay log the *right physical GPIOs* for a given chip:

```bash
# Simulate an ESP32-S3 pin map over WebSocket + kiosk
TRANSPORT=ws python -m src.main --board esp32s3

# Flags can replace the env vars entirely:
python -m src.main --transport ws --board esp32c3

# Load a full custom map file instead of a built-in profile:
python -m src.main --transport ws --board-config docs/boards/custom-example.board.json
```

Flags (CPython-only; ignored/stripped on the device, where argv is empty and
`os.environ` is absent):

| Flag | Env equivalent | Meaning |
|---|---|---|
| `--board <name>` | `BOARD_PROFILE` | Force a profile for this run. |
| `--board-config <path>` | `BOARD_CONFIG_FILE` | Use a specific `board.json`-style file (default on-device is `/board.json`). |
| `--transport <ws\|ble>` | `TRANSPORT` | Transport to start. |

On startup the harness prints the resolved board, e.g.:

```
Tapayoka Pico W - WS
[Board] esp32s3 | relay pin 1
```

Because the host has no `machine` module, the relay stays in mock mode — this
simulates the **pin mapping / addressing**, not real electrical output.

---

## 5. Deploying

Deployment is **two phases**: flash MicroPython once per chip (phase A,
chip-specific), then copy the firmware files (phase B, identical for every
board — the chip self-detects at boot).

### Phase A — flash MicroPython + BLE (one-time, per chip)

| Board | How to flash | Firmware |
|---|---|---|
| **Pico W / Pico 2 W** | Hold **BOOTSEL**, plug in USB → a `RPI-RP2` drive mounts → drag the `.uf2` onto it. | `RPI_PICO_W` / `RPI_PICO2_W` |
| **ESP32 / S3 / C3** | Serial flash the `.bin` with esptool (below). | `ESP32_GENERIC` / `ESP32_GENERIC_S3` / `ESP32_GENERIC_C3` |

Download the matching build (must include BLE) from
<https://micropython.org/download/>.

**Easiest — the helper** erases, flashes at the right offset for the variant,
resets, checks `aioble`, and can chain straight into the file deploy:

```bash
pip install esptool
# S3/C3 use flash offset 0x0, classic ESP32 uses 0x1000 -- the script picks it.
scripts/flash_esp32.sh -v esp32s3 -f ~/Downloads/ESP32_GENERIC_S3-*.bin
# all-in-one: flash, install aioble if the build lacks it, then deploy:
scripts/flash_esp32.sh -v esp32c3 -f fw.bin -p /dev/cu.usbserial-10 \
                       --install-aioble --deploy
```

**By hand**, the equivalent esptool calls:

```bash
esptool.py --chip esp32 --port <PORT> erase_flash
# offset 0x1000 for classic ESP32; 0x0 for S3/C3
esptool.py --chip esp32 --port <PORT> --baud 460800 write_flash -z 0x1000 ESP32_GENERIC-*.bin
mpremote connect <PORT> mip install aioble   # only if the build lacks aioble
```

> Most dev boards auto-enter the bootloader via DTR/RTS. If flashing won't
> start, hold the **BOOT** button while esptool connects, then release.

### Phase B — copy the firmware (identical for all four boards)

Use the helper, which stages `src/` without host bytecode, replaces `/src`,
copies `main.py`, optionally installs a `/board.json`, verifies detection, and
resets:

```bash
scripts/deploy.sh --list                                   # find your board's port
scripts/deploy.sh -p /dev/cu.usbmodem14101                 # Pico
scripts/deploy.sh -p /dev/cu.SLAB_USBtoUART \
                  -b docs/boards/esp32s3.board.json         # ESP32-S3 w/ custom pins
```

Or do it by hand (what the script runs):

```bash
mpremote connect <PORT> fs rm -r :src          # drop the old copy (ignore if absent)
mpremote connect <PORT> fs cp -r src :         # src/ -> /src
mpremote connect <PORT> fs cp main.py :        # launcher -> /main.py
mpremote connect <PORT> fs cp docs/boards/esp32c3.board.json :board.json   # optional
mpremote connect <PORT> reset
```

On reset you should see the board line in the boot log:

```
[Board] esp32s3 | relay pin 1
```

The device self-generates its wallet on first boot regardless of chip; the
server wallet is still set later via the signed `SETUP_SERVER` command. You only
need a `/board.json` if your wiring differs from the built-in defaults (§2–3).

---

## 6. Adding a new board

1. Add a profile dict to `_PROFILES` in `src/boards.py` (name,
   `default_relay_pin`, `logical_to_gpio`, `timer_id`).
2. Extend `detect_profile_name()` if the chip needs a new `sys.platform` /
   `os.uname().machine` branch.
3. Add a sample under `docs/boards/<name>.board.json` and a row to the tables
   above.
4. Add a case to `tests/test_boards.py` (`detect_profile_name` + the
   well-formed-profile check already covers new entries automatically).

Keep `src/boards.py` within the MicroPython subset: no `from __future__`,
no PEP-448 `{**d}` unpacking, no `json.dumps(sort_keys=...)`.
