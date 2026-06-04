"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

"Converge" — twin comets that meet in the middle, made for a matte-black chute.

Rationale
---------
The bin is matte black, so the IDLE background is left OFF. Two crisp
full-white comets launch from the two ENDS of the strip and glide INWARD with
eased acceleration, each trailing a soft cool-white tail. The instant they
TOUCH in the CENTRE the contact point flashes RED, then they bounce back out
and fade to white again — a continuous, breathing convergence. The energy
pulling toward the centre reads as "deposit here" and looks like a designed
product light against black.

The scan acknowledgment is a matching green version: both comets snap to green
and SLAM to the centre into a full-green burst that holds, then dissolves back
to black before the white convergence resumes.

Two hard constraints are respected:
  1. Everything runs at full brightness. The WS2812 master brightness stays at
     255 and the GPIO13 PWM LED at 1.0; the comets, tails and bloom are shaped
     purely by per-pixel COLOUR, never by dimming the hardware. The comet heads
     are pure white (255, 255, 255) and the burst peaks at pure green.
  2. A scan is always GREEN (#00FF00); only its SHAPE/animation is unique.

Behaviour
---------
* Idle (no tags being scanned): two cool-white comets converge to the centre
  (flashing RED at the moment they touch) and sweep back out over an OFF strip,
  looping forever.
* Every NEW unique tag reported by `rfid_reader` produces ONE green burst:
  green comets slam to the centre, a full-green flash holds, then dissolves.
* If another new tag arrives during a burst, it is cut short and a fresh one
  starts. That way, scanning N unique containers in quick succession produces
  N distinct green bursts — a visual counter for the operator.
* Every tag line also prints "[LED-RFID] Tags scanned: N" so the running total
  is visible in the terminal.
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

# ── Colours (HEX) ──────────────────────────────────────────
GREEN_HEX = "#00FF00"
WHITE_HEX = "#FFFFFF"
OFF_HEX   = "#000000"
# ────────────────────────────────────────────────────────────

# ── Converge (idle) tuning ─────────────────────────────────
# Seconds for one full converge-and-return cycle (edges → centre → edges).
CONVERGE_PERIOD_SECONDS = 3.2
# Frame interval. Small = smoother motion.
CONVERGE_FRAME_SECONDS = 0.02
# Width of each comet's soft tail, in pixels (gaussian sigma).
CONVERGE_HALO_SIGMA = 1.8
# Pixels whose computed intensity is below this stay fully OFF (clean black).
CONVERGE_MIN_INTENSITY = 0.04
# Cool-white bias of the tails (blue lingers): 1.0 = neutral, lower = cooler.
CONVERGE_COOL_EXPONENT = 0.78
# Extra glow added at the centre as the two comets meet (0 = none).
CONVERGE_MEET_GLOW = 1.0
# ────────────────────────────────────────────────────────────

# ── Green burst (scan acknowledgment) tuning ───────────────
# Seconds for the green comets to slam from the edges to the centre.
BURST_IN_SECONDS = 0.26
# Seconds the fully-green strip is held at the peak.
BURST_HOLD_SECONDS = 0.42
# Seconds for the green to dissolve smoothly back to black.
BURST_FADE_SECONDS = 0.42
# Frame interval for the burst.
BURST_FRAME_SECONDS = 0.02
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


def _render_twin_comets(
    strip: PixelStrip,
    n: int,
    left_pos: float,
    right_pos: float,
    two_sigma_sq: float,
    meet_glow: float,
    green: bool,
) -> None:
    """Render two comets (at `left_pos`/`right_pos`) plus a centre meet-glow.

    `meet_glow` (0..1) is how strongly the comets are meeting at the centre.
    `green` selects the comet colour: pure green when True, cool white when
    False. In the cool-white idle mode the meeting point flashes RED as the
    comets touch (the green burst keeps its meeting green).
    """
    center = (n - 1) / 2.0
    for i in range(n):
        dl = i - left_pos
        dr = i - right_pos
        comet = max(
            math.exp(-(dl * dl) / two_sigma_sq),
            math.exp(-(dr * dr) / two_sigma_sq),
        )

        if green:
            intensity = comet
            if meet_glow > 0.0:
                dc = i - center
                intensity = max(
                    intensity,
                    meet_glow * math.exp(-(dc * dc) / two_sigma_sq),
                )
            if intensity < CONVERGE_MIN_INTENSITY:
                strip.setPixelColor(i, 0)
                continue
            strip.setPixelColor(i, Color(0, int(round(255 * intensity)), 0))
            continue

        # Cool-white comets with a RED flash where they touch in the centre.
        red = 0.0
        if meet_glow > 0.0:
            dc = i - center
            red = meet_glow * math.exp(-(dc * dc) / two_sigma_sq)
        if comet < CONVERGE_MIN_INTENSITY and red < CONVERGE_MIN_INTENSITY:
            strip.setPixelColor(i, 0)
            continue
        # White base from the comets; the red overlay boosts red and suppresses
        # green/blue, so the contact point reads as pure red, easing back to
        # white as the comets separate.
        white_r = 255 * comet
        white_b = 255 * (comet ** CONVERGE_COOL_EXPONENT)
        r = max(white_r, 255 * red)
        g = white_r * (1.0 - red)
        b = white_b * (1.0 - red)
        strip.setPixelColor(i, Color(int(round(r)), int(round(g)), int(round(b))))


def idle_converge_until_tag(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Run the twin-comet convergence over an OFF strip until a tag.

    Two cool-white comets glide from the edges to the centre (eased), bloom as
    they meet, then sweep back out — looping. Between every frame we poll for a
    new tag event.

    Returns True if a new-tag event arrived (consumed from the queue, so the
    caller should render a green burst). Returns False if `stop_event` was set.
    """
    n = strip.numPixels()
    if n <= 0:
        return False

    center = (n - 1) / 2.0
    two_sigma_sq = 2.0 * CONVERGE_HALO_SIGMA * CONVERGE_HALO_SIGMA
    start = time.monotonic()

    while not stop_event.is_set():
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            pass

        # Eased oscillation 0→1→0: comets move in to the centre then back out.
        t = time.monotonic() - start
        p = 0.5 - 0.5 * math.cos(2.0 * math.pi * t / CONVERGE_PERIOD_SECONDS)
        left_pos = p * center
        right_pos = (n - 1) - p * center
        # Red contact flash: only as the comets actually touch (p → 1), so it
        # snaps red on contact and fades to white as they bounce apart.
        meet_glow = CONVERGE_MEET_GLOW * _ease((p - 0.82) / 0.18)

        _render_twin_comets(
            strip, n, left_pos, right_pos, two_sigma_sq, meet_glow, green=False
        )
        strip.show()
        time.sleep(CONVERGE_FRAME_SECONDS)

    return False


def green_burst_until_idle(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Render the green "burst" scan acknowledgment, looping on retriggers.

    One burst = green comets slam from the edges to the centre, a full-green
    flash holds, then the strip dissolves back to black. If a new tag arrives
    at any point, the current burst is cut short and a fresh one starts, so
    rapid scans produce distinct bursts.

    Returns True when it finishes cleanly (caller resumes the convergence), or
    False if `stop_event` was set.
    """
    n = strip.numPixels()
    if n <= 0:
        return True

    center = (n - 1) / 2.0
    two_sigma_sq = 2.0 * CONVERGE_HALO_SIGMA * CONVERGE_HALO_SIGMA

    def _new_tag() -> bool:
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            return False

    while not stop_event.is_set():
        retrigger = False

        # Phase 1 — green comets slam inward to the centre.
        start = time.monotonic()
        while True:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            e = (time.monotonic() - start) / BURST_IN_SECONDS
            if e >= 1.0:
                break
            p = _ease(e)
            left_pos = p * center
            right_pos = (n - 1) - p * center
            meet_glow = _ease((p - 0.6) / 0.4)
            _render_twin_comets(
                strip, n, left_pos, right_pos, two_sigma_sq, meet_glow,
                green=True,
            )
            strip.show()
            time.sleep(BURST_FRAME_SECONDS)
        if retrigger:
            continue

        # Phase 2 — hold at full green.
        fill_strip(strip, Color(0, 255, 0))
        hold_until = time.monotonic() + BURST_HOLD_SECONDS
        while time.monotonic() < hold_until:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            time.sleep(BURST_FRAME_SECONDS)
        if retrigger:
            continue

        # Phase 3 — dissolve: ease the whole strip from full green to black.
        start = time.monotonic()
        while True:
            if stop_event.is_set():
                return False
            if _new_tag():
                retrigger = True
                break
            e = (time.monotonic() - start) / BURST_FADE_SECONDS
            if e >= 1.0:
                break
            level = 1.0 - _ease(e)
            fill_strip(strip, Color(0, int(round(255 * level)), 0))
            time.sleep(BURST_FRAME_SECONDS)
        if retrigger:
            continue

        # Clean finish: strip fully OFF, then back to the idle convergence.
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

    # Default idle state: strip OFF (the convergence takes over once the LED
    # worker thread starts).
    fill_strip(strip, OFF)

    # One item is enqueued per new unique tag. The LED worker pops items and
    # turns them into visible green bursts.
    tag_events: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()

    def led_worker() -> None:
        """Render the converge idle / GREEN-burst behaviour described in the
        module docstring."""
        while not stop_event.is_set():
            # Idle: run the convergence over an OFF strip until a new tag
            # arrives (or we are told to stop).
            got_tag = idle_converge_until_tag(strip, stop_event, tag_events)
            if not got_tag:
                break  # stop_event was set

            # New tag → green burst acknowledgment (slam in → hold → dissolve),
            # retriggering on additional tags. Returns when done or on stop.
            if not green_burst_until_idle(strip, stop_event, tag_events):
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
