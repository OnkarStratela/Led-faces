"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

Runs the compiled `./rfid_reader` binary as a subprocess, mirrors every line of
its output to the terminal, keeps the strip a steady WHITE while idle, and
fires a fast burst of GREEN blinks each time a NEW unique tag is reported
(rfid_reader.c already filters duplicates via its `seen_tags[]` table, so every
"TAG DETECTED" line corresponds to a genuinely new tag). A running count of
total unique tags is printed alongside each detection.
"""

import os
import queue
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
WHITE_HEX = "#FFFFFF"        # Idle colour
GREEN_HEX = "#00FF00"        # New-tag flash colour
OFF_HEX   = "#000000"
# ────────────────────────────────────────────────────────────

# Per-tag blink burst: how many quick green flashes and how fast.
BLINK_COUNT       = 4        # number of green flashes per new tag
BLINK_ON_SECONDS  = 0.08     # green ON duration
BLINK_OFF_SECONDS = 0.07     # back-to-white duration between flashes

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

    WHITE = hex_to_color(WHITE_HEX)
    GREEN = hex_to_color(GREEN_HEX)
    OFF   = hex_to_color(OFF_HEX)
    fill_strip(strip, WHITE)

    stop_event   = threading.Event()
    tag_events: "queue.Queue[float]" = queue.Queue()

    def led_worker() -> None:
        # Idle = solid white.
        fill_strip(strip, WHITE)
        while not stop_event.is_set():
            try:
                tag_events.get(timeout=0.1)
            except queue.Empty:
                continue

            # Fast green blink burst, snapping back to white between flashes.
            for _ in range(BLINK_COUNT):
                if stop_event.is_set():
                    break
                fill_strip(strip, GREEN)
                time.sleep(BLINK_ON_SECONDS)
                fill_strip(strip, WHITE)
                time.sleep(BLINK_OFF_SECONDS)
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

    tag_count = 0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if TAG_LINE_RE.search(line):
                tag_count += 1
                print(f"[LED-RFID] >>> Total unique tags scanned: {tag_count}",
                      flush=True)
                tag_events.put(time.time())
    finally:
        shutdown()

    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
