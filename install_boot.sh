#!/bin/bash
# Install / enable the RFID + LED system as a boot-time systemd service.
#
# After running this once, every reboot of the Pi will automatically:
#   1. Build rfid_reader (via compile.sh)
#   2. Run rfid_led.py, which:
#       - turns on the aux white LED (GPIO13) at AUX_LED_BRIGHTNESS,
#       - drives the WS2812 strip with the green-blink-on-tag behaviour,
#       - launches the rfid_reader subprocess to scan tags.
#
# Usage:
#     ./install_boot.sh             # install + enable + start
#     ./install_boot.sh uninstall   # stop + disable + remove

set -e

SERVICE_NAME="rfid-system.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="${SCRIPT_DIR}/${SERVICE_NAME}"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

case "${1:-install}" in
    uninstall|remove)
        echo "[Boot] Stopping + disabling ${SERVICE_NAME}..."
        sudo systemctl stop    "${SERVICE_NAME}" || true
        sudo systemctl disable "${SERVICE_NAME}" || true
        sudo rm -f "${SERVICE_DST}"
        sudo systemctl daemon-reload
        echo "[Boot] Removed."
        exit 0
        ;;
esac

# Sanity checks -----------------------------------------------------------
if [ ! -f "${SERVICE_SRC}" ]; then
    echo "[Boot] ERROR: ${SERVICE_SRC} not found."
    exit 1
fi
if [ ! -f "${SCRIPT_DIR}/system.sh" ] || [ ! -f "${SCRIPT_DIR}/rfid_led.py" ]; then
    echo "[Boot] ERROR: system.sh or rfid_led.py missing in ${SCRIPT_DIR}."
    exit 1
fi

# Dependencies ------------------------------------------------------------
if ! python3 -c "import gpiozero" 2>/dev/null; then
    echo "[Boot] Installing python3-gpiozero (for the aux GPIO13 LED)..."
    sudo apt-get update -y
    sudo apt-get install -y python3-gpiozero
fi
if ! python3 -c "import rpi_ws281x" 2>/dev/null; then
    echo "[Boot] Installing rpi_ws281x (for the WS2812 strip)..."
    sudo pip3 install rpi_ws281x --break-system-packages 2>/dev/null \
        || sudo pip3 install rpi_ws281x
fi

# Make scripts executable
chmod +x "${SCRIPT_DIR}/system.sh" "${SCRIPT_DIR}/compile.sh" 2>/dev/null || true

# Install service file ----------------------------------------------------
echo "[Boot] Installing ${SERVICE_NAME} -> ${SERVICE_DST}"
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"

# Rewrite the {SCRIPT_DIR} placeholder so the unit points at this checkout
# of the repo, no matter where the user cloned it.
sudo sed -i "s|{SCRIPT_DIR}|${SCRIPT_DIR}|g" "${SERVICE_DST}"

# Enable + start ----------------------------------------------------------
echo "[Boot] Reloading systemd..."
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo ""
echo "[Boot] Done. The whole RFID + LED system will start at every boot."
echo ""
echo "  Status :  sudo systemctl status  ${SERVICE_NAME}"
echo "  Logs   :  sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Stop   :  sudo systemctl stop    ${SERVICE_NAME}"
echo "  Start  :  sudo systemctl start   ${SERVICE_NAME}"
echo "  Off    :  ./install_boot.sh uninstall"
