"""Test ALL ADB click methods at native 720x1280 resolution."""
import subprocess, time, numpy as np
from io import BytesIO
from PIL import Image

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"

def screenshot():
    r = subprocess.run([ADB, "-s", DEV, "shell", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return r.stdout.replace(b'\r\n', b'\n')

def to_img(data):
    return np.array(Image.open(BytesIO(data)))

def diff(a, b):
    ia, ib = to_img(a), to_img(b)
    if ia.shape != ib.shape: return -1.0
    return float(np.mean(np.abs(ia.astype(float) - ib.astype(float))))

# Verify no override
r = subprocess.run([ADB, "-s", DEV, "shell", "wm", "size"],
                   capture_output=True, text=True, timeout=5)
print(f"Resolution: {r.stdout.strip()}")

# Natural drift
print("\n=== Natural drift ===")
drifts = []
for i in range(3):
    s1 = screenshot(); time.sleep(1.5); s2 = screenshot()
    d = diff(s1, s2)
    drifts.append(d)
    print(f"  drift[{i}] = {d:.2f}")
avg_drift = max(np.mean(drifts), 0.01)
print(f"  avg = {avg_drift:.2f}")

# Test methods on center (360, 640) - safe neutral location
center_x, center_y = 360, 640

methods = [
    ("input tap {x} {y}", "default tap"),
    ("input touchscreen tap {x} {y}", "touchscreen tap"),
    ("input mouse tap {x} {y}", "mouse tap"),
]

for cmd_template, label in methods:
    cmd = cmd_template.format(x=center_x, y=center_y)
    print(f"\n=== {label}: {cmd} ===")
    time.sleep(2)
    before = screenshot()
    r = subprocess.run([ADB, "-s", DEV, "shell"] + cmd.split(),
                       capture_output=True, timeout=10, text=True)
    time.sleep(2)
    after = screenshot()
    d = diff(before, after)
    ratio = d / avg_drift
    status = "✅ WORKS" if d > avg_drift * 3 else "❌ NO CHANGE"
    print(f"  diff={d:.2f} ratio={ratio:.1f}x {status}")
    if r.stderr.strip():
        print(f"  stderr: {r.stderr.strip()[:100]}")

# Now test on the CALL button (361, 1220) with each method
call_x, call_y = 361, 1220
print(f"\n{'='*60}")
print(f"=== Testing on CALL button ({call_x},{call_y}) ===")
print(f"{'='*60}")

for cmd_template, label in methods:
    cmd = cmd_template.format(x=call_x, y=call_y)
    print(f"\n=== {label}: {cmd} ===")
    time.sleep(3)  # wait for UI settle / new hand
    before = screenshot()
    # Check if call button is visible
    img = to_img(before)
    px = img[call_y, call_x, :3]
    is_green = px[1] > 100 and px[0] < 80
    print(f"  button pixel: RGB={px}, is_green={is_green}")
    if not is_green:
        print(f"  ⚠ Call button not visible, skipping")
        continue
    
    r = subprocess.run([ADB, "-s", DEV, "shell"] + cmd.split(),
                       capture_output=True, timeout=10, text=True)
    time.sleep(2)
    after = screenshot()
    d = diff(before, after)
    ratio = d / avg_drift
    status = "✅ CLICKED!" if d > avg_drift * 3 else "❌ NO CHANGE"
    print(f"  diff={d:.2f} ratio={ratio:.1f}x {status}")
    if r.stderr.strip():
        print(f"  stderr: {r.stderr.strip()[:100]}")

print("\nDone.")
