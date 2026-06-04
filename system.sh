#!/bin/bash

echo "===== Simple RFID Reader System ====="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check for required CAEN library files in SRC folder
echo -e "${YELLOW}Checking for CAEN library files in SRC folder...${NC}"

# Check if SRC folder exists
if [ ! -d "SRC" ]; then
    echo -e "${RED}SRC folder not found!${NC}"
    echo "Please create SRC folder and copy CAEN library files there."
    exit 1
fi

REQUIRED_FILES=(
    "SRC/CAENRFIDLib_Light.c"
    "SRC/CAENRFIDLib_Light.h"
    "SRC/CAENRFIDTypes_Light.h"
    "SRC/IO_Light.c"
    "SRC/IO_Light.h"
    "SRC/Protocol_Light.h"
    "SRC/host.c"
    "SRC/host.h"
)

MISSING_FILES=()
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        MISSING_FILES+=("$file")
    fi
done

if [ ${#MISSING_FILES[@]} -ne 0 ]; then
    echo -e "${RED}Missing required CAEN library files:${NC}"
    for file in "${MISSING_FILES[@]}"; do
        echo "  - $file"
    done
    echo ""
    echo "Please copy all CAEN library files to the SRC directory."
    exit 1
fi

echo -e "${GREEN}All required CAEN files found in SRC folder!${NC}"

# Check USB device permissions
echo -e "${YELLOW}Checking USB device access...${NC}"
if [ -e /dev/ttyACM0 ] || [ -e /dev/ttyUSB0 ]; then
    if [ ! -r /dev/ttyACM0 ] && [ ! -r /dev/ttyUSB0 ]; then
        echo -e "${YELLOW}USB device found but no read permission.${NC}"
        echo "Adding user to dialout group..."
        sudo usermod -a -G dialout $USER
        echo -e "${GREEN}User added to dialout group. Please logout and login again.${NC}"
    else
        echo -e "${GREEN}USB device access OK${NC}"
    fi
else
    echo -e "${YELLOW}No CAEN RFID reader detected on USB${NC}"
    echo "Please connect the CAEN RFID reader to USB port"
fi

# Set executable permissions
echo -e "${YELLOW}Setting permissions...${NC}"
chmod +x compile.sh 2>/dev/null

# Compile the RFID reader
echo -e "${YELLOW}Compiling RFID reader...${NC}"
./compile.sh

if [ $? -eq 0 ]; then
    echo -e "${GREEN}Compilation successful!${NC}"
    echo ""

    # Let the operator pick which LED behaviour to run.
    echo -e "${YELLOW}Select LED mode:${NC}"
    echo "  1) rfid_led.py            (white snaps back after green)"
    echo "  2) white light pointing center (white wave slides to the middle over OFF background)"
    echo "  3) green wave on white      (green wave slides to the middle over WHITE background)"
    echo "  4) red wave on white        (red wave slides to the middle over WHITE background)"
    echo "  5) signature glide          (premium eased white light gliding on matte black)"
    echo "  6) signature glide + bloom  (same glide; scan = green bloom expanding from center)"
    echo "  7) constellation            (cool-white starlight twinkle; scan = green supernova)"
    echo ""
    read -p "Enter choice [1/2/3/4/5/6/7]: " LED_CHOICE

    case "$LED_CHOICE" in
        2)
            LED_SCRIPT="rfid_led_center.py"
            ;;
        3)
            LED_SCRIPT="rfid_led_green_wave.py"
            ;;
        4)
            LED_SCRIPT="rfid_led_red_wave.py"
            ;;
        5)
            LED_SCRIPT="rfid_led_signature.py"
            ;;
        6)
            LED_SCRIPT="rfid_led_signature_bloom.py"
            ;;
        7)
            LED_SCRIPT="rfid_led_constellation.py"
            ;;
        *)
            LED_SCRIPT="rfid_led.py"
            ;;
    esac

    echo ""
    echo -e "${GREEN}Starting RFID reader with LED feedback (${LED_SCRIPT})...${NC}"
    echo -e "${YELLOW}(LEDs will turn GREEN whenever a new unique tag is scanned)${NC}"
    echo ""

    # The rpi_ws281x library needs root for PWM/DMA access on the Pi.
    if [ "$EUID" -ne 0 ]; then
        sudo python3 "$LED_SCRIPT"
    else
        python3 "$LED_SCRIPT"
    fi
else
    echo -e "${RED}Compilation failed. Please check error messages.${NC}"
    exit 1
fi