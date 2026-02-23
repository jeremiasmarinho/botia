"""Live benchmark: persistent ADB shell vs subprocess per-click.

Tests both approaches at a safe area of the screen (center of table)
and measures latency.
"""
import subprocess
import time
import sys
import os

ADB = r"F:\LDPlayer\LDPlayer9\adb.exe"
DEVICE = "emulator-5554"

# Safe tap point — center of table, no buttons
SAFE_X, SAFE_Y = 360, 640


def benchmark_subprocess(n: int = 5) -> list[float]:
    """Benchmark: new subprocess per click."""
    times = []
    for i in range(n):
        t0 = time.perf_counter()
        subprocess.run(
            [ADB, "-s", DEVICE, "shell", "input", "touchscreen", "tap",
             str(SAFE_X), str(SAFE_Y)],
            timeout=5, capture_output=True,
        )
        dt = time.perf_counter() - t0
        times.append(dt)
        print(f"  subprocess #{i+1}: {dt*1000:.1f} ms")
    return times


def benchmark_persistent(n: int = 5) -> list[float]:
    """Benchmark: persistent ADB shell via stdin pipe."""
    proc = subprocess.Popen(
        [ADB, "-s", DEVICE, "shell"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    # Warm up — first command may be slower
    proc.stdin.write(b"echo READY\n")
    proc.stdin.flush()
    time.sleep(0.5)

    times = []
    for i in range(n):
        cmd = f"input touchscreen tap {SAFE_X} {SAFE_Y}\n"
        t0 = time.perf_counter()
        proc.stdin.write(cmd.encode())
        proc.stdin.flush()
        dt = time.perf_counter() - t0
        times.append(dt)
        print(f"  persistent #{i+1}: {dt*1000:.3f} ms (write+flush)")
        time.sleep(0.3)  # Give time for command to execute

    # Clean up
    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=3)
    return times


def benchmark_sendevent(n: int = 3) -> list[float]:
    """Benchmark: raw sendevent via persistent shell."""
    proc = subprocess.Popen(
        [ADB, "-s", DEVICE, "shell"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    proc.stdin.write(b"echo READY\n")
    proc.stdin.flush()
    time.sleep(0.5)

    d = "/dev/input/event2"
    # Coordinate transform: display (x, y) -> touch (y, x)
    touch_x = SAFE_Y  # display Y -> kernel X
    touch_y = SAFE_X  # display X -> kernel Y

    times = []
    for i in range(n):
        down = (
            f"sendevent {d} 3 47 0;"
            f"sendevent {d} 3 57 1;"
            f"sendevent {d} 3 53 {touch_x};"
            f"sendevent {d} 3 54 {touch_y};"
            f"sendevent {d} 3 58 1;"
            f"sendevent {d} 1 330 1;"
            f"sendevent {d} 1 325 1;"
            f"sendevent {d} 0 0 0\n"
        )
        up = (
            f"sendevent {d} 3 47 0;"
            f"sendevent {d} 3 57 -1;"
            f"sendevent {d} 1 330 0;"
            f"sendevent {d} 1 325 0;"
            f"sendevent {d} 0 0 0\n"
        )

        t0 = time.perf_counter()
        proc.stdin.write(down.encode())
        proc.stdin.flush()
        time.sleep(0.04)  # Hold 40ms
        proc.stdin.write(up.encode())
        proc.stdin.flush()
        dt = time.perf_counter() - t0
        times.append(dt)
        print(f"  sendevent #{i+1}: {dt*1000:.1f} ms (down+hold+up)")
        time.sleep(0.5)

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=3)
    return times


if __name__ == "__main__":
    print("=" * 60)
    print("PERSISTENT ADB SHELL BENCHMARK")
    print("=" * 60)
    print(f"Target: ({SAFE_X}, {SAFE_Y}) — center of table (safe area)")
    print()

    print("[1/3] Subprocess per-click (baseline):")
    sub_times = benchmark_subprocess(5)
    print(f"  AVG: {sum(sub_times)/len(sub_times)*1000:.1f} ms\n")

    print("[2/3] Persistent shell (stdin pipe):")
    per_times = benchmark_persistent(5)
    print(f"  AVG: {sum(per_times)/len(per_times)*1000:.3f} ms (write+flush only)\n")

    print("[3/3] Raw sendevent (kernel bypass):")
    sev_times = benchmark_sendevent(3)
    print(f"  AVG: {sum(sev_times)/len(sev_times)*1000:.1f} ms (includes 40ms hold)\n")

    print("=" * 60)
    speedup = (sum(sub_times)/len(sub_times)) / max(sum(per_times)/len(per_times), 0.0001)
    print(f"SPEEDUP: persistent shell is {speedup:.0f}x faster than subprocess")
    print(f"  subprocess avg: {sum(sub_times)/len(sub_times)*1000:.1f} ms")
    print(f"  persistent avg: {sum(per_times)/len(per_times)*1000:.3f} ms")
    print(f"  sendevent  avg: {sum(sev_times)/len(sev_times)*1000:.1f} ms")
    print("=" * 60)
