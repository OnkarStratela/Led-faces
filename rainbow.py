"""Standalone rainbow animation for the WS2812 strip.

Plays a smoothly-flowing rainbow across all LEDs, forever, until you press
Ctrl+C. Has nothing to do with the RFID reader — just a fun toy script.

Run on the Pi:

    sudo python3 rainbow.py            # default speed
    sudo python3 rainbow.py --speed 8  # faster flow
    sudo python3 rainbow.py --mode solid   # whole strip pulses one colour
    sudo python3 rainbow.py --mode chase   # rainbow flows along the strip (default)

Press Ctrl+C to stop — the strip turns off cleanly.

LED constants are kept in sync with rfid_led.py so it Just Works on the same
hardware.
"""

import argparse
import signal
import sys
import time

from rpi_ws281x import PixelStrip, Color

# ── LED configuration (must match rfid_led.py) ─────────────
LED_COUNT      = 19          # Number of LEDs on the strip
LED_PIN        = 12          # GPIO12 (PWM0)
LED_FREQ_HZ    = 600000      # WS2812 signal frequency
LED_DMA        = 10          # DMA channel
LED_BRIGHTNESS = 225         # 0 (off) to 255 (full brightness)
LED_INVERT     = False
LED_CHANNEL    = 0
# ────────────────────────────────────────────────────────────


def wheel(pos: int) -> int:
    """Map 0..255 to a rainbow colour (R → G → B → R)."""
    pos = pos & 255
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    if pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    pos -= 170
    return Color(0, pos * 3, 255 - pos * 3)


def fill_off(strip: PixelStrip) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def rainbow_chase(strip: PixelStrip, speed: int) -> None:
    """Each LED gets its own hue, and the whole pattern slides along the
    strip — looks like a flowing rainbow ribbon."""
    delay = max(1, 30 - speed * 2) / 1000.0  # speed 1 → 28 ms, speed 14 → 2 ms
    j = 0
    while True:
        for i in range(strip.numPixels()):
            strip.setPixelColor(
                i,
                wheel((int(i * 256 / strip.numPixels()) + j) & 255),
            )
        strip.show()
        time.sleep(delay)
        j = (j + 1) & 255


def rainbow_solid(strip: PixelStrip, speed: int) -> None:
    """Whole strip pulses through the rainbow, all LEDs the same colour at
    any moment."""
    delay = max(1, 30 - speed * 2) / 1000.0
    j = 0
    while True:
        c = wheel(j & 255)
        for i in range(strip.numPixels()):
            strip.setPixelColor(i, c)
        strip.show()
        time.sleep(delay)
        j = (j + 1) & 255


def main() -> int:
    parser = argparse.ArgumentParser(description="WS2812 rainbow toy.")
    parser.add_argument(
        "--mode",
        choices=("chase", "solid"),
        default="chase",
        help="chase = flowing rainbow ribbon (default); solid = whole strip pulses one colour at a time",
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=6,
        help="1 (slow) to 14 (fast). Default: 6",
    )
    args = parser.parse_args()

    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
    )
    strip.begin()

    def shutdown(*_args) -> None:
        print("\n[Rainbow] Stopping, turning LEDs off...")
        fill_off(strip)
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[Rainbow] mode={args.mode} speed={args.speed} — Ctrl+C to stop")
    try:
        if args.mode == "chase":
            rainbow_chase(strip, args.speed)
        else:
            rainbow_solid(strip, args.speed)
    finally:
        fill_off(strip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
