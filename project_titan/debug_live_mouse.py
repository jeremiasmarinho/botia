"""Quick live test: input mouse tap on Call button with before/after screenshots."""
import subprocess, time, sys, numpy as np

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEV = "emulator-5554"

def screenshot():
    r = subprocess.run([ADB, "-s", DEV, "shell", "screencap", "-p"],
                       capture_output=True, timeout=10)
    # Remove \r\n -> \n (adb line ending fix)
    return r.stdout.replace(b'\r\n', b'\n')

def diff_images(a: bytes, b: bytes) -> float:
    """Pixel-level diff between two PNG screenshots."""
    from io import BytesIO
    from PIL import Image
    ia = np.array(Image.open(BytesIO(a)))
    ib = np.array(Image.open(BytesIO(b)))
    if ia.shape != ib.shape:
        return -1.0
    return float(np.mean(np.abs(ia.astype(float) - ib.astype(float))))

# --- Natural drift baseline ---
print("=== Natural drift (no action) ===")
s1 = screenshot(); time.sleep(1.5); s2 = screenshot()
drift = diff_images(s1, s2)
print(f"  drift = {drift:.2f}")

# --- input mouse tap on Call (542, 1830) ---
print("\n=== input mouse tap 542 1830 (Call button) ===")
before = screenshot()
subprocess.run([ADB, "-s", DEV, "shell", "input", "mouse", "tap", "542", "1830"],
               capture_output=True, timeout=10)
time.sleep(2.0)  # wait for UI reaction
after = screenshot()
d = diff_images(before, after)
print(f"  diff = {d:.2f}  (drift={drift:.2f}, ratio={d/max(drift,0.01):.1f}x)")

if d > drift * 3:
    print("  ✅ CLICK REGISTERED — significant screen change!")
else:
    print("  ❌ No significant change — click may not have registered.")

# --- input mouse tap on Fold (189, 1830) ---
print("\n=== input mouse tap 189 1830 (Fold button) ===")
before2 = screenshot()
subprocess.run([ADB, "-s", DEV, "shell", "input", "mouse", "tap", "189", "1830"],
               capture_output=True, timeout=10)
time.sleep(2.0)
after2 = screenshot()
d2 = diff_images(before2, after2)
print(f"  diff = {d2:.2f}  (drift={drift:.2f}, ratio={d2/max(drift,0.01):.1f}x)")

if d2 > drift * 3:
    print("  ✅ CLICK REGISTERED — significant screen change!")
else:
    print("  ❌ No significant change — click may not have registered.")

print("\nDone.")
