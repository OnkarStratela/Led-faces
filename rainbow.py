"""Just-for-fun rainbow flow for the WS2812 strip.

Runs a smooth, continuously-flowing rainbow across all LEDs on the same strip
the RFID system uses. Completely standalone — does NOT touch the RFID reader,
the C binary, or any of the production scripts.

Run it (on the Pi) with:

    sudo python3 rainbow.py            # default smooth rainbow flow
    sudo python3 rainbow.py --speed 3  # faster
    sudo python3 rainbow.py --mode pulse
    sudo python3 rainbow.py --help

`sudo` is required because the rpi_ws281x library needs root for PWM/DMA.
Press Ctrl+C to stop — the strip turns off cleanly on exit.
"""

import argparse
import signal
import sys
import time

from rpi_ws281x import PixelStrip, Color

# ── LED configuration (must match rfid_led.py / actual wiring) ──
LED_COUNT      = 19          # Number of LEDs on the strip
LED_PIN        = 12          # GPIO12 (PWM0)
LED_FREQ_HZ    = 600000      # WS2812 signal frequency
LED_DMA        = 10          # DMA channel
LED_BRIGHTNESS = 225         # 0 (off) to 255 (full brightness)
LED_INVERT     = False
LED_CHANNEL    = 0
# ────────────────────────────────────────────────────────────────


def wheel(pos: int) -> int:
    """Map 0..255 to a smooth R→G→B→R rainbow colour."""
    pos &= 255
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    if pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    pos -= 170
    return Color(0, pos * 3, 255 - pos * 3)


def make_strip() -> PixelStrip:
    strip = PixelStrip(
        LED_COUNT, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL,
    )
    strip.begin()
    return strip


def fill(strip: PixelStrip, color: int) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
    strip.show()


def mode_flow(strip: PixelStrip, speed: float, stop) -> None:
    """Each LED holds its own hue; the whole rainbow scrolls along the strip."""
    n = strip.numPixels()
    offset = 0
    # Spread one full rainbow across the strip, regardless of LED count.
    span = max(n, 1)
    while not stop():
        for i in range(n):
            hue = int((i * 256 / span) + offset) & 255
            strip.setPixelColor(i, wheel(hue))
        strip.show()
        offset = (offset + max(1, int(speed))) & 255
        time.sleep(0.02 / max(speed, 0.1))


def mode_solid(strip: PixelStrip, speed: float, stop) -> None:
    """Whole strip cycles through the rainbow as one solid colour."""
    hue = 0
    while not stop():
        fill(strip, wheel(hue))
        hue = (hue + max(1, int(speed))) & 255
        time.sleep(0.03 / max(speed, 0.1))


def mode_pulse(strip: PixelStrip, speed: float, stop) -> None:
    """Rainbow flow that breathes in and out (brightness wave)."""
    n = strip.numPixels()
    offset = 0
    t0 = time.time()
    while not stop():
        elapsed = time.time() - t0
        # Breathing factor 0.15 .. 1.0
        breath = 0.575 + 0.425 * _sine_wave(elapsed * speed * 0.6)
        for i in range(n):
            hue = int((i * 256 / max(n, 1)) + offset) & 255
            c = wheel(hue)
            r = int(((c >> 16) & 0xFF) * breath)
            g = int(((c >> 8)  & 0xFF) * breath)
            b = int((c        & 0xFF) * breath)
            strip.setPixelColor(i, Color(r, g, b))
        strip.show()
        offset = (offset + max(1, int(speed))) & 255
        time.sleep(0.025 / max(speed, 0.1))


def _sine_wave(t: float) -> float:
    """Cheap sine wave in [0, 1] without importing math."""
    import math
    return 0.5 * (1.0 + math.sin(2.0 * math.pi * t))


MODES = {
    "flow":  mode_flow,
    "solid": mode_solid,
    "pulse": mode_pulse,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WS2812 rainbow demo for the RFID strip.")
    p.add_argument(
        "--mode", choices=sorted(MODES.keys()), default="flow",
        help="Animation style (default: flow).",
    )
    p.add_argument(
        "--speed", type=float, default=1.0,
        help="Animation speed multiplier, 0.2 = slow, 5 = fast (default: 1.0).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    strip = make_strip()

    stopping = {"now": False}

    def shutdown(*_):
        if stopping["now"]:
            return
        stopping["now"] = True
        print("\n[rainbow] Stopping — turning strip off.")
        fill(strip, Color(0, 0, 0))

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[rainbow] mode={args.mode}  speed={args.speed}  "
          f"LEDs={LED_COUNT} on GPIO{LED_PIN}.  Ctrl+C to stop.")

    try:
        MODES[args.mode](strip, args.speed, lambda: stopping["now"])
    finally:
        fill(strip, Color(0, 0, 0))

    return 0


if __name__ == "__main__":
    sys.exit(main())
