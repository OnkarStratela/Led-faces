"""Standalone green-flow animation for the WS2812 strip.

A green wave starts at each end of the strip and flows toward the center.
The two waves meet in the middle, briefly hold, fade out, and the cycle
repeats — forever, until you press Ctrl+C.

Has nothing to do with the RFID reader; just a fun toy script.

Run on the Pi:

    sudo python3 rainbow.py             # default speed
    sudo python3 rainbow.py --speed 10  # faster
    sudo python3 rainbow.py --speed 2   # slower / chill

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

# How bright each wave's head LED is (0..255), and how many LEDs trail
# behind it (the comet tail). The tail fades linearly from peak → 0.
GREEN_PEAK = 255
TRAIL_LEN  = 5

# Number of fade-out frames after the waves meet at the center, before the
# cycle restarts. Larger = longer hold + slower fade.
FADE_STEPS = 8


def fill_off(strip: PixelStrip) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def render_frame(strip: PixelStrip, head: int, fade: float = 1.0) -> None:
    """Draw a single frame.

    `head` is the index of the LEFT wave's head (it advances 0 → center).
    The right wave is mirrored so its head is at `N - 1 - head`.

    For each LED we compute its distance behind the nearer wave head; LEDs
    closer to a head are brighter, LEDs further back are dimmer. LEDs in
    front of both heads (the "untouched" middle gap) stay off.

    `fade` (0..1) globally scales brightness — used to fade the meeting
    pulse out at the end of each cycle.
    """
    n = strip.numPixels()
    right_head = n - 1 - head
    big = 10 ** 6  # sentinel: "this LED is in front of this head, ignore"
    for i in range(n):
        d_left  = head - i       if i <= head       else big
        d_right = i - right_head if i >= right_head else big
        d = min(d_left, d_right)
        if d <= TRAIL_LEN:
            g = int(GREEN_PEAK * (1.0 - d / (TRAIL_LEN + 1)) * fade)
            if g < 0:
                g = 0
            strip.setPixelColor(i, Color(0, g, 0))
        else:
            strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def green_flow(strip: PixelStrip, speed: int) -> None:
    """Endless animation: waves converge from both ends → fade → repeat."""
    n = strip.numPixels()
    half = (n + 1) // 2          # frames for a head to reach the centre
    delay = max(1, 30 - speed * 2) / 1000.0   # speed 1 → 28 ms, 14 → 2 ms

    while True:
        # Phase 1: heads advance, one LED per frame, toward the centre.
        for head in range(half):
            render_frame(strip, head)
            time.sleep(delay)
        # Phase 2: hold + fade after the waves meet at the centre.
        for k in range(FADE_STEPS):
            render_frame(strip, half - 1, fade=1.0 - (k + 1) / FADE_STEPS)
            time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="WS2812 green-flow toy: waves converge from both ends.",
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
        print("\n[GreenFlow] Stopping, turning LEDs off...")
        fill_off(strip)
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[GreenFlow] speed={args.speed} — Ctrl+C to stop")
    try:
        green_flow(strip, args.speed)
    finally:
        fill_off(strip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
