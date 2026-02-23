"""Rigorous test: verify which input methods work now, with stderr capture."""
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

# --- Check stderr for each command ---
print("=== Checking stderr/stdout for input commands ===")
for cmd in [
    "input mouse tap 540 960",
    "input touchscreen tap 540 960",
    "input tap 540 960",
    "input mouse --help",
]:
    r = subprocess.run([ADB, "-s", DEV, "shell"] + cmd.split(),
                       capture_output=True, timeout=10)
    print(f"\n  '{cmd}':")
    print(f"    returncode={r.returncode}")
    if r.stdout.strip():
        print(f"    stdout: {r.stdout.decode(errors='replace')[:200]}")
    if r.stderr.strip():
        print(f"    stderr: {r.stderr.decode(errors='replace')[:200]}")

# --- Rigorous diff tests ---
print("\n=== Natural drift x3 ===")
drifts = []
for i in range(3):
    s1 = screenshot(); time.sleep(1.5); s2 = screenshot()
    d = diff(s1, s2)
    drifts.append(d)
    print(f"  drift[{i}] = {d:.2f}")
avg_drift = np.mean(drifts)
print(f"  avg_drift = {avg_drift:.2f}")

# Test each method 2x
methods = [
    ("input touchscreen tap 542 1830", "touchscreen call"),
    ("input touchscreen tap 542 1830", "touchscreen call x2"),
    ("input tap 542 1830", "default tap call"),
    ("input mouse tap 542 1830", "mouse tap call"),
]

for cmd, label in methods:
    print(f"\n=== {label}: {cmd} ===")
    time.sleep(2)  # wait for UI to settle
    before = screenshot()
    r = subprocess.run([ADB, "-s", DEV, "shell"] + cmd.split(),
                       capture_output=True, timeout=10)
    time.sleep(2)
    after = screenshot()
    d = diff(before, after)
    ratio = d / max(avg_drift, 0.01)
    status = "✅ WORKS" if d > avg_drift * 3 else "❌ BLOCKED"
    print(f"  diff={d:.2f} ratio={ratio:.1f}x {status}")
    if r.returncode != 0 or r.stderr.strip():
        print(f"  rc={r.returncode} stderr={r.stderr.decode(errors='replace')[:100]}")

print("\nDone.")
