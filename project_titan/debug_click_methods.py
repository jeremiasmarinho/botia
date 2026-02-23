"""Comprehensive click method diagnostic for LDPlayer + PPPoker.

Tests ALL known methods in priority order:
1. input touchscreen tap (current method - broken?)
2. sendevent (raw kernel events, bypasses Android framework)
3. input tap (no source qualifier)
4. input mouse tap
5. Combined sendevent batch (single shell command)
"""

import subprocess
import time
import hashlib
import sys
import struct

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"
REPORTS = "reports"

# Touch device info from /proc/bus/input/devices:
# /dev/input/event2 = "input" (touchscreen)
# ABS_MT_POSITION_X: 0..1279  (corresponds to display HEIGHT in portrait)
# ABS_MT_POSITION_Y: 0..719   (corresponds to display WIDTH in portrait)
# ABS_MT_SLOT: 0..15
# ABS_MT_TRACKING_ID: 0..65535
# ABS_MT_PRESSURE: 0..2
# BTN_TOUCH, BTN_TOOL_FINGER
# INPUT_PROP_DIRECT

TOUCH_DEV = "/dev/input/event2"

# Event codes
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0
ABS_MT_SLOT = 0x2f        # 47
ABS_MT_POSITION_X = 0x35  # 53
ABS_MT_POSITION_Y = 0x36  # 54
ABS_MT_TRACKING_ID = 0x39 # 57
ABS_MT_PRESSURE = 0x3a    # 58
BTN_TOUCH = 0x14a         # 330
BTN_TOOL_FINGER = 0x145   # 325


def adb(*args, timeout=10):
    """Run ADB command and return stdout."""
    cmd = [ADB, "-s", DEVICE] + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.stdout


def screenshot(path):
    """Take screenshot and return file hash."""
    data = adb("shell", "screencap", "-p")
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.md5(data).hexdigest()


def check_current_app():
    """Show what app is in foreground."""
    out = adb("shell", "dumpsys", "window", "windows")
    for line in out.decode(errors="replace").split("\n"):
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            print(f"  {line.strip()}")


def test_method(name, tap_fn, display_x, display_y, wait=1.5):
    """Test a click method by comparing before/after screenshots."""
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"Target: display ({display_x}, {display_y})")
    print(f"{'='*60}")

    before_path = f"{REPORTS}/diag_{name}_before.png"
    after_path = f"{REPORTS}/diag_{name}_after.png"

    h1 = screenshot(before_path)
    print(f"  Before hash: {h1}")

    tap_fn(display_x, display_y)
    time.sleep(wait)

    h2 = screenshot(after_path)
    print(f"  After hash:  {h2}")

    if h1 != h2:
        print(f"  ✅ SCREEN CHANGED — click likely registered!")
        return True
    else:
        print(f"  ❌ SCREEN UNCHANGED — click did NOT register")
        return False


def method_input_touchscreen_tap(x, y):
    """Method 1: adb shell input touchscreen tap X Y"""
    adb("shell", "input", "touchscreen", "tap", str(x), str(y))


def method_input_tap(x, y):
    """Method 2: adb shell input tap X Y (no source)"""
    adb("shell", "input", "tap", str(x), str(y))


def method_input_mouse_tap(x, y):
    """Method 3: adb shell input mouse tap X Y"""
    adb("shell", "input", "mouse", "tap", str(x), str(y))


