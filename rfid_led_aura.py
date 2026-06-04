"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

"AURA" — a premium, minimal lighting design for a matte-black bin chute.

Concept
-------
The strip frames the chute opening. Against matte black, restrained cool-white
light reads as expensive and calm, so the idle state is deliberately quiet and
the only "loud" moment is the confirmation when an item is dropped in.

* IDLE (standby): the whole ring "breathes" — a slow, soft cool-white glow that
  swells and settles on a gentle ~4 second cycle. It says "ready / alive"
  without ever looking like a blinking indicator light.
* ITEM DETECTED (a NEW unique tag from `rfid_reader`): a confident "bloom"
  blooms outward from the CENTRE of the strip to both edges — a bright white
  wavefront led by a subtle teal edge — holds at full brightness for a beat as
  a clear "accepted" acknowledgement, then eases smoothly back down into the
  breathing standby.
* If several items are dropped in quick succession, each one re-triggers its
  own bloom, so the operator/user always gets one crisp confirmation per item.
* Every tag line also prints "[LED-RFID] Tags scanned: N" so the running total
  is visible in the terminal.

This module keeps exactly the same RFID plumbing, GPIO13 PWM LED handling and
clean-shutdown behaviour as the other LED scripts; only the WS2812 visuals
differ.
"""

import atexit
import math
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
LED_BRIGHTNESS = 255         # 0 (off) to 255 (full brightness)
LED_INVERT     = False
LED_CHANNEL    = 0
# ────────────────────────────────────────────────────────────

# ── Simple PWM LED on GPIO13 (independent of the WS2812 strip) ──
# Held at PWM_LED_BRIGHTNESS for the entire session, off cleanly on exit.
PWM_LED_PIN        = 13
PWM_LED_BRIGHTNESS = 1.0  # 0.0 (off) … 1.0 (full)
# ────────────────────────────────────────────────────────────

# ── Palette (RGB tuples; brightness is shaped in software) ──
# Cool white reads as premium against matte black; teal is the accent that
# leads the "accepted" bloom.
WHITE_RGB  = (210, 226, 255)   # soft cool white
ACCENT_RGB = (0, 200, 178)     # refined teal
# ────────────────────────────────────────────────────────────

# ── Idle "breathing" standby ───────────────────────────────
BREATH_MIN_LEVEL = 0.05   # dimmest point of the breath (barely lit)
BREATH_MAX_LEVEL = 0.32   # brightest point of the idle breath
BREATH_PERIOD    = 4.0    # seconds for one full inhale+exhale
FRAME_SECONDS    = 0.03   # ~33 fps animation step
# ────────────────────────────────────────────────────────────

# ── "Accepted" bloom from the centre ───────────────────────
BLOOM_BG_LEVEL   = 0.06   # rest of the ring while the bloom expands
BLOOM_BODY_LEVEL = 0.85   # filled-in pixels behind the wavefront
BLOOM_STEP_SEC   = 0.028  # time between bloom expansion frames
BLOOM_HOLD_SEC   = 0.28   # hold at full brightness once fully bloomed
BLOOM_FADE_SEC   = 0.55   # ease back down into the breathing baseline
# ────────────────────────────────────────────────────────────

# Pattern that the C reader prints for every NEW unique tag.
TAG_LINE_RE = re.compile(r"\[RFID\] TAG DETECTED:")

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
RFID_BINARY = os.path.join(SCRIPT_DIR, "rfid_reader")


def _force_gpio_low(pin: int) -> None:
    """Force `pin` to OUTPUT driving LOW at the SoC pin-mux register level.

    Last-resort cleanup for the GPIO13 PWM LED. When gpiozero / lgpio
    releases the GPIO chardev claim on shutdown, on Pi OS Bookworm the
    SoC pin-mux register is sometimes left configured as OUTPUT-HIGH
    (or floats and is pulled HIGH by the LED circuit) — which makes the
    LED snap to maximum brightness as the program exits.

    `pinctrl` (preinstalled on Pi OS Bookworm; `raspi-gpio` on older
    releases) writes the SoC pin-mux register directly. That state
    persists after our Python process exits, so the LED stays off until
    a reboot or another GPIO library reclaims the pin.
    """
    for cmd in (
        ["pinctrl", "set", str(pin), "op", "dl"],
        ["raspi-gpio", "set", str(pin), "op", "dl"],
    ):
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return
        except Exception:
            continue


def scale_rgb(rgb, level: float) -> int:
    """Return a Color for `rgb` dimmed by `level` (0.0..1.0), shaped in software."""
    level = 0.0 if level < 0.0 else 1.0 if level > 1.0 else level
    r, g, b = rgb
    return Color(int(r * level), int(g * level), int(b * level))


def lerp_rgb(a, b, t: float):
    """Linear blend between two RGB tuples (t in 0.0..1.0)."""
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def fill_color(strip: PixelStrip, color: int) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
    strip.show()


def breathe_until_tag(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Run the soft cool-white breathing standby until a tag arrives.

    Returns True if a new-tag event arrived (already consumed from the queue,
    so the caller should render an "accepted" bloom). Returns False if
    `stop_event` was set instead.
    """
    omega = (2.0 * math.pi) / BREATH_PERIOD
    span = BREATH_MAX_LEVEL - BREATH_MIN_LEVEL
    t0 = time.time()
    while not stop_event.is_set():
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            pass
        # Smooth sinusoidal swell/settle between the min and max levels.
        phase = (time.time() - t0) * omega
        level = BREATH_MIN_LEVEL + span * (0.5 - 0.5 * math.cos(phase))
        fill_color(strip, scale_rgb(WHITE_RGB, level))
        time.sleep(FRAME_SECONDS)
    return False


