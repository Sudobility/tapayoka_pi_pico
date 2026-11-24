#!/usr/bin/env bash
#
# Flash MicroPython (with BLE) onto an ESP32 / ESP32-S3 / ESP32-C3, then
# optionally install aioble and deploy the Tapayoka firmware.
#
# This is "Phase A" (one-time, per chip) from docs/BOARDS.md section 5. After it
# runs, the board runs bare MicroPython; use scripts/deploy.sh (or --deploy
# here) to copy the application files -- those are identical for every board.
#
# For the Raspberry Pi Pico there is no esptool step: hold BOOTSEL, plug in USB,
# and drag the .uf2 onto the RPI-RP2 drive. Then run scripts/deploy.sh.
#
# Usage:
#   scripts/flash_esp32.sh --variant <esp32|esp32s3|esp32c3> \
#                          --firmware <FILE.bin> \
#                          [--port PORT] [--baud BAUD] \
#                          [--no-erase] [--install-aioble] [--deploy] \
#                          [--board-config FILE]
#
#   --variant, -v     Chip variant. Sets the correct flash offset:
#                       esp32   -> 0x1000
#                       esp32s3 -> 0x0
#                       esp32c3 -> 0x0
#   --firmware, -f    Path to the MicroPython .bin (download the BLE build for
#                     your variant from https://micropython.org/download/).
#   --port, -p        Serial device (e.g. /dev/cu.usbserial-0001,
#                     /dev/cu.SLAB_USBtoUART). Default: esptool auto-detect.
#   --baud, -b        Write baud rate (default 460800).
#   --no-erase        Skip `erase_flash` (erase is on by default, recommended).
#   --install-aioble  After flashing + reset, `mip install aioble` (needs net on
#                     the host; skip if your firmware already bundles aioble).
#   --deploy          After flashing, run scripts/deploy.sh for this port.
#   --board-config F  Passed through to scripts/deploy.sh (implies --deploy).
#   --help, -h        Show this help.
#
# Examples:
#   scripts/flash_esp32.sh -v esp32s3 -f ~/Downloads/ESP32_GENERIC_S3-20250911-v1.29.0.bin
#   scripts/flash_esp32.sh -v esp32c3 -f fw.bin -p /dev/cu.usbserial-10 \
#                          --install-aioble --deploy
#
# Note: most dev boards auto-enter the bootloader via DTR/RTS. If flashing
# fails to start, hold the BOOT button while esptool connects (release after).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

die() { echo "error: $*" >&2; exit 1; }

VARIANT=""
FIRMWARE=""
PORT=""
BAUD="460800"
ERASE=1
INSTALL_AIOBLE=0
DEPLOY=0
BOARD_JSON=""

while [ $# -gt 0 ]; do
  case "$1" in
    -v|--variant)       VARIANT="${2:-}"; shift 2 ;;
    -f|--firmware)      FIRMWARE="${2:-}"; shift 2 ;;
    -p|--port)          PORT="${2:-}"; shift 2 ;;
    -b|--baud)          BAUD="${2:-}"; shift 2 ;;
    --no-erase)         ERASE=0; shift ;;
    --install-aioble)   INSTALL_AIOBLE=1; shift ;;
    --deploy)           DEPLOY=1; shift ;;
    --board-config)     BOARD_JSON="${2:-}"; DEPLOY=1; shift 2 ;;
    -h|--help)          sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                  die "unknown argument: $1 (try --help)" ;;
  esac
done

case "$VARIANT" in
  esp32)   OFFSET="0x1000" ;;
  esp32s3) OFFSET="0x0" ;;
  esp32c3) OFFSET="0x0" ;;
  "")      die "--variant is required (esp32 | esp32s3 | esp32c3)" ;;
  *)       die "unknown --variant: $VARIANT (expected esp32 | esp32s3 | esp32c3)" ;;
esac

[ -n "$FIRMWARE" ] || die "--firmware <FILE.bin> is required"
[ -f "$FIRMWARE" ] || die "firmware file not found: $FIRMWARE"

# Resolve an esptool invocation (only needed once we are actually flashing).
if command -v esptool.py >/dev/null 2>&1; then
  ESPTOOL=(esptool.py)
elif command -v esptool >/dev/null 2>&1; then
  ESPTOOL=(esptool)
elif python3 -c "import esptool" >/dev/null 2>&1; then
  ESPTOOL=(python3 -m esptool)
else
  die "esptool not found. Install it with:  pip install esptool"
fi

# Resolve an mpremote invocation (for reset / aioble install).
if command -v mpremote >/dev/null 2>&1; then
  MPR=(mpremote)
else
  MPR=(python3 -m mpremote)
fi
MPR_CONNECT=(connect)
if [ -n "$PORT" ]; then MPR_CONNECT+=("$PORT"); else MPR_CONNECT+=(auto); fi

# Common esptool port args.
ESP_PORT=()
if [ -n "$PORT" ]; then ESP_PORT=(--port "$PORT"); fi

echo ">> variant : $VARIANT  (flash offset $OFFSET)"
echo ">> firmware: $FIRMWARE"
echo ">> port    : ${PORT:-auto}"

if [ "$ERASE" -eq 1 ]; then
  echo ">> erasing flash"
  "${ESPTOOL[@]}" --chip "$VARIANT" "${ESP_PORT[@]}" erase_flash
fi

echo ">> writing firmware"
"${ESPTOOL[@]}" --chip "$VARIANT" "${ESP_PORT[@]}" --baud "$BAUD" \
  write_flash -z "$OFFSET" "$FIRMWARE"

echo ">> resetting into MicroPython"
# Give the board a moment to boot after the esptool-triggered reset.
"${MPR[@]}" "${MPR_CONNECT[@]}" reset || true

if [ "$INSTALL_AIOBLE" -eq 1 ]; then
  echo ">> installing aioble (mip)"
  "${MPR[@]}" "${MPR_CONNECT[@]}" mip install aioble \
    || die "mip install aioble failed (needs host network + a booted board)"
fi

echo ">> verifying MicroPython + BLE"
"${MPR[@]}" "${MPR_CONNECT[@]}" exec "import sys
print('   port says:', sys.platform, '-', sys.version)
try:
    import aioble
    print('   aioble: OK')
except Exception as e:
    print('   aioble: MISSING -', e, '(re-run with --install-aioble, or use a BLE build)')" \
  || die "could not talk to the board over MicroPython after flashing"

if [ "$DEPLOY" -eq 1 ]; then
  echo ">> deploying firmware"
  DEPLOY_ARGS=()
  if [ -n "$PORT" ]; then DEPLOY_ARGS+=(--port "$PORT"); fi
  if [ -n "$BOARD_JSON" ]; then DEPLOY_ARGS+=(--board-config "$BOARD_JSON"); fi
  "$REPO_ROOT/scripts/deploy.sh" "${DEPLOY_ARGS[@]}"
fi

echo ">> done"
