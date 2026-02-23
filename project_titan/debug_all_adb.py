"""Try ALL ADB input methods systematically."""
import subprocess, time, os
import numpy as np
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

def ss(name):
    r = subprocess.run([ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                      capture_output=True, timeout=10)
    path = f"reports/{name}.png"
    os.makedirs("reports", exist_ok=True)
    with open(path, "wb") as f:
        f.write(r.stdout)
    return path

def compare(p1, p2):
    i1 = np.array(Image.open(p1))[:,:,:3]
    i2 = np.array(Image.open(p2))[:,:,:3]
    return np.abs(i1.astype(int) - i2.astype(int)).mean()

def run_adb(*args, timeout=10):
    cmd = [ADB, "-s", DEVICE] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

# Coordinates: centre of 1080x1920 space
x, y = 540, 960

# First get full event2 device info
print("=== Full event2 device info ===")
out, err, _ = run_adb("shell", "getevent", "-p", "/dev/input/event2")
print(out)

print("\n=== Full event4 device info ===")
out2, err2, _ = run_adb("shell", "getevent", "-p", "/dev/input/event4")
print(out2)

# Baseline
print("\n=== Baseline ===")
p0 = ss("adb_base")
time.sleep(2)
p_drift = ss("adb_drift")
drift = compare(p0, p_drift)
print(f"  Natural drift (2s): {drift:.2f}")

# Test 1: input touchscreen tap
print(f"\n=== Test 1: input touchscreen tap {x} {y} ===")
p1a = ss("adb_t1_pre")
out, err, rc = run_adb("shell", "input", "touchscreen", "tap", str(x), str(y))
print(f"  stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(1)
p1b = ss("adb_t1_post")
d1 = compare(p1a, p1b)
print(f"  Diff: {d1:.2f}")

# Test 2: input mouse tap (different source!)
print(f"\n=== Test 2: input mouse tap {x} {y} ===")
p2a = ss("adb_t2_pre")
out, err, rc = run_adb("shell", "input", "mouse", "tap", str(x), str(y))
print(f"  stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(1)
p2b = ss("adb_t2_post")
d2 = compare(p2a, p2b)
print(f"  Diff: {d2:.2f}")

# Test 3: input tablet tap
print(f"\n=== Test 3: input tablet tap {x} {y} ===")
p3a = ss("adb_t3_pre")
out, err, rc = run_adb("shell", "input", "tablet", "tap", str(x), str(y))
print(f"  stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(1)
p3b = ss("adb_t3_post")
d3 = compare(p3a, p3b)
print(f"  Diff: {d3:.2f}")

# Test 4: input swipe (very short, acts like tap)
print(f"\n=== Test 4: input swipe {x} {y} {x} {y} 100 ===")
p4a = ss("adb_t4_pre")
out, err, rc = run_adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "100")
print(f"  stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(1)
p4b = ss("adb_t4_post")
d4 = compare(p4a, p4b)
print(f"  Diff: {d4:.2f}")

# Test 5: input motionevent (available on some Android versions)
print(f"\n=== Test 5: input motionevent DOWN {x} {y} then UP ===")
p5a = ss("adb_t5_pre")
out, err, rc = run_adb("shell", "input", "motionevent", "DOWN", str(x), str(y))
print(f"  DOWN: stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(0.1)
out2, err2, rc2 = run_adb("shell", "input", "motionevent", "UP", str(x), str(y))
print(f"  UP: stdout='{out2}' stderr='{err2}' rc={rc2}")
time.sleep(1)
p5b = ss("adb_t5_post")
d5 = compare(p5a, p5b)
print(f"  Diff: {d5:.2f}")

# Test 6: Use 720x1280 physical coords instead
print(f"\n=== Test 6: input tap 360 640 (720x1280 physical) ===")
p6a = ss("adb_t6_pre")
# Reset wm size first
run_adb("shell", "wm", "size", "reset")
time.sleep(0.5)
out, err, rc = run_adb("shell", "input", "touchscreen", "tap", "360", "640")
print(f"  stdout='{out}' stderr='{err}' rc={rc}")
time.sleep(1)
p6b = ss("adb_t6_post")
d6 = compare(p6a, p6b)
print(f"  Diff: {d6:.2f}")
# Restore override
run_adb("shell", "wm", "size", "1080x1920")
time.sleep(0.5)

# Test 7: sendevent to event2 with FULL multitouch protocol
print(f"\n=== Test 7: Full multitouch sendevent (event2) ===")
# Get the full protocol from getevent -p 
# ABS codes: 2f=MT_SLOT, 35=MT_POSITION_X, 36=MT_POSITION_Y, 39=MT_TRACKING_ID
p7a = ss("adb_t7_pre")
cmds = [
    "sendevent /dev/input/event2 3 47 0",       # ABS_MT_SLOT = 0
    "sendevent /dev/input/event2 3 57 100",      # ABS_MT_TRACKING_ID = 100
    "sendevent /dev/input/event2 3 53 540",      # ABS_MT_POSITION_X = 540
    "sendevent /dev/input/event2 3 54 960",      # ABS_MT_POSITION_Y = 960
    "sendevent /dev/input/event2 3 48 5",        # ABS_MT_TOUCH_MAJOR = 5
    "sendevent /dev/input/event2 3 58 50",       # ABS_MT_PRESSURE = 50
    "sendevent /dev/input/event2 1 330 1",       # BTN_TOUCH = 1
    "sendevent /dev/input/event2 0 0 0",         # SYN_REPORT
]
for cmd in cmds:
    run_adb("shell", *cmd.split(), timeout=5)
time.sleep(0.15)
release = [
    "sendevent /dev/input/event2 3 47 0",
    "sendevent /dev/input/event2 3 57 -1",       # ABS_MT_TRACKING_ID = -1 (release)
    "sendevent /dev/input/event2 1 330 0",
    "sendevent /dev/input/event2 0 0 0",
]
for cmd in release:
    run_adb("shell", *cmd.split(), timeout=5)
time.sleep(1)
p7b = ss("adb_t7_post")
d7 = compare(p7a, p7b)
print(f"  Diff: {d7:.2f}")

print(f"\n{'='*50}")
print(f"=== SUMMARY ===")
print(f"{'='*50}")
print(f"  Natural drift:         {drift:.2f}")
print(f"  touchscreen tap:       {d1:.2f}")
print(f"  mouse tap:             {d2:.2f}")
print(f"  tablet tap:            {d3:.2f}")
print(f"  swipe (as tap):        {d4:.2f}")
print(f"  motionevent DOWN/UP:   {d5:.2f}")
print(f"  physical coords tap:   {d6:.2f}")
print(f"  full MT sendevent:     {d7:.2f}")

# Any that are significantly above drift?
results = {"touchscreen": d1, "mouse": d2, "tablet": d3, "swipe": d4, 
           "motionevent": d5, "phys_coords": d6, "full_mt": d7}
working = {k: v for k, v in results.items() if v > drift * 3}
if working:
    print(f"\n*** WORKING METHODS: {working} ***")
else:
    print(f"\n*** NO METHOD WORKED. Need scrcpy or Interception driver. ***")
