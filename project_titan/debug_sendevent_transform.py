"""Test coordinate transforms for sendevent."""
import subprocess
import hashlib
import time

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"
TOUCH = "/dev/input/event2"


def adb(*a):
    return subprocess.run(
        [ADB, "-s", DEV] + list(a),
        capture_output=True, timeout=10
    ).stdout


def screenhash():
    adb("shell", "screencap", "/sdcard/tmp.png")
    adb("pull", "/sdcard/tmp.png", "reports/tmp.png")
    from PIL import Image
    import numpy as np
    return hashlib.md5(
        np.array(Image.open("reports/tmp.png")).tobytes()
    ).hexdigest()


def sendevent_tap(tx, ty):
    """Send a touch via sendevent at raw touch coords (tx, ty)."""
    cmds = "; ".join([
        f"sendevent {TOUCH} 3 47 0",
        f"sendevent {TOUCH} 3 57 100",
        f"sendevent {TOUCH} 3 53 {tx}",
        f"sendevent {TOUCH} 3 54 {ty}",
        f"sendevent {TOUCH} 3 58 1",
        f"sendevent {TOUCH} 1 330 1",
        f"sendevent {TOUCH} 1 325 1",
        f"sendevent {TOUCH} 0 0 0",
        "sleep 0.08",
        f"sendevent {TOUCH} 3 57 -1",
        f"sendevent {TOUCH} 1 330 0",
        f"sendevent {TOUCH} 1 325 0",
        f"sendevent {TOUCH} 0 0 0",
    ])
    adb("shell", cmds)


def main():
    # Close any open dialog first
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(2)

    # Display coordinate of the known blue button
    dx, dy = 590, 1220

    transforms = [
        ("A(dy,dx)", dy, dx),                     # (1220, 590)
        ("B(dy,719-dx)", dy, 719 - dx),            # (1220, 129)
        ("C(1279-dy,dx)", 1279 - dy, dx),          # (59, 590)
        ("D(1279-dy,719-dx)", 1279 - dy, 719 - dx),  # (59, 129)
    ]

    for name, tx, ty in transforms:
        print(f"Testing {name}: touch=({tx},{ty})")
        h1 = screenhash()
        sendevent_tap(tx, ty)
        time.sleep(1.5)
        h2 = screenhash()
        result = ">>> SCREEN CHANGED! <<<" if h1 != h2 else "no change"
        print(f"  Result: {result}")

        if h1 != h2:
            print("  (closing dialog)")
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1.5)

    # Also test: does input touchscreen tap still work? 
    print("\nVerification: input touchscreen tap 590 1220")
    h1 = screenhash()
    adb("shell", "input", "touchscreen", "tap", "590", "1220")
    time.sleep(1.5)
    h2 = screenhash()
    result = ">>> SCREEN CHANGED! <<<" if h1 != h2 else "no change"
    print(f"  Result: {result}")
    if h1 != h2:
        adb("shell", "input", "keyevent", "KEYCODE_BACK")


if __name__ == "__main__":
    main()