def portrait_to_touch_coords(display_x, display_y, disp_w=720, disp_h=1280):
    """Convert portrait display coordinates to raw touch device coordinates.
    
    The touch device has:
      X axis: 0..1279 (maps to display height)
      Y axis: 0..719  (maps to display width)
    
    For a 0-degree rotation (portrait), the mapping typically is:
      touch_x = display_y * (touch_x_max / (disp_h - 1))
      touch_y = display_x * (touch_y_max / (disp_w - 1))
    
    But the exact mapping depends on the rotation. Let me test both options.
    """
    # Option A: direct mapping (assuming no rotation compensation needed)
    # touch_x = display_y, touch_y = display_x
    touch_x_a = int(display_y * 1279 / (disp_h - 1))
    touch_y_a = int(display_x * 719 / (disp_w - 1))
    
    # Option B: mirrored Y
    # touch_x = display_y, touch_y = (disp_w - 1 - display_x) * 719 / (disp_w - 1) 
    touch_x_b = int(display_y * 1279 / (disp_h - 1))
    touch_y_b = int((disp_w - 1 - display_x) * 719 / (disp_w - 1))
    
    # Option C: no swap (direct 1:1 assuming orientation-aware)
    touch_x_c = int(display_x * 1279 / (disp_w - 1))
    touch_y_c = int(display_y * 719 / (disp_h - 1))
    
    return {
        "A_swap": (touch_x_a, touch_y_a),
        "B_swap_mirror": (touch_x_b, touch_y_b),
        "C_direct": (touch_x_c, touch_y_c),
    }


def method_sendevent_individual(x, y):
    """Method 4: Individual sendevent calls (slow but clear)."""
    coords = portrait_to_touch_coords(x, y)
    tx, ty = coords["A_swap"]
    print(f"  sendevent coords (A_swap): touch=({tx}, {ty})")
    
    cmds = [
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_SLOT} 0",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} 1",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_X} {tx}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_Y} {ty}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_PRESSURE} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 1",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
        "sleep 0.05",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} -1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 0",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 0",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
    ]
    
    shell_cmd = "; ".join(cmds)
    adb("shell", shell_cmd)


def method_sendevent_option_b(x, y):
    """Method 5: sendevent with swap+mirror coordinate transform."""
    coords = portrait_to_touch_coords(x, y)
    tx, ty = coords["B_swap_mirror"]
    print(f"  sendevent coords (B_swap_mirror): touch=({tx}, {ty})")
    
    cmds = [
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_SLOT} 0",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} 2",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_X} {tx}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_Y} {ty}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_PRESSURE} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 1",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
        "sleep 0.05",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} -1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 0",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 0",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
    ]
    
    shell_cmd = "; ".join(cmds)
    adb("shell", shell_cmd)


def method_sendevent_option_c(x, y):
    """Method 6: sendevent with direct (no swap) coordinate transform."""
    coords = portrait_to_touch_coords(x, y)
    tx, ty = coords["C_direct"]
    print(f"  sendevent coords (C_direct): touch=({tx}, {ty})")
    
    cmds = [
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_SLOT} 0",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} 3",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_X} {tx}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_POSITION_Y} {ty}",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_PRESSURE} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 1",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
        "sleep 0.05",
        f"sendevent {TOUCH_DEV} {EV_ABS} {ABS_MT_TRACKING_ID} -1",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOUCH} 0",
        f"sendevent {TOUCH_DEV} {EV_KEY} {BTN_TOOL_FINGER} 0",
        f"sendevent {TOUCH_DEV} {EV_SYN} {SYN_REPORT} 0",
    ]
    
    shell_cmd = "; ".join(cmds)
    adb("shell", shell_cmd)


def method_dd_binary_write(x, y):
    """Method 7: Direct binary write to /dev/input/event2.
    
    This is the FASTEST possible method - writes raw input_event structs
    directly to the device node using 'dd' or 'printf'.
    
    On Android 32-bit (LDPlayer), struct input_event is 16 bytes:
      struct timeval { uint32_t sec; uint32_t usec; };  // 8 bytes
      uint16_t type;   // 2 bytes
      uint16_t code;   // 2 bytes
      int32_t value;   // 4 bytes
    Total: 16 bytes per event
    """
    coords = portrait_to_touch_coords(x, y)
    tx, ty = coords["A_swap"]
    print(f"  binary write coords (A_swap): touch=({tx}, {ty})")
    
    def pack_event(ev_type, code, value):
        """Pack one input_event struct (16 bytes, 32-bit Android)."""
        return struct.pack("<IIHHi", 0, 0, ev_type, code, value)
    
    events = []
    # Touch down
    events.append(pack_event(EV_ABS, ABS_MT_SLOT, 0))
    events.append(pack_event(EV_ABS, ABS_MT_TRACKING_ID, 10))
    events.append(pack_event(EV_ABS, ABS_MT_POSITION_X, tx))
    events.append(pack_event(EV_ABS, ABS_MT_POSITION_Y, ty))
    events.append(pack_event(EV_ABS, ABS_MT_PRESSURE, 1))
    events.append(pack_event(EV_KEY, BTN_TOUCH, 1))
    events.append(pack_event(EV_KEY, BTN_TOOL_FINGER, 1))
    events.append(pack_event(EV_SYN, SYN_REPORT, 0))
    
    # Touch up
    events.append(pack_event(EV_ABS, ABS_MT_TRACKING_ID, 0xFFFFFFFF))  # -1 as unsigned
    events.append(pack_event(EV_KEY, BTN_TOUCH, 0))
    events.append(pack_event(EV_KEY, BTN_TOOL_FINGER, 0))
    events.append(pack_event(EV_SYN, SYN_REPORT, 0))
    
    raw = b"".join(events)
    
    # Write via printf to the device
    hex_str = "".join(f"\\x{b:02x}" for b in raw)
    shell_cmd = f'printf "{hex_str}" > {TOUCH_DEV}'
    adb("shell", shell_cmd)


