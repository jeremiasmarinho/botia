"""Test LDPlayer ldconsole operaterecord for touch injection."""
import subprocess, time, os, json

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
LDCONSOLE = r"F:\LDPlayer\LDPlayer9\ldconsole.exe"
DEVICE = "emulator-5554"

def screenshot(name):
    r = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                      capture_output=True, timeout=10)
    path = f"reports/{name}.png"
    with open(path, "wb") as f:
        f.write(r.stdout)
    print(f"  {name}: {len(r.stdout):,} bytes")
    return path

def operate(content_list):
    """Send operaterecord command."""
    content = json.dumps(content_list)
    print(f"  operaterecord: {content}")
    r = subprocess.run([LDCONSOLE, "operaterecord", "--index", "0", 
                       "--content", content],
                      capture_output=True, text=True, timeout=10)
    print(f"  result: {r.stdout.strip()} exit={r.returncode}")
    return r

def adb_tap(x, y):
    """ADB input tap (for comparison)."""
    r = subprocess.run([ADB, "-s", DEVICE, "shell", "input", "tap", str(x), str(y)],
                      capture_output=True, text=True, timeout=10)
    print(f"  adb tap ({x},{y}): exit={r.returncode}")

# Get current resolution  
r = subprocess.run([ADB, "-s", DEVICE, "shell", "wm", "size"],
                  capture_output=True, text=True, timeout=5)
print(f"Android size: {r.stdout.strip()}")

r2 = subprocess.run([ADB, "-s", DEVICE, "shell", "wm", "density"],
                   capture_output=True, text=True, timeout=5)
print(f"Android density: {r2.stdout.strip()}")

# Physical resolution is 720x1280 (from ldconsole list2)
print("\nPhysical: 720x1280, Override: 1080x1920")

# ===== TEST 1: Screenshot baseline =====
print("\n=== Screenshot baseline ===")
screenshot("operate_baseline")

# ===== TEST 2: Try different operaterecord formats =====
print("\n=== Test format 1: basic touch ===")
# Centre of screen in 720x1280 = (360, 640)
operate([{"timing": 0, "eid": 1, "sx": 360, "sy": 640}])
time.sleep(1)
screenshot("operate_f1")

print("\n=== Test format 2: touch with type ===")
operate([{"timing": 0, "type": "touch", "x": 360, "y": 640}])
time.sleep(1)
screenshot("operate_f2")

print("\n=== Test format 3: touch down + up ===")
operate([
    {"timing": 0, "eid": 1, "sx": 360, "sy": 640, "state": 0},  # down
    {"timing": 100, "eid": 1, "sx": 360, "sy": 640, "state": 1}  # up
])
time.sleep(1)
screenshot("operate_f3")

print("\n=== Test format 4: operaterecord with input action ===")
# Try action command instead  
r = subprocess.run([LDCONSOLE, "action", "--index", "0", 
                   "--key", "call.input", "--value", "360 640 0"],
                  capture_output=True, text=True, timeout=10)
print(f"  action result: {r.stdout.strip()} exit={r.returncode}")
time.sleep(1)
screenshot("operate_f4")

# ===== TEST 3: Try adb through ldconsole =====
print("\n=== Test: adb via ldconsole ===")
r = subprocess.run([LDCONSOLE, "adb", "--index", "0",
                   "--command", "shell input tap 360 640"],
                  capture_output=True, text=True, timeout=10)
print(f"  ldconsole adb: {r.stdout.strip()} exit={r.returncode}")
time.sleep(1)
screenshot("operate_adb")

# ===== TEST 4: Try input in 1080x1920 (override) coords =====
print("\n=== Test: adb tap in 1080x1920 coords ===")
adb_tap(540, 960)
time.sleep(1)
screenshot("operate_adb_override")

# ===== TEST 5: Try sendevent directly =====
print("\n=== Test: sendevent to event2 ===")
# Simulate touch: ABS_MT_TRACKING_ID, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, SYN_REPORT
cmds = [
    "sendevent /dev/input/event2 3 57 0",       # ABS_MT_TRACKING_ID = 0
    "sendevent /dev/input/event2 3 53 360",      # ABS_MT_POSITION_X = 360 
    "sendevent /dev/input/event2 3 54 640",      # ABS_MT_POSITION_Y = 640
    "sendevent /dev/input/event2 1 330 1",       # BTN_TOUCH DOWN
    "sendevent /dev/input/event2 0 0 0",         # SYN_REPORT
]
for cmd in cmds:
    subprocess.run([ADB, "-s", DEVICE, "shell", cmd],
                  capture_output=True, timeout=5)
time.sleep(0.1)
# Release
cmds2 = [
    "sendevent /dev/input/event2 3 57 -1",       # ABS_MT_TRACKING_ID = -1 (release)
    "sendevent /dev/input/event2 1 330 0",        # BTN_TOUCH UP  
    "sendevent /dev/input/event2 0 0 0",          # SYN_REPORT
]
for cmd in cmds2:
    subprocess.run([ADB, "-s", DEVICE, "shell", cmd],
                  capture_output=True, timeout=5)
time.sleep(1)
screenshot("operate_sendevent")

print("\n=== Comparing all screenshots ===")
import numpy as np
from PIL import Image

baseline = np.array(Image.open("reports/operate_baseline.png"))[:,:,:3]

for name in ["operate_f1", "operate_f2", "operate_f3", "operate_f4",
             "operate_adb", "operate_adb_override", "operate_sendevent"]:
    try:
        img = np.array(Image.open(f"reports/{name}.png"))[:,:,:3]
        diff = np.abs(baseline.astype(int) - img.astype(int)).mean()
        print(f"  {name:25s}: mean_diff={diff:.2f}")
    except:
        print(f"  {name:25s}: FAILED")

print("\nDone!")
