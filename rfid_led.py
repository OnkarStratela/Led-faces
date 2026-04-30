"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

Behaviour
---------
* Idle (no tags being scanned): the strip is solid WHITE.
* Every NEW unique tag reported by `rfid_reader` produces ONE visible green
  blink:
      - a very short WHITE "off-pulse" (so back-to-back blinks are visually
        distinct from one continuous green pulse),
      - then GREEN for `GREEN_HOLD_SECONDS` seconds,
      - then back to WHITE.
* If another new tag arrives while the strip is still green, the current green
  pulse is cut short and a fresh blink starts. That way, scanning N unique
  containers in quick succession produces N distinct green blinks — a visual
  counter for the operator.
* Every tag line also prints "[LED-RFID] Tags scanned: N" so the running total
  is visible in the terminal.
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

try:
    from gpiozero import PWMLED  # used for the simple GPIO13 PWM LED
except Exception:  # pragma: no cover - fall back so RFID flow still runs
    PWMLED = None  # type: ignore[assignment]

# ── LED configuration (same as ../leds_on.py) ──────────────
LED_COUNT      = 19          # Number of LEDs on the strip
LED_PIN        = 12          # GPIO12 (PWM0)
LED_FREQ_HZ    = 600000      # WS2812 signal frequency
LED_DMA        = 10          # DMA channel
LED_BRIGHTNESS = 225         # 0 (off) to 255 (full brightness)
LED_INVERT     = False
LED_CHANNEL    = 0
# ────────────────────────────────────────────────────────────

# ── Simple PWM LED on GPIO13 (independent of the WS2812 strip) ──
# Held at PWM_LED_BRIGHTNESS for the entire session, off cleanly on exit.
PWM_LED_PIN        = 13
PWM_LED_BRIGHTNESS = 0.5  # 0.0 (off) … 1.0 (full)
# ────────────────────────────────────────────────────────────

# ── Colours (HEX) ──────────────────────────────────────────
GREEN_HEX = "#00FF00"
WHITE_HEX = "#FFFFFF"
OFF_HEX   = "#000000"
# ────────────────────────────────────────────────────────────

# How long each green blink lasts after a new tag is detected.
GREEN_HOLD_SECONDS = 1.0
# Short white "off-pulse" used to visually separate consecutive green blinks
# when several tags arrive in quick succession.
WHITE_FLASH_SECONDS = 0.10

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

    # Bring up the simple GPIO13 PWM LED at full brightness immediately, so it
    # is on the moment system.sh launches this script. Failures here must NOT
    # affect the RFID / WS2812 flow.
    pwm_led = None
    if PWMLED is not None:
        try:
            pwm_led = PWMLED(PWM_LED_PIN)
            pwm_led.value = PWM_LED_BRIGHTNESS
            print(
                f"[LED-RFID] GPIO{PWM_LED_PIN} PWM LED ON at "
                f"{int(PWM_LED_BRIGHTNESS * 100)}% duty."
            )
            sys.stdout.flush()
        except Exception as exc:
            print(f"[LED-RFID] WARN: could not init GPIO{PWM_LED_PIN} PWM LED: {exc}")
            sys.stdout.flush()
            pwm_led = None
    else:
        print("[LED-RFID] WARN: gpiozero not available; skipping GPIO13 PWM LED.")
        sys.stdout.flush()

    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
    )
    strip.begin()

    GREEN = hex_to_color(GREEN_HEX)
    WHITE = hex_to_color(WHITE_HEX)
    OFF   = hex_to_color(OFF_HEX)

    # Default idle state: solid WHITE the moment the script starts.
    fill_strip(strip, WHITE)

    # One item is enqueued per new unique tag. The LED worker pops items and
    # turns them into visible green blinks.
    tag_events: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()

    def led_worker() -> None:
        """Render the WHITE-idle / GREEN-blink behaviour described in the
        module docstring."""
        # Make sure we start from a known white state.
        fill_strip(strip, WHITE)
        while not stop_event.is_set():
            # Wait for a new-tag event. Short timeout so we can periodically
            # re-check stop_event.
            try:
                tag_events.get(timeout=0.1)
            except queue.Empty:
                continue

            # New tag → produce one green blink.
            # 1. Short white off-pulse so consecutive blinks are visually
            #    distinct from a single sustained green.
            fill_strip(strip, WHITE)
            time.sleep(WHITE_FLASH_SECONDS)

            # 2. GREEN for up to GREEN_HOLD_SECONDS, but cut short and restart
            #    the blink if another tag arrives in the meantime.
            fill_strip(strip, GREEN)
            green_until = time.time() + GREEN_HOLD_SECONDS
            while not stop_event.is_set():
                remaining = green_until - time.time()
                if remaining <= 0:
                    break
                try:
                    tag_events.get(timeout=remaining)
                except queue.Empty:
                    break  # full green window elapsed with no new tags
                # Another tag arrived during the green window → restart blink.
                fill_strip(strip, WHITE)
                time.sleep(WHITE_FLASH_SECONDS)
                fill_strip(strip, GREEN)
                green_until = time.time() + GREEN_HOLD_SECONDS

            # 3. Back to idle white.
            fill_strip(strip, WHITE)

        # Shutdown: turn the strip off completely.
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
        if pwm_led is not None:
            try:
                pwm_led.value = 0.0
                pwm_led.close()
            except Exception:
                pass

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
                print(f"[LED-RFID] Tags scanned: {tag_count}")
                sys.stdout.flush()
                tag_events.put(time.time())
    finally:
        shutdown()

    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
