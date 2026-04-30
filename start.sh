#!/bin/bash
#
# Wrapper that turns on the PWM-dimmable LED on GPIO13 at full brightness
# and then runs the existing RFID system (system.sh) untouched.
#
# Usage on the Pi:
#
#     chmod +x start.sh        # first time only
#     sudo ./start.sh
#
# `sudo` is required so that:
#   - pwm_led.py can drive GPIO13, and
#   - system.sh / rfid_led.py can drive the WS2812 strip on GPIO12 via
#     PWM/DMA (rpi_ws281x needs root).
#
# Press Ctrl+C to stop everything: the RFID reader is shut down by
# system.sh as usual, and the GPIO13 LED is turned off cleanly here.
#
# This script does NOT modify any existing project file. It only adds a
# new entry point. Running ./system.sh directly still works exactly as
# before (without the GPIO13 LED).

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}===== Starting PWM LED on GPIO13 =====${NC}"

# Launch the GPIO13 PWM helper in the background. We deliberately do this
# BEFORE system.sh so the LED is at full brightness from the very first
# moment of the session.
python3 "$SCRIPT_DIR/pwm_led.py" &
PWM_PID=$!

cleanup() {
    # Idempotent: only run once.
    if [ -n "${CLEANED_UP:-}" ]; then
        return
    fi
    CLEANED_UP=1
    echo ""
    echo -e "${YELLOW}[start.sh] Shutting down GPIO13 PWM LED...${NC}"
    if kill -0 "$PWM_PID" 2>/dev/null; then
        kill -INT "$PWM_PID" 2>/dev/null || true
        # Give it a moment to clean up, then force.
        for _ in 1 2 3 4 5; do
            kill -0 "$PWM_PID" 2>/dev/null || break
            sleep 0.2
        done
        kill -KILL "$PWM_PID" 2>/dev/null || true
        wait "$PWM_PID" 2>/dev/null || true
    fi
}

# Make sure the PWM LED is always turned off when this script exits, no
# matter how (Ctrl+C, system.sh failing, etc.).
trap cleanup EXIT INT TERM

# Hand off to the existing system.sh untouched.
echo -e "${GREEN}[start.sh] PWM LED on GPIO13 should now be at full brightness.${NC}"
echo -e "${YELLOW}[start.sh] Launching existing system.sh...${NC}"
echo ""
./system.sh
SYSTEM_RC=$?

if [ "$SYSTEM_RC" -ne 0 ]; then
    echo -e "${RED}[start.sh] system.sh exited with code $SYSTEM_RC${NC}"
fi

exit "$SYSTEM_RC"
