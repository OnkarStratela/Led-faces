#!/bin/bash
# Install / enable the aux-led.service so the GPIO13 white LED turns on
# automatically every time the Pi boots.
#
# Usage:
#     ./install_aux_led.sh           # install + enable + start
#     ./install_aux_led.sh uninstall # stop + disable + remove

set -e

SERVICE_NAME="aux-led.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="${SCRIPT_DIR}/${SERVICE_NAME}"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

case "${1:-install}" in
    uninstall|remove)
        echo "[AuxLED] Stopping + disabling ${SERVICE_NAME}..."
        sudo systemctl stop    "${SERVICE_NAME}" || true
        sudo systemctl disable "${SERVICE_NAME}" || true
        sudo rm -f "${SERVICE_DST}"
        sudo systemctl daemon-reload
        echo "[AuxLED] Removed."
        exit 0
        ;;
esac

# Sanity checks -----------------------------------------------------------
if [ ! -f "${SERVICE_SRC}" ]; then
    echo "[AuxLED] ERROR: ${SERVICE_SRC} not found."
    exit 1
fi
if [ ! -f "${SCRIPT_DIR}/aux_led.py" ]; then
    echo "[AuxLED] ERROR: ${SCRIPT_DIR}/aux_led.py not found."
    exit 1
fi

# Make sure gpiozero is available (it ships with Raspberry Pi OS, but be
# forgiving in case someone is on a minimal image).
if ! python3 -c "import gpiozero" 2>/dev/null; then
    echo "[AuxLED] Installing python3-gpiozero..."
    sudo apt-get update -y
    sudo apt-get install -y python3-gpiozero
fi

# Install service file ----------------------------------------------------
echo "[AuxLED] Installing ${SERVICE_NAME} -> ${SERVICE_DST}"
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"

# Rewrite the {SCRIPT_DIR} placeholder so the service points at this
# checkout of the repo, no matter where the user cloned it.
sudo sed -i "s|{SCRIPT_DIR}|${SCRIPT_DIR}|g" "${SERVICE_DST}"

# Enable + start ----------------------------------------------------------
echo "[AuxLED] Reloading systemd..."
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo ""
echo "[AuxLED] Done. The LED should be on now and will turn on at every boot."
echo ""
echo "  Status :  sudo systemctl status  ${SERVICE_NAME}"
echo "  Logs   :  sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Stop   :  sudo systemctl stop    ${SERVICE_NAME}"
echo "  Start  :  sudo systemctl start   ${SERVICE_NAME}"
echo "  Off-permanently:  ./install_aux_led.sh uninstall"
