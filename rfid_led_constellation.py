"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

"Constellation" — a starlight-twinkle idle with a green "supernova" scan, made
for a matte-black bin chute.

Rationale
---------
The bin is matte black, so the IDLE background is left OFF. Instead of a single
moving light, the strip becomes a quiet STARFIELD: random cool-white points
softly twinkle in and out at varying speeds and peak brightness — like a
luxury starlight headliner. It is calm, organic and reads as premium against
black, while being clearly distinct from the comet/wave modes.

The scan acknowledgment is a matching green SUPERNOVA: the whole field flashes
to full green, holds, then SHATTERS — every pixel fading out at its own random
rate — before the white starfield returns.

Two hard constraints are respected:
  1. Everything runs at full brightness. The WS2812 master brightness stays at
     255 and the GPIO13 PWM LED at 1.0; the twinkle and fades are shaped purely
     by per-pixel COLOUR, never by dimming the hardware. Star peaks reach pure
     white (255, 255, 255) and the supernova peaks at pure green.
  2. A scan is always GREEN (#00FF00); only its SHAPE/animation is unique.

Behaviour
---------
* Idle (no tags being scanned): matte-black strip with cool-white stars softly
  twinkling in and out at random, looping forever.
* Every NEW unique tag reported by `rfid_reader` produces ONE green supernova:
  a full-green flash that holds, then shatters into per-pixel fades back to
  black.
* If another new tag arrives during a supernova, it is cut short and a fresh
  one starts. That way, scanning N unique containers in quick succession
  produces N distinct green supernovas — a visual counter for the operator.
* Every tag line also prints "[LED-RFID] Tags scanned: N" so the running total
  is visible in the terminal.
"""

import atexit
import math
import os
import queue
import random
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

# ── Colours (HEX) ──────────────────────────────────────────
GREEN_HEX = "#00FF00"
WHITE_HEX = "#FFFFFF"
OFF_HEX   = "#000000"
# ────────────────────────────────────────────────────────────

# ── Starfield (idle) tuning ────────────────────────────────
# Frame interval for the twinkle animation.
STAR_FRAME_SECONDS = 0.03
# Max number of stars lit at the same time.
STAR_MAX_ACTIVE = 8
# Per-frame probability of igniting a new star (while below STAR_MAX_ACTIVE).
STAR_SPAWN_CHANCE = 0.35
# Each star rises then falls over a random lifetime in this range (seconds).
STAR_MIN_LIFETIME = 0.9
STAR_MAX_LIFETIME = 2.2
# Each star peaks at a random brightness in this range (1.0 = full white).
STAR_MIN_PEAK = 0.45
STAR_MAX_PEAK = 1.0
# Cool-white bias: blue channel lingers a touch over red/green for an icy
# star. 1.0 = neutral white; lower = cooler.
STAR_COOL_EXPONENT = 0.78
# ────────────────────────────────────────────────────────────

# ── Green supernova (scan acknowledgment) tuning ───────────
# Seconds to flash up to full green.
NOVA_FLASH_SECONDS = 0.12
# Seconds the fully-green strip is held at the peak.
NOVA_HOLD_SECONDS = 0.35
# Per-pixel shatter fade-out durations are drawn from this range (seconds).
NOVA_FADE_MIN_SECONDS = 0.30
NOVA_FADE_MAX_SECONDS = 0.95
# Frame interval for the supernova.
NOVA_FRAME_SECONDS = 0.02
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


def hex_to_color(hex_code: str) -> int:
    r = int(hex_code[1:3], 16)
    g = int(hex_code[3:5], 16)
    b = int(hex_code[5:7], 16)
    return Color(r, g, b)


def fill_strip(strip: PixelStrip, color: int) -> None:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
    strip.show()


def _ease(x: float) -> float:
    """Smoothstep easing (0→1) with zero velocity at both ends."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def idle_constellation_until_tag(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Run the cool-white starfield twinkle over an OFF strip until a tag.

    Random pixels ignite and softly rise-then-fall over random lifetimes, so
    the matte-black strip looks like a calm field of stars. Between every frame
    we poll for a new tag event.

    Returns True if a new-tag event arrived (consumed from the queue, so the
    caller should render a green supernova). Returns False if `stop_event` was
    set.
    """
    n = strip.numPixels()
    if n <= 0:
        return False

    active = [False] * n
    age = [0.0] * n
    lifetime = [0.0] * n
    peak = [0.0] * n

    while not stop_event.is_set():
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            pass

        # Maybe ignite a new star on a currently-dark pixel.
        if sum(active) < STAR_MAX_ACTIVE and random.random() < STAR_SPAWN_CHANCE:
            dark = [i for i in range(n) if not active[i]]
            if dark:
                i = random.choice(dark)
                active[i] = True
                age[i] = 0.0
                lifetime[i] = random.uniform(STAR_MIN_LIFETIME, STAR_MAX_LIFETIME)
                peak[i] = random.uniform(STAR_MIN_PEAK, STAR_MAX_PEAK)

        # Advance and render every pixel.
        for i in range(n):
            if not active[i]:
                strip.setPixelColor(i, 0)
                continue
            age[i] += STAR_FRAME_SECONDS
            if age[i] >= lifetime[i]:
                active[i] = False
                strip.setPixelColor(i, 0)
                continue
            # Smooth rise-then-fall over the star's lifetime.
            shape = math.sin(math.pi * age[i] / lifetime[i])
            intensity = peak[i] * shape
            r = int(round(255 * intensity))
            g = r
            b = int(round(255 * (intensity ** STAR_COOL_EXPONENT)))
            strip.setPixelColor(i, Color(r, g, b))

        strip.show()
        time.sleep(STAR_FRAME_SECONDS)

    return False


def green_supernova_until_idle(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Render the green "supernova" scan acknowledgment, looping on retriggers.

    One supernova = a full-green flash, a brief hold, then a SHATTER where each
    pixel fades to black at its own random rate. If a new tag arrives at any
    point, the current supernova is cut short and a fresh one starts, so rapid
    scans produce distinct supernovas.

    Returns True when it finishes cleanly (caller resumes the starfield), or
    False if `stop_event` was set.
    """
    n = strip.numPixels()
    if n <= 0:
        return True

    def _new_tag() -> bool:
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            return False

    while not stop_event.is_set():
        retrigger = False

        # Phase 1 — flash up to full green.
        start = time.monotonic()
        while True:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            e = (time.monotonic() - start) / NOVA_FLASH_SECONDS
            if e >= 1.0:
                break
            level = _ease(e)
            fill_strip(strip, Color(0, int(round(255 * level)), 0))
            time.sleep(NOVA_FRAME_SECONDS)
        if retrigger:
            continue

        # Phase 2 — hold at full green.
        fill_strip(strip, Color(0, 255, 0))
        hold_until = time.monotonic() + NOVA_HOLD_SECONDS
        while time.monotonic() < hold_until:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            time.sleep(NOVA_FRAME_SECONDS)
        if retrigger:
            continue

        # Phase 3 — shatter: each pixel fades to black at its own random rate.
        fade_dur = [
            random.uniform(NOVA_FADE_MIN_SECONDS, NOVA_FADE_MAX_SECONDS)
            for _ in range(n)
        ]
        start = time.monotonic()
        while True:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            t = time.monotonic() - start
            all_dark = True
            for i in range(n):
                if t >= fade_dur[i]:
                    strip.setPixelColor(i, 0)
                else:
                    level = 1.0 - _ease(t / fade_dur[i])
                    all_dark = False
                    strip.setPixelColor(i, Color(0, int(round(255 * level)), 0))
            strip.show()
            if all_dark:
                break
            time.sleep(NOVA_FRAME_SECONDS)
        if retrigger:
            continue

        # Clean finish: strip fully OFF, then back to the idle starfield.
        fill_strip(strip, 0)
        return True

    return False


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

    GREEN = hex_to_color(GREEN_HEX)
    WHITE = hex_to_color(WHITE_HEX)
    OFF   = hex_to_color(OFF_HEX)

    # Default idle state: strip OFF (the starfield takes over once the LED
    # worker thread starts).
    fill_strip(strip, OFF)

    # One item is enqueued per new unique tag. The LED worker pops items and
    # turns them into visible green supernovas.
    tag_events: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()

    def led_worker() -> None:
        """Render the constellation idle / GREEN-supernova behaviour described
        in the module docstring."""
        while not stop_event.is_set():
            # Idle: run the starfield over an OFF strip until a new tag arrives
            # (or we are told to stop).
            got_tag = idle_constellation_until_tag(
                strip, stop_event, tag_events
            )
            if not got_tag:
                break  # stop_event was set

            # New tag → green supernova acknowledgment (flash → hold →
            # shatter), retriggering on additional tags. Returns when done or
            # on stop.
            if not green_supernova_until_idle(strip, stop_event, tag_events):
                break  # stop_event was set

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
