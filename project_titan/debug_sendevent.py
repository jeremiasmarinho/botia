"""Focused sendevent test - verify raw event injection works."""
import subprocess, time, os, numpy as np
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

def ss(name):
    r = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                      capture_output=True, timeout=10)
    path = f"reports/{name}.png"
    with open(path, "wb") as f:
        f.write(r.stdout)
    return path

def sendevent_tap(x, y, device="/dev/input/event2"):
    """Raw sendevent tap."""
    cmds = [
        f"sendevent {device} 3 57 0",       # ABS_MT_TRACKING_ID
        f"sendevent {device} 3 53 {x}",     # ABS_MT_POSITION_X
        f"sendevent {device} 3 54 {y}",     # ABS_MT_POSITION_Y
        f"sendevent {device} 1 330 1",       # BTN_TOUCH DOWN
        f"sendevent {device} 0 0 0",         # SYN_REPORT
    ]
    for cmd in cmds:
        subprocess.run([ADB, "-s", DEVICE, "shell"] + cmd.split(),
                      capture_output=True, timeout=5)
    time.sleep(0.1)
    # Release
    cmds2 = [
        f"sendevent {device} 3 57 -1",     # release
        f"sendevent {device} 1 330 0",      # BTN_TOUCH UP
        f"sendevent {device} 0 0 0",        # SYN_REPORT
    ]
    for cmd in cmds2:
        subprocess.run([ADB, "-s", DEVICE, "shell"] + cmd.split(),
                      capture_output=True, timeout=5)

def compare(path1, path2):
    img1 = np.array(Image.open(path1))[:,:,:3]
    img2 = np.array(Image.open(path2))[:,:,:3]
    return np.abs(img1.astype(int) - img2.astype(int)).mean()

# Step 1: Check event2 device capabilities
print("=== Event2 device info ===")
r = subprocess.run([ADB, "-s", DEVICE, "shell", "getevent", "-p", "/dev/input/event2"],
                  capture_output=True, text=True, timeout=5)
print(r.stdout[:500])

# Step 2: Check what coordinate range event2 expects
print("\n=== Event4 (mouse) device info ===")
r2 = subprocess.run([ADB, "-s", DEVICE, "shell", "getevent", "-p", "/dev/input/event4"],
                   capture_output=True, text=True, timeout=5)
print(r2.stdout[:500])

# Step 3: Baseline vs idle (no input)
print("\n=== Natural drift test (no input) ===")
p0 = ss("se_baseline")
time.sleep(3)
p1 = ss("se_idle")
drift = compare(p0, p1)
print(f"  Natural drift (3s): {drift:.2f}")

# Step 4: sendevent on event2 - centre of 720x1280 space
print(f"\n=== sendevent event2: tap (360, 640) ===")
p2_pre = ss("se_pre_e2")
sendevent_tap(360, 640, "/dev/input/event2")
time.sleep(1)
p2_post = ss("se_post_e2")
d2 = compare(p2_pre, p2_post)
print(f"  Diff after event2 tap: {d2:.2f}")

# Step 5: sendevent on event4 (mouse) - centre
print(f"\n=== sendevent event4: tap (360, 640) ===")
p3_pre = ss("se_pre_e4")
sendevent_tap(360, 640, "/dev/input/event4")
time.sleep(1)
p3_post = ss("se_post_e4")
d3 = compare(p3_pre, p3_post)
print(f"  Diff after event4 tap: {d3:.2f}")

# Step 6: Try sendevent with ABS_X/ABS_Y instead of ABS_MT
print(f"\n=== sendevent event2: ABS_X/ABS_Y (not multitouch) ===")
p4_pre = ss("se_pre_abs")
cmds = [
    "sendevent /dev/input/event2 3 0 360",   # ABS_X
    "sendevent /dev/input/event2 3 1 640",   # ABS_Y
    "sendevent /dev/input/event2 1 330 1",   # BTN_TOUCH DOWN
    "sendevent /dev/input/event2 0 0 0",     # SYN_REPORT
]
for cmd in cmds:
    subprocess.run([ADB, "-s", DEVICE, "shell"] + cmd.split(),
                  capture_output=True, timeout=5)
time.sleep(0.1)
cmds2 = [
    "sendevent /dev/input/event2 1 330 0",   # BTN_TOUCH UP
    "sendevent /dev/input/event2 0 0 0",     # SYN_REPORT
]
for cmd in cmds2:
    subprocess.run([ADB, "-s", DEVICE, "shell"] + cmd.split(),
                  capture_output=True, timeout=5)
time.sleep(1)
p4_post = ss("se_post_abs")
d4 = compare(p4_pre, p4_post)
print(f"  Diff after ABS_XY tap: {d4:.2f}")

# Step 7: Try clicking the call button position
# In 720x1280: call at (542*720/1080, 1830*1280/1920) = (361, 1220)
print(f"\n=== sendevent event2: CALL BUTTON at (361, 1220) ===")
p5_pre = ss("se_pre_call")
sendevent_tap(361, 1220, "/dev/input/event2")
time.sleep(1)
p5_post = ss("se_post_call")
d5 = compare(p5_pre, p5_post)
print(f"  Diff after call tap: {d5:.2f}")

print(f"\n=== Summary ===")
print(f"  Natural drift:  {drift:.2f}")
print(f"  event2 tap:     {d2:.2f}")
print(f"  event4 tap:     {d3:.2f}")
print(f"  ABS_XY tap:     {d4:.2f}")
print(f"  Call button:    {d5:.2f}")
