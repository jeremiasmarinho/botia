from __future__ import annotations

import argparse
import time


def run_debug(rect_size: int, interval_seconds: float) -> None:
    import cv2
    import mss
    import numpy as np
    import pyautogui

    print("[Debug] Press Q in preview window to exit")

    with mss.mss() as sct:
        monitor = sct.monitors[1]

        while True:
            frame = np.array(sct.grab(monitor))[:, :, :3]
            mouse_x, mouse_y = pyautogui.position()

            left = max(0, mouse_x - rect_size // 2)
            top = max(0, mouse_y - rect_size // 2)
            right = min(frame.shape[1] - 1, mouse_x + rect_size // 2)
            bottom = min(frame.shape[0] - 1, mouse_y + rect_size // 2)

            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.putText(
                frame,
                f"Click simulated at {mouse_x},{mouse_y}",
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            print(f"[Debug] Click simulated at {mouse_x},{mouse_y}")
            cv2.imshow("Titan Debug Interface", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            time.sleep(interval_seconds)

    cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw click rectangle for debug without physical click")
    parser.add_argument("--rect-size", type=int, default=90)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    run_debug(rect_size=args.rect_size, interval_seconds=args.interval)


if __name__ == "__main__":
    main()