def accepted_bloom(strip: PixelStrip, stop_event: "threading.Event") -> None:
    """Bloom outward from the centre to both edges as an "item accepted" cue.

    A bright white body grows from the middle, led by a subtle teal wavefront;
    once the ring is full it holds briefly, then eases back down to the
    breathing baseline so the transition into standby is seamless.
    """
    n = strip.numPixels()
    center = (n - 1) / 2.0
    max_dist = center  # symmetric strip → furthest pixel distance from centre
    edge_rgb = lerp_rgb(WHITE_RGB, ACCENT_RGB, 0.55)

    radius = 0.0
    while radius <= max_dist + 1.0:
        if stop_event.is_set():
            return
        for i in range(n):
            dist = abs(i - center)
            if dist <= radius:
                # Leading edge gets the teal-tinted wavefront; the filled body
                # behind it settles to bright white.
                if radius - dist <= 1.0:
                    strip.setPixelColor(i, scale_rgb(edge_rgb, 1.0))
                else:
                    strip.setPixelColor(i, scale_rgb(WHITE_RGB, BLOOM_BODY_LEVEL))
            else:
                strip.setPixelColor(i, scale_rgb(WHITE_RGB, BLOOM_BG_LEVEL))
        strip.show()
        time.sleep(BLOOM_STEP_SEC)
        radius += 1.0

    # Hold the full, bright ring for a confident beat.
    fill_color(strip, scale_rgb(WHITE_RGB, 1.0))
    hold_until = time.time() + BLOOM_HOLD_SEC
    while time.time() < hold_until:
        if stop_event.is_set():
            return
        time.sleep(FRAME_SECONDS)

    # Ease from full brightness down to the breathing baseline.
    steps = max(1, int(BLOOM_FADE_SEC / FRAME_SECONDS))
    for s in range(steps + 1):
        if stop_event.is_set():
            return
        t = s / steps
        level = 1.0 + (BREATH_MIN_LEVEL - 1.0) * t
        fill_color(strip, scale_rgb(WHITE_RGB, level))
        time.sleep(FRAME_SECONDS)


def main() -> int:
    if not os.path.isfile(RFID_BINARY) or not os.access(RFID_BINARY, os.X_OK):
        print(f"[LED-RFID] ERROR: '{RFID_BINARY}' not found or not executable.")
        print("[LED-RFID] Build it first:  ./compile.sh")
        return 1

    # Bring up the simple GPIO13 PWM LED at full brightness immediately, so it
    # is on the moment system.sh launches this script. Failures here must NOT
    # affect the RFID / WS2812 flow.
    #
    # Register the SoC-level pin-LOW fallback BEFORE creating PWMLED so that
    # in atexit's LIFO order our handler runs AFTER gpiozero's own cleanup.
    # That way, even on a crash / unexpected exit, the LED ends up off.
    atexit.register(_force_gpio_low, PWM_LED_PIN)

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

    OFF = Color(0, 0, 0)

    # Default idle state: dim baseline glow (the breathing loop takes over once
    # the LED worker thread starts).
    fill_color(strip, scale_rgb(WHITE_RGB, BREATH_MIN_LEVEL))

    # One item is enqueued per new unique tag. The LED worker pops items and
    # turns them into "accepted" blooms.
    tag_events: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()

    def led_worker() -> None:
        """Breathing standby, interrupted by an "accepted" bloom per item."""
        while not stop_event.is_set():
            got_tag = breathe_until_tag(strip, stop_event, tag_events)
            if not got_tag:
                break  # stop_event was set
            accepted_bloom(strip, stop_event)
            # Back to the breathing standby (next loop iteration).

        # Shutdown: turn the strip off completely.
        fill_color(strip, OFF)

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
        fill_color(strip, OFF)
        if pwm_led is not None:
            # Step 1: tell gpiozero to drive the line LOW (PWM duty 0%).
            try:
                pwm_led.value = 0.0
                time.sleep(0.05)
            except Exception:
                pass
            # Step 2: release the gpiozero / lgpio chardev claim so the
            # SoC-level pin-mux write below isn't fighting an active claim.
            try:
                pwm_led.close()
            except Exception:
                pass
        # Step 3: hardware-level final LOW. This writes the SoC pin-mux
        # register directly and persists after our process exits, even
        # if a later atexit handler (e.g. gpiozero's) would have left
        # the pin floating / latched HIGH.
        _force_gpio_low(PWM_LED_PIN)

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