def observe_real_touch():
    """Monitor what events a real tap generates in LDPlayer.
    
    This helps us understand the exact event sequence expected.
    """
    print("\n" + "="*60)
    print("OBSERVING: What does LDPlayer's own touch generate?")
    print("Capturing 3 seconds of touch events...")
    print("(Click MANUALLY on LDPlayer during this time!)")
    print("="*60)
    
    try:
        result = subprocess.run(
            [ADB, "-s", DEVICE, "shell", "getevent", "-lt", TOUCH_DEV],
            capture_output=True,
            timeout=5,
        )
        output = result.stdout.decode(errors="replace")
        if output.strip():
            print(f"  Events captured:\n{output[:2000]}")
        else:
            print("  No events captured (no touch happened)")
    except subprocess.TimeoutExpired as e:
        output = e.stdout.decode(errors="replace") if e.stdout else ""
        if output.strip():
            print(f"  Events captured:\n{output[:2000]}")
        else:
            print("  No events captured (timeout, no touch happened)")


def main():
    target_x, target_y = 361, 1220  # Call button at 720x1280
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "observe":
            observe_real_touch()
            return
        elif sys.argv[1] == "center":
            target_x, target_y = 360, 640
    
    print("=" * 60)
    print("CLICK METHOD DIAGNOSTICS")
    print("=" * 60)
    
    print("\n--- Current app info ---")
    check_current_app()
    
    # Show coordinate transforms
    print(f"\n--- Coordinate transforms for ({target_x}, {target_y}) ---")
    coords = portrait_to_touch_coords(target_x, target_y)
    for name, (tx, ty) in coords.items():
        print(f"  {name}: touch=({tx}, {ty})")
    
    results = {}
    
    # Test each method
    results["1_input_touchscreen_tap"] = test_method(
        "1_input_touchscreen_tap",
        method_input_touchscreen_tap, target_x, target_y
    )
    
    results["2_input_tap"] = test_method(
        "2_input_tap",
        method_input_tap, target_x, target_y
    )
    
    results["3_input_mouse_tap"] = test_method(
        "3_input_mouse_tap",
        method_input_mouse_tap, target_x, target_y
    )
    
    results["4_sendevent_A_swap"] = test_method(
        "4_sendevent_A_swap",
        method_sendevent_individual, target_x, target_y
    )
    
    results["5_sendevent_B_swap_mirror"] = test_method(
        "5_sendevent_B_swap_mirror",
        method_sendevent_option_b, target_x, target_y
    )
    
    results["6_sendevent_C_direct"] = test_method(
        "6_sendevent_C_direct",
        method_sendevent_option_c, target_x, target_y
    )
    
    results["7_dd_binary_write"] = test_method(
        "7_dd_binary_write",
        method_dd_binary_write, target_x, target_y
    )
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, ok in results.items():
        status = "✅ WORKS" if ok else "❌ FAILED"
        print(f"  {status}  {name}")


if __name__ == "__main__":
    main()
