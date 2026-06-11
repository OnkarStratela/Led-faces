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

# Trackbars are integers, so each control is stored as an int slider position
# and mapped to the camera's real (float) range. (slider_default, divisor,
# offset): real_value = slider / divisor - offset.
#   Brightness: -1.0 .. 1.0   (slider 0..200,  default -0.1 -> 90)
#   Exposure  : -8.0 .. 8.0   (slider 0..160,  default -1.0 -> 70)
#   Contrast  :  0.0 .. 3.0   (slider 0..300,  default  1.2 -> 120)
BRIGHTNESS_MAX = 200
BRIGHTNESS_DEFAULT = 90
EXPOSURE_MAX = 160
EXPOSURE_DEFAULT = 70
CONTRAST_MAX = 300
CONTRAST_DEFAULT = 120


def _brightness_from_slider(v: int) -> float:
    return v / 100.0 - 1.0


def _exposure_from_slider(v: int) -> float:
    return v / 10.0 - 8.0


def _contrast_from_slider(v: int) -> float:
    return v / 100.0


_running = True


def _noop(_v) -> None:
    pass


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
    # Read out the FULL sensor area (not a cropped centre region) so we get the
    # widest field of view the lens allows, then scale it down to the preview
    # window. This keeps the output/window resolution the same; only the field
    # of view widens. Requesting a small size alone makes Picamera2 pick a
    # cropped sensor mode, which looks "zoomed in".
    try:
        full_res = picam2.sensor_resolution
        config = picam2.create_preview_configuration(
            main={"size": (WINDOW_W, WINDOW_H)},
            raw={"size": full_res},
        )
    except Exception:
        config = picam2.create_preview_configuration(main={"size": (WINDOW_W, WINDOW_H)})
    picam2.configure(config)
    picam2.start()

    # Ensure the scaler uses the entire sensor array (full FOV), in case a
    # previous run left a cropped ScalerCrop in place.
    try:
        full_w, full_h = picam2.sensor_resolution
        picam2.set_controls({"ScalerCrop": (0, 0, full_w, full_h)})
    except Exception:
        pass

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

    # Live adjustment sliders inside the same window. Drag these to change the
    # camera in real time; they map to the real Brightness/Exposure/Contrast
    # controls applied below.
    cv2.createTrackbar("Brightness", WINDOW_NAME, BRIGHTNESS_DEFAULT, BRIGHTNESS_MAX, _noop)
    cv2.createTrackbar("Exposure", WINDOW_NAME, EXPOSURE_DEFAULT, EXPOSURE_MAX, _noop)
    cv2.createTrackbar("Contrast", WINDOW_NAME, CONTRAST_DEFAULT, CONTRAST_MAX, _noop)

    last_settings = None
    try:
        while _running:
            # Read slider positions and push them to the camera when changed.
            try:
                b = cv2.getTrackbarPos("Brightness", WINDOW_NAME)
                e = cv2.getTrackbarPos("Exposure", WINDOW_NAME)
                c = cv2.getTrackbarPos("Contrast", WINDOW_NAME)
                settings = (b, e, c)
                if settings != last_settings:
                    picam2.set_controls({
                        "Brightness": _brightness_from_slider(b),
                        "ExposureValue": _exposure_from_slider(e),
                        "Contrast": _contrast_from_slider(c),
                    })
                    last_settings = settings
            except Exception:
                pass

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
    """Bias gain/metering so bright glare stops clipping to pure white.

    Brightness, ExposureValue and Contrast are now driven live by the window
    sliders (their defaults match the previous anti-glare values), so they are
    intentionally not set here.
    """
    try:
        from libcamera import controls as libcontrols
    except Exception:
        libcontrols = None

    ctrls = {
        # Cap analogue gain so dark areas aren't amplified into more glare.
        "AnalogueGain": 1.0,
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
