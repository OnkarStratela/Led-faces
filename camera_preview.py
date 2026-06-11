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

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, WINDOW_W, WINDOW_H)
    cv2.moveWindow(WINDOW_NAME, WINDOW_X, WINDOW_Y)

    try:
        while _running:
            frame = picam2.capture_array()
            cv2.imshow(WINDOW_NAME, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        picam2.stop()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    sys.exit(main())
