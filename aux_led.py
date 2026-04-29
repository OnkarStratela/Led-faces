"""Drive a simple white LED on GPIO13 via PWM and hold it on indefinitely.

Designed to be launched by `aux-led.service` (auto-installed by
`system.sh`, or installed manually with `install_aux_led.sh`) so the LED
turns on as soon as the Pi finishes booting and stays on until shutdown.

It can also be run by hand for quick tests:

    sudo python3 aux_led.py             # uses AUX_LED_BRIGHTNESS below
    sudo python3 aux_led.py --duty 50   # one-off override to 50% brightness
    sudo python3 aux_led.py --duty 10   # very dim glow

Press Ctrl+C (or `sudo systemctl stop aux-led`) to switch the LED off
cleanly.

Pin notes
---------
* GPIO13 (BCM 13) is on the Pi's PWM1 hardware channel. The WS2812 strip
  used by rfid_led.py / rainbow.py is on GPIO12 (PWM0), so there is no
  pin clash.
* `gpiozero` uses software-driven PWM here, which does not contend with
  the DMA-driven PWM0 used by the `rpi_ws281x` library.
"""

import argparse
import signal
import sys

from gpiozero import PWMLED

# ── Aux LED configuration ──────────────────────────────────
PIN                 = 13     # GPIO13 (PWM1) — physical pin 33
AUX_LED_BRIGHTNESS  = 100    # 0 (off) .. 100 (fully on). Same role as
                             # LED_BRIGHTNESS in rfid_led.py, but on a
                             # 0..100 scale because gpiozero's PWMLED
                             # takes a normalised duty cycle.
# ────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive a white LED on GPIO13 via PWM and keep it on.",
    )
    parser.add_argument(
        "--duty",
        type=float,
        default=None,
        help=(
            "Override brightness 0..100. If omitted, the AUX_LED_BRIGHTNESS "
            "constant at the top of this file is used."
        ),
    )
    args = parser.parse_args()

    raw = args.duty if args.duty is not None else AUX_LED_BRIGHTNESS
    duty = max(0.0, min(100.0, float(raw)))
    value = duty / 100.0

    led = PWMLED(PIN)
    led.value = value
    print(f"[AuxLED] GPIO{PIN} on, brightness = {duty:.1f}%")
    sys.stdout.flush()

    def shutdown(*_args) -> None:
        try:
            led.off()
            led.close()
        finally:
            print("[AuxLED] Off.")
            sys.stdout.flush()
            sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Block forever while gpiozero holds the PWM signal at the requested
    # duty cycle. signal.pause() returns when SIGINT / SIGTERM arrive,
    # which our handlers above turn into a clean exit.
    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
