"""Drive a simple white LED on GPIO13 via PWM and hold it on indefinitely.

Designed to be launched by `aux-led.service` (see `install_aux_led.sh`) so
the LED turns on as soon as the Pi finishes booting and stays on until
shutdown.

It can also be run by hand for quick tests:

    sudo python3 aux_led.py             # full brightness (default)
    sudo python3 aux_led.py --duty 50   # dim to 50% brightness
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

PIN = 13


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive a white LED on GPIO13 via PWM and keep it on.",
    )
    parser.add_argument(
        "--duty",
        type=float,
        default=100.0,
        help="Brightness 0..100 (default: 100 = fully on).",
    )
    args = parser.parse_args()

    duty = max(0.0, min(100.0, args.duty))
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
