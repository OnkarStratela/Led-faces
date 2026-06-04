"""Bridge between the CAEN RFID reader (rfid_reader.c) and the WS2812 LED strip.

"Signature glide" idle animation — designed for a matte-black bin chute.

Rationale
---------
The bin is matte black, so the IDLE background is left OFF: against black, an
unlit strip reads as a seamless, intentional surface rather than a lit-up
gadget. On top of that void a single CRISP FULL-WHITE light glides back and
forth with a soft, cool-white gaussian halo trailing it. The motion uses
cosine easing, so the light decelerates and "breathes" at each end instead of
scanning mechanically — a calm, premium, product-grade light that quietly
draws the eye to the chute opening and invites a deposit.

Two hard constraints are respected:
  1. Everything runs at full brightness. The WS2812 master brightness stays at
     255 and the GPIO13 PWM LED at 1.0; the halo/tail is shaped purely by
     per-pixel COLOUR, never by dimming the hardware. The glide head is pure
     white (255, 255, 255).
  2. The GREEN scan blink is unchanged — identical colour (#00FF00) and timing
     to every other mode.

Behaviour
---------
* Idle (no tags being scanned): strip background OFF; one full-white light
  glides edge-to-edge with eased motion and a soft cool-white tail, looping.
* Every NEW unique tag reported by `rfid_reader` produces ONE visible green
  blink:
      - a very short WHITE "off-pulse" (so back-to-back blinks are visually
        distinct from one continuous green pulse),
      - then GREEN for `GREEN_HOLD_SECONDS` seconds,
      - then back to the signature glide idle animation.
* If another new tag arrives while the strip is still green, the current green
  pulse is cut short and a fresh blink starts. That way, scanning N unique
  containers in quick succession produces N distinct green blinks — a visual
  counter for the operator.
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

# How long each green blink lasts after a new tag is detected.
GREEN_HOLD_SECONDS = 1.0
# Short white "off-pulse" used to visually separate consecutive green blinks
# when several tags arrive in quick succession.
WHITE_FLASH_SECONDS = 0.10

# ── Signature glide animation tuning ───────────────────────
# Seconds for one full there-and-back glide (edge → edge → edge). Larger is
# calmer / more premium.
GLIDE_PERIOD_SECONDS = 4.2
# Frame interval. Small = smoother motion.
GLIDE_FRAME_SECONDS = 0.02
# Width of the soft halo/tail around the glide head, in pixels (gaussian
# sigma). Larger = longer, softer trail.
GLIDE_HALO_SIGMA = 2.3
# Pixels whose computed intensity is below this are left fully OFF, keeping the
# matte-black background clean instead of a dim grey wash.
GLIDE_MIN_INTENSITY = 0.04
# Cool-white bias of the tail: as the halo fades, the blue channel lingers
# slightly longer than red/green for an icy, premium falloff. 1.0 = neutral
# white tail; lower = cooler. Applied as an exponent on the blue channel.
GLIDE_COOL_EXPONENT = 0.75
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


def idle_signature_glide_until_tag(
    strip: PixelStrip,
    stop_event: "threading.Event",
    tag_events: "queue.Queue[float]",
) -> bool:
    """Run the signature glide over an OFF (matte-black) strip until a tag.

    A single full-white head glides edge-to-edge with cosine easing (it slows
    and breathes at each end), trailing a soft cool-white gaussian halo. The
    background stays OFF so it blends into the matte-black chute. Between every
    frame we poll for a new tag event.

    Returns True if a new-tag event arrived (consumed from the queue, so the
    caller should render a green blink). Returns False if `stop_event` was set.
    """
    n = strip.numPixels()
    if n <= 0:
        return False

    two_sigma_sq = 2.0 * GLIDE_HALO_SIGMA * GLIDE_HALO_SIGMA
    start = time.monotonic()

    while not stop_event.is_set():
        try:
            tag_events.get_nowait()
            return True
        except queue.Empty:
            pass

        # Eased oscillation: phase runs 0→1→0 smoothly via a cosine, so the
        # head decelerates at both ends rather than snapping back.
        t = time.monotonic() - start
        phase = 0.5 - 0.5 * math.cos(2.0 * math.pi * t / GLIDE_PERIOD_SECONDS)
        head = phase * (n - 1)

        for i in range(n):
            dist = i - head
            intensity = math.exp(-(dist * dist) / two_sigma_sq)
            if intensity < GLIDE_MIN_INTENSITY:
                strip.setPixelColor(i, 0)  # stay matte-black
                continue
            # Head is pure full white; the tail keeps its blue channel a touch
            # longer for a cool, icy falloff. Hardware brightness is untouched.
            r = int(round(255 * intensity))
            g = r
            b = int(round(255 * (intensity ** GLIDE_COOL_EXPONENT)))
            strip.setPixelColor(i, Color(r, g, b))

        strip.show()
        time.sleep(GLIDE_FRAME_SECONDS)

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

    # Default idle state: strip OFF (the signature glide takes over once the
    # LED worker thread starts).
    fill_strip(strip, OFF)

    # One item is enqueued per new unique tag. The LED worker pops items and
    # turns them into visible green blinks.
    tag_events: "queue.Queue[float]" = queue.Queue()
    stop_event = threading.Event()

    def led_worker() -> None:
        """Render the signature-glide idle / GREEN-blink behaviour described in
        the module docstring."""
        while not stop_event.is_set():
            # Idle: run the signature glide over an OFF strip until a new tag
            # arrives (or we are told to stop).
            got_tag = idle_signature_glide_until_tag(
                strip, stop_event, tag_events
            )
            if not got_tag:
                break  # stop_event was set

            # New tag → produce one green blink (unchanged behaviour).
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

            # 3. Back to the signature glide idle animation (next loop
            #    iteration).

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
