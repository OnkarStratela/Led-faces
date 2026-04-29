"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

Runs the compiled `./rfid_reader` binary as a subprocess, mirrors every line of
its output to the terminal, and lights the LED strip GREEN for a short hold time
each time a NEW unique tag is reported (rfid_reader.c already filters duplicates
via its `seen_tags[]` table, so every "TAG DETECTED" line corresponds to a
genuinely new tag).
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time

from rpi_ws281x import PixelStrip, Color

# ── LED configuration (same as ../leds_on.py) ──────────────
LED_COUNT      = 19          # Number of LEDs on the strip
LED_PIN        = 12          # GPIO12 (PWM0)
LED_FREQ_HZ    = 600000      # WS2812 signal frequency
LED_DMA        = 10          # DMA channel
LED_BRIGHTNESS = 225         # 0 (off) to 255 (full brightness)
LED_INVERT     = False
LED_CHANNEL    = 0
# ────────────────────────────────────────────────────────────

# ── Colours (HEX) ──────────────────────────────────────────
GREEN_HEX = "#00FF00"
OFF_HEX   = "#000000"
# ────────────────────────────────────────────────────────────

# How long the strip stays green after the last tag detection.
GREEN_HOLD_SECONDS = 2.0

# Pattern that the C reader prints for every NEW unique tag.
TAG_LINE_RE = re.compile(r"\[RFID\] TAG DETECTED:")

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
RFID_BINARY = os.path.join(SCRIPT_DIR, "rfid_reader")


def hex_to_color(hex_code: str) -> int:
    r = int(hex_code[1:3], 16)
    g = int(hex_code[3:5], 16)
    b = int(hex_code[5:7], 16)
    return Color(r, g, b)


def fill_strip(strip: PixelStrip, color: int) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
    strip.show()


def main() -> int:
    if not os.path.isfile(RFID_BINARY) or not os.access(RFID_BINARY, os.X_OK):
        print(f"[LED-RFID] ERROR: '{RFID_BINARY}' not found or not executable.")
        print("[LED-RFID] Build it first:  ./compile.sh")
        return 1

    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
    )
    strip.begin()

    GREEN = hex_to_color(GREEN_HEX)
    OFF   = hex_to_color(OFF_HEX)
    fill_strip(strip, OFF)

    # Shared state between reader thread and LED thread.
    last_detect_ts = [0.0]
    stop_event     = threading.Event()

    def led_worker() -> None:
        is_green = False
        while not stop_event.is_set():
            green_active = (time.time() - last_detect_ts[0]) < GREEN_HOLD_SECONDS
            if green_active and not is_green:
                fill_strip(strip, GREEN)
                is_green = True
            elif not green_active and is_green:
                fill_strip(strip, OFF)
                is_green = False
            time.sleep(0.05)
        fill_strip(strip, OFF)

    led_thread = threading.Thread(target=led_worker, daemon=True)
    led_thread.start()

    print("[LED-RFID] Launching RFID reader subprocess...")
    proc = subprocess.Popen(
        [RFID_BINARY],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    def shutdown(*_args) -> None:
        if stop_event.is_set():
            return
        print("\n[LED-RFID] Shutting down...")
        stop_event.set()
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        led_thread.join(timeout=2)
        fill_strip(strip, OFF)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if TAG_LINE_RE.search(line):
                last_detect_ts[0] = time.time()
    finally:
        shutdown()

    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
