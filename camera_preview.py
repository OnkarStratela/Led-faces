#!/usr/bin/env python3
"""Small movable camera preview window for system.sh.

Opens a normal desktop window (title bar, move, resize, minimize) so the
RFID terminal stays visible. Uses Picamera2 + OpenCV instead of rpicam-hello,
whose default preview is a fullscreen overlay on Pi OS.
"""

import os
import signal
import sys

# When launched from a shell/SSH session, DISPLAY is often unset even though
# the Pi desktop is running on :0.
os.environ.setdefault("DISPLAY", ":0")

WINDOW_NAME = "Camera Live Stream"
WINDOW_W = 640
WINDOW_H = 480
# Initial top-right placement (adjust if your screen resolution differs).
WINDOW_X = 1250
WINDOW_Y = 30

_running = True


def _stop(*_args) -> None:
    global _running
    _running = False


def main() -> int:
    try:
        import cv2
        from picamera2 import Picamera2
    except ImportError as exc:
        print(f"[camera] Skipping preview (missing package): {exc}", file=sys.stderr)
        return 0

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"size": (WINDOW_W, WINDOW_H)}))
    picam2.start()

    # Anti-glare tuning: the bright LED strip blows out highlights, so bias the
    # auto-exposure down and meter on the centre. The camera still runs at full
    # capability (AE/AWB stay on) but the image stops clipping to white.
    _apply_antiglare_controls(picam2)

    # CLAHE on the luminance channel locally recovers detail in the glare
    # without darkening the whole frame.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, WINDOW_W, WINDOW_H)
    cv2.moveWindow(WINDOW_NAME, WINDOW_X, WINDOW_Y)

    try:
        while _running:
            frame = picam2.capture_array()
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            bgr = _reduce_glare(cv2, bgr, clahe)
            cv2.imshow(WINDOW_NAME, bgr)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        picam2.stop()
        cv2.destroyAllWindows()

    return 0


def _apply_antiglare_controls(picam2) -> None:
    """Bias exposure/gain down so bright glare stops clipping to pure white."""
    try:
        from libcamera import controls as libcontrols
    except Exception:
        libcontrols = None

    ctrls = {
        # Pull the overall exposure target down (negative = darker).
        "ExposureValue": -1.0,
        # Cap analogue gain so dark areas aren't amplified into more glare.
        "AnalogueGain": 1.0,
        # Slightly reduce brightness / raise contrast for clarity.
        "Brightness": -0.1,
        "Contrast": 1.2,
    }
    if libcontrols is not None:
        try:
            # Centre-weighted metering ignores bright edges/glare hotspots.
            ctrls["AeMeteringMode"] = libcontrols.AeMeteringModeEnum.CentreWeighted
        except Exception:
            pass

    # Apply best-effort; unsupported keys must not break the preview.
    for key, value in ctrls.items():
        try:
            picam2.set_controls({key: value})
        except Exception:
            pass


def _reduce_glare(cv2, bgr, clahe):
    """Recover highlight detail locally and gently roll off near-white pixels."""
    try:
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = clahe.apply(l)
        lab = cv2.merge((l, a, b))
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    except Exception:
        return bgr


if __name__ == "__main__":
    sys.exit(main())
