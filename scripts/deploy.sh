#!/usr/bin/env bash
#
# Deploy Tapayoka firmware to a connected MicroPython board.
#
# The firmware is IDENTICAL for every supported board (Raspberry Pi Pico W /
# Pico 2 W, ESP32, ESP32-S3, ESP32-C3) -- the chip is auto-detected at boot by
# src/boards.py, so there is nothing board-specific to copy here.
#
# PREREQUISITE (one-time, per chip): MicroPython *with BLE/aioble* must already
# be flashed. That is a separate, chip-specific step (UF2 for the Pico, esptool
# for ESP32) -- see docs/BOARDS.md section 5. This script only copies the
# application files onto an already-running MicroPython board.
#
# Usage:
#   scripts/deploy.sh [--port PORT] [--board-config FILE] [--no-reset] [--list]
#
#   --port, -p PORT        Serial device (default: mpremote "auto").
#                          e.g. /dev/cu.usbmodem14101 (Pico),
#                               /dev/cu.usbserial-* or /dev/cu.SLAB_USBtoUART (ESP32)
#   --board-config, -b F   Copy F to the device as /board.json (per-device pin
#                          overrides; usually unnecessary -- see docs/boards/).
#   --no-reset             Do not reset the board after copying.
#   --list, -l             List connected boards and exit.
#   --help, -h             Show this help.
#
# Examples:
#   scripts/deploy.sh                                   # auto-detect port
#   scripts/deploy.sh -p /dev/cu.usbmodem14101          # Pico
#   scripts/deploy.sh -p /dev/cu.SLAB_USBtoUART \
#                     -b docs/boards/esp32s3.board.json # ESP32-S3 w/ pin map

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Resolve an mpremote invocation (PATH binary preferred, else `python3 -m`).
if command -v mpremote >/dev/null 2>&1; then
  MPR=(mpremote)
else
  MPR=(python3 -m mpremote)
fi

PORT=""
BOARD_JSON=""
RESET=1

die() { echo "error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--port)          PORT="${2:-}"; shift 2 ;;
    -b|--board-config)  BOARD_JSON="${2:-}"; shift 2 ;;
    --no-reset)         RESET=0; shift ;;
    -l|--list)          "${MPR[@]}" connect list; exit 0 ;;
    -h|--help)          sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                  die "unknown argument: $1 (try --help)" ;;
  esac
done

# `mpremote connect <PORT>` selects the device; empty PORT uses mpremote's
# built-in "auto" (works when exactly one board is attached).
CONNECT=(connect)
if [ -n "$PORT" ]; then CONNECT+=("$PORT"); else CONNECT+=(auto); fi

[ -f main.py ] || die "main.py not found (run from the repo, or it moved)"
[ -d src ]     || die "src/ not found"
if [ -n "$BOARD_JSON" ] && [ ! -f "$BOARD_JSON" ]; then
  die "board-config file not found: $BOARD_JSON"
fi

echo ">> target: ${PORT:-auto}"

# Stage src/ without __pycache__ / *.pyc so no host bytecode lands on the device.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude='__pycache__' --exclude='*.pyc' src "$STAGE/"
else
  cp -R src "$STAGE/"
  find "$STAGE/src" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$STAGE/src" -name '*.pyc' -delete 2>/dev/null || true
fi

echo ">> removing stale /src on device (ignored if absent)"
"${MPR[@]}" "${CONNECT[@]}" fs rm -r :src >/dev/null 2>&1 || true

echo ">> copying src/ -> :/src"
"${MPR[@]}" "${CONNECT[@]}" fs cp -r "$STAGE/src" :

echo ">> copying main.py -> :/main.py"
"${MPR[@]}" "${CONNECT[@]}" fs cp main.py :

if [ -n "$BOARD_JSON" ]; then
  echo ">> copying $BOARD_JSON -> :/board.json"
  "${MPR[@]}" "${CONNECT[@]}" fs cp "$BOARD_JSON" :board.json
fi

echo ">> verifying board detection"
"${MPR[@]}" "${CONNECT[@]}" exec "import sys
sys.path.insert(0, '/src')
import src.boards as b
print('   board :', b.BOARD_NAME)
print('   relay :', b.DEFAULT_RELAY_PIN)
print('   logical pins :', len(b.LOGICAL_TO_GPIO))" || die "verification failed (is MicroPython+aioble flashed?)"

if [ "$RESET" -eq 1 ]; then
  echo ">> resetting board"
  "${MPR[@]}" "${CONNECT[@]}" reset || true
fi

echo ">> done"
