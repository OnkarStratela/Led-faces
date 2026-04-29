"""Standalone green-on-dim-white flow animation for the WS2812 strip.

The whole strip stays a soft dim white at all times. A bright green wave
starts at each end and flows toward the centre, with a long fading green
tail behind each head that melts back into the dim-white background.
Green is the brightest thing on the strip; white is just a soft glow that
gives every LED some signal of life. When the two waves meet at the
centre, the green pulse melts back into white and the cycle repeats — the
LEDs never go dark.

Has nothing to do with the RFID reader; just a fun toy script.

Run on the Pi:

    sudo python3 rainbow.py             # default speed
    sudo python3 rainbow.py --speed 10  # faster
    sudo python3 rainbow.py --speed 2   # slower / chill

Press Ctrl+C to stop — the strip turns off cleanly.

LED constants are kept in sync with rfid_led.py so it Just Works on the same
hardware. To change the look, tweak `WHITE_LEVEL` (background brightness),
`GREEN_PEAK` (head brightness), or `TRAIL_LEN` (length of the green smear)
near the top of this file.
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

# Brightness of the wave-head LED, in raw RGB (0..255). Green is the
# brightest thing on the strip — keep this near 255.
GREEN_PEAK  = 255
# Brightness of the idle white background, in raw RGB (0..255). Lower than
# GREEN_PEAK so green visibly stands out against the white.
WHITE_LEVEL = 10
# Comet-tail length (LEDs). Longer tail = the green smear behind each wave
# head reaches further back toward the strip's ends.
TRAIL_LEN   = 9

# Number of fade-out frames after the waves meet at the centre, before the
# cycle restarts. Larger = longer hold + slower fade.
FADE_STEPS = 8


def fill_off(strip: PixelStrip) -> None:
    """Turn the strip fully off (used on shutdown)."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()


def fill_white(strip: PixelStrip) -> None:
    """Solid dim-white background — the idle / between-waves state."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(WHITE_LEVEL, WHITE_LEVEL, WHITE_LEVEL))
    strip.show()


def render_frame(strip: PixelStrip, head: int, fade: float = 1.0) -> None:
    """Draw a single frame.

    Background is always dim white at WHITE_LEVEL. The two wave heads (left
    at `head`, right mirrored at `N - 1 - head`) appear as full-brightness
    green over that white; each head trails a fade that blends back into
    the dim white.

    For every LED we compute distance `d` behind the nearer wave head and
    derive an "intensity" t in [0..1]:
        * t = 1 → wave head: pure green (0, GREEN_PEAK, 0)
        * t = 0 → background: dim white (WHITE_LEVEL, WHITE_LEVEL, WHITE_LEVEL)
        * 0 < t < 1 → linear blend between the two
    `fade` (0..1) globally scales t — used to melt the centre green pulse
    back into the dim white after the waves meet.
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
        # Linear blend: background (WHITE_LEVEL on all channels) → green head
        # (0, GREEN_PEAK, 0). Green is unambiguously the brightest thing.
        rb = int(WHITE_LEVEL * (1.0 - t))
        g  = int(WHITE_LEVEL + (GREEN_PEAK - WHITE_LEVEL) * t)
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
