"""Drive a simple PWM-dimmable LED on GPIO13 at full brightness.

Completely standalone — does NOT touch the RFID reader, the C binary, the
WS2812 strip on GPIO12, or any of the production scripts. Intended to be
launched in the background by `start.sh` so the GPIO13 LED comes on the
moment the system is started, and stays on at full brightness for the
whole session.

Behaviour
---------
* Configures GPIO13 as a PWM output via `gpiozero.PWMLED`.
* Sets the duty cycle to 1.0 (100% = full brightness).
* Sleeps forever until it receives SIGINT / SIGTERM, at which point it
  drops the duty cycle to 0 and releases the pin cleanly.

Pin choice
----------
GPIO13 is hardware PWM channel 1, independent from GPIO12 (PWM channel 0)
used by the WS2812 strip — so the two cannot collide. `gpiozero` here
falls back to software PWM via its default backend, which is fine for a
single dimmable LED held at 100% duty.

Run it (on the Pi) with:

    sudo python3 pwm_led.py

`sudo` is required because GPIO access on the Pi normally needs root
(matching how the rest of the system is launched). Press Ctrl+C to stop;
the LED is turned off cleanly on exit.
"""

import signal
import sys
import time

from gpiozero import PWMLED

PWM_PIN = 13     # GPIO13 (PWM1)
BRIGHTNESS = 0.5 # 0.0 (off) to 1.0 (full brightness)


def main() -> int:
    led = PWMLED(PWM_PIN)
    led.value = BRIGHTNESS

    print(
        f"[pwm-led] GPIO{PWM_PIN} ON at {int(BRIGHTNESS * 100)}% duty. "
        f"Ctrl+C to stop."
    )
    sys.stdout.flush()

    stopping = {"now": False}

    def shutdown(*_args) -> None:
        if stopping["now"]:
            return
        stopping["now"] = True
        print("\n[pwm-led] Stopping — turning GPIO13 off.")
        sys.stdout.flush()
        try:
            # Drive the line LOW and hold it; deliberately do NOT call
            # led.close() here. close() can race the PWM thread and
            # latch the pin HIGH right as the program exits, which makes
            # the LED snap to full brightness on shutdown. Holding
            # value = 0.0 keeps the PWM driver outputting LOW; the OS
            # cleans up the pin claim when the process exits.
            led.value = 0.0
            time.sleep(0.1)
        except Exception:
            pass

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while not stopping["now"]:
            time.sleep(1.0)
    finally:
        shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
