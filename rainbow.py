"""Standalone green-on-white flow animation for the WS2812 strip.

The whole strip stays solid white at all times. A green wave starts at each
end and flows toward the centre; behind each wave head a short green tail
fades smoothly back into the white background. When the two waves meet at
the centre, the green fades back into white and the cycle repeats — the
LEDs never go dark.

Has nothing to do with the RFID reader; just a fun toy script.

Run on the Pi:

    sudo python3 rainbow.py             # default speed
    sudo python3 rainbow.py --speed 10  # faster
    sudo python3 rainbow.py --speed 2   # slower / chill

Press Ctrl+C to stop — the strip turns off cleanly.

LED constants are kept in sync with rfid_led.py so it Just Works on the same
hardware. (Note: with all 19 LEDs on white, current draw is higher than the
old all-off animation — make sure your 5 V supply can handle ~1 A.)
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
    """Turn the strip fully off (used on shutdown)."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def fill_white(strip: PixelStrip) -> None:
    """Solid white background — the idle / between-waves state."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(255, 255, 255))
    strip.show()


def render_frame(strip: PixelStrip, head: int, fade: float = 1.0) -> None:
    """Draw a single frame.

    Background is always white. The two wave heads (left at `head`, right
    mirrored at `N - 1 - head`) appear as pure green over that white, and
    each head trails a short fade back into white.

    For every LED we compute distance `d` behind the nearer wave head and
    derive an "intensity" t in [0..1]:
        * t = 1 → fully green (R = B = 0)
        * t = 0 → fully white (R = G = B = 255)
        * in between, linearly blend white → green by reducing R and B
    `fade` (0..1) scales t globally — used to fade the green back into
    white after the waves meet at the centre.
    """
    n = strip.numPixels()
    right_head = n - 1 - head
    big = 10 ** 6  # sentinel: "this LED is in front of this head, ignore"
    for i in range(n):
        d_left  = head - i       if i <= head       else big
        d_right = i - right_head if i >= right_head else big
        d = min(d_left, d_right)
        if d <= TRAIL_LEN:
            t = (1.0 - d / (TRAIL_LEN + 1)) * fade
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
        else:
            t = 0.0
        # white → green blend: reduce R and B as t grows, keep G at peak.
        rb = int(255 * (1.0 - t))
        g  = GREEN_PEAK
        strip.setPixelColor(i, Color(rb, g, rb))
    strip.show()


def green_flow(strip: PixelStrip, speed: int) -> None:
    """Endless animation: white background, with green waves converging
    from both ends and fading back into white at the centre."""
    n = strip.numPixels()
    half = (n + 1) // 2          # frames for a head to reach the centre
    delay = max(1, 30 - speed * 2) / 1000.0   # speed 1 → 28 ms, 14 → 2 ms

    # Start in the idle white state so the strip is visibly "on" right away.
    fill_white(strip)

    while True:
        # Phase 1: heads advance, one LED per frame, toward the centre.
        for head in range(half):
            render_frame(strip, head)
            time.sleep(delay)
        # Phase 2: green pulse at the centre fades back into solid white.
        for k in range(FADE_STEPS):
            render_frame(strip, half - 1, fade=1.0 - (k + 1) / FADE_STEPS)
            time.sleep(delay)
        # Make sure we're at exactly solid white before the next wave starts
        # (compensates for any rounding error in the fade above).
        fill_white(strip)


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
