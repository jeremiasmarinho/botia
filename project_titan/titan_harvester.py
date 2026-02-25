"""titan_harvester.py — Gold-Standard Dataset Builder for YOLO Retraining.

Autonomous frame harvester for MuMu Player (or any emulator via profile).
Captures the raw nemuwin surface at 720×1280, applies perceptual-hash
de-duplication to guarantee every saved frame is visually distinct, and
writes clean PNGs to dataset_raw/ with microsecond timestamps.

Designed to run unattended while watching PLO5/PLO6 tables:
    python titan_harvester.py                        # defaults: 3s, 500 frames
    python titan_harvester.py --target 1000          # 1000 unique frames
    python titan_harvester.py --interval 2 --adb     # 2s, ADB capture
    python titan_harvester.py --duration 3600         # run for 1 hour
    python titan_harvester.py --hamming 6             # stricter dedup (default 8)

Press Ctrl+C at any time — progress is printed live and a summary is shown.

Capture Backends (auto-selected, override with --adb or --win32):
  1. Win32 + mss  (default, ~15ms, no shell overhead)
  2. ADB screencap (fallback, ~300ms, works even if window is occluded)
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import io
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────
ANDROID_W, ANDROID_H = 720, 1280
OUTPUT_DIR = Path(__file__).resolve().parent / "dataset_raw"
DEFAULT_INTERVAL = 3.0
DEFAULT_TARGET = 500
DEFAULT_HAMMING = 8       # max hamming distance to consider "same frame"
PHASH_SIZE = 8            # 8×8 DCT → 64-bit hash

# ── Win32 helpers ─────────────────────────────────────────────────────

_MUMU_CLASSES = {"Qt5156QWindowIcon", "Qt5154QWindowIcon", "Qt5QWindowIcon"}
_RENDER_CHILD = "nemuwin"


class _CaptureBackend:
    """Abstract capture backend."""

    def grab(self) -> "np.ndarray | None":
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────
#  Win32 + mss backend
# ──────────────────────────────────────────────────────────────────────
class Win32Backend(_CaptureBackend):
    """Captures the nemuwin surface via ClientToScreen + mss.grab()."""

    def __init__(self) -> None:
        self._hwnd: int | None = 0

    def connect(self) -> bool:
        self._hwnd = _find_nemuwin_hwnd()
        return self._hwnd not in (None, 0)

    def info(self) -> str:
        if not self._hwnd:
            return "not connected"
        user32 = ctypes.windll.user32
        cname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(self._hwnd, cname, 256)
        rect = wt.RECT()
        user32.GetClientRect(self._hwnd, ctypes.byref(rect))
        return f"hwnd={self._hwnd}  class='{cname.value}'  client={rect.right}×{rect.bottom}"

    def grab(self) -> "np.ndarray | None":
        import cv2
        import mss
        import numpy as np

        user32 = ctypes.windll.user32

        # Verify window still alive
        if not user32.IsWindow(self._hwnd):
            # Try reconnect once
            if not self.connect():
                return None

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _PT(0, 0)
        user32.ClientToScreen(self._hwnd, ctypes.byref(pt))
        rect = wt.RECT()
        user32.GetClientRect(self._hwnd, ctypes.byref(rect))
        cw, ch = rect.right, rect.bottom
        if cw <= 0 or ch <= 0:
            return None

        with mss.mss() as sct:
            monitor = {"left": pt.x, "top": pt.y, "width": cw, "height": ch}
            raw = np.array(sct.grab(monitor))

        frame = raw[:, :, :3].copy()  # BGRA → BGR
        if frame.shape[1] != ANDROID_W or frame.shape[0] != ANDROID_H:
            frame = cv2.resize(frame, (ANDROID_W, ANDROID_H),
                               interpolation=cv2.INTER_LINEAR)
        return frame


# ──────────────────────────────────────────────────────────────────────
#  ADB screencap backend
# ──────────────────────────────────────────────────────────────────────
class ADBBackend(_CaptureBackend):
    """Captures via `adb exec-out screencap -p`."""

    def __init__(self, adb_exe: str = "", device: str = "") -> None:
        self._adb_exe = adb_exe
        self._device = device

    def connect(self) -> bool:
        # Resolve ADB path
        if not self._adb_exe:
            self._adb_exe = _resolve_adb_path()
        if not self._adb_exe:
            return False

        # Resolve device serial
        if not self._device:
            self._device = _resolve_adb_device()

        # Verify ADB connection
        try:
            r = subprocess.run(
                [self._adb_exe, "-s", self._device, "get-state"],
                capture_output=True, timeout=5, text=True,
            )
            return "device" in r.stdout.strip()
        except Exception:
            return False

    def info(self) -> str:
        return f"adb={self._adb_exe}  device={self._device}"

    def grab(self) -> "np.ndarray | None":
        import cv2
        import numpy as np

        try:
            r = subprocess.run(
                [self._adb_exe, "-s", self._device,
                 "exec-out", "screencap", "-p"],
                capture_output=True, timeout=8,
            )
            if r.returncode != 0 or len(r.stdout) < 1000:
                return None
            buf = np.frombuffer(r.stdout, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return None
            if img.shape[1] != ANDROID_W or img.shape[0] != ANDROID_H:
                img = cv2.resize(img, (ANDROID_W, ANDROID_H),
                                 interpolation=cv2.INTER_LINEAR)
            return img
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────────
#  Perceptual Hash engine (DCT-based, no external deps beyond numpy)
# ──────────────────────────────────────────────────────────────────────
class PHashEngine:
    """64-bit perceptual hash via DCT on 32×32 grayscale thumbnail.

    Hamming distance < threshold → visually "same" frame.
    """

    def __init__(self, threshold: int = DEFAULT_HAMMING) -> None:
        self.threshold = threshold
        self._hashes: list[int] = []

    def compute(self, frame_bgr: "np.ndarray") -> int:
        """Compute 64-bit pHash of a BGR frame."""
        import cv2
        import numpy as np

        # Resize to 32×32 grayscale
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        small = small.astype(np.float64)

        # DCT
        dct = cv2.dct(small)

        # Take top-left 8×8 low-frequency components (skip DC at [0,0])
        dct_low = dct[:PHASH_SIZE, :PHASH_SIZE]
        med = np.median(dct_low)

        # Build 64-bit hash
        bits = (dct_low > med).flatten()
        h = 0
        for b in bits:
            h = (h << 1) | int(b)
        return h

    def is_duplicate(self, phash: int) -> bool:
        """Check if this hash is within hamming distance of any stored hash."""
        for stored in self._hashes:
            dist = bin(phash ^ stored).count("1")
            if dist <= self.threshold:
                return True
        return False

    def register(self, phash: int) -> None:
        self._hashes.append(phash)

    @property
    def count(self) -> int:
        return len(self._hashes)


# ──────────────────────────────────────────────────────────────────────
#  Frame quality filter
# ──────────────────────────────────────────────────────────────────────
def _is_valid_game_frame(frame: "np.ndarray") -> tuple[bool, str]:
    """Multi-criteria quality check. Returns (valid, reason)."""
    import numpy as np

    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return False, "empty"

    mean_val = float(np.mean(frame))
    if mean_val < 12:
        return False, f"black (mean={mean_val:.0f})"
    if mean_val > 248:
        return False, f"white (mean={mean_val:.0f})"

    # Check variance per channel — solid color screens
    stds = [float(np.std(frame[:, :, c])) for c in range(3)]
    max_std = max(stds)
    if max_std < 6:
        return False, f"uniform (std={max_std:.1f})"

    # Check if the frame has enough "game content"
    # A poker table has dark-green felt + cards + UI elements
    # Reject loading screens / splash screens (usually centered bright rectangle)
    center_crop = frame[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
    center_std = float(np.std(center_crop))
    if center_std < 10:
        return False, f"loading (center_std={center_std:.1f})"

    return True, "ok"


# ──────────────────────────────────────────────────────────────────────
#  Win32 window discovery
# ──────────────────────────────────────────────────────────────────────
def _find_nemuwin_hwnd() -> Optional[int]:
    """Find the nemuwin render surface HWND of MuMu Player."""
    user32 = ctypes.windll.user32

    # Step 1: Find MuMu top-level windows
    candidates: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        cname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cname, 256)
        if cname.value in _MUMU_CLASSES:
            candidates.append(hwnd)
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if "mumu" in title.value.lower():
            candidates.append(hwnd)
        return True

    user32.EnumWindows(_enum_top, 0)
    if not candidates:
        return None

    # Step 2: Find nemuwin child in each candidate
    for main_hwnd in candidates:
        best: int | None = None
        best_area: int = 0

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _enum_child(hwnd: int, _lp: int) -> bool:
            nonlocal best, best_area
            cname = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cname, 256)
            if cname.value.lower() == _RENDER_CHILD:
                best = hwnd
                best_area = 10**9
                return True
            if user32.IsWindowVisible(hwnd):
                rect = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                area = rect.right * rect.bottom
                if area > best_area and best_area < 10**9:
                    best_area = area
                    best = hwnd
            return True

        user32.EnumChildWindows(main_hwnd, _enum_child, 0)
        if best and best != 0:
            return best

    return candidates[0] if candidates else None


# ──────────────────────────────────────────────────────────────────────
#  ADB resolution helpers
# ──────────────────────────────────────────────────────────────────────
def _resolve_adb_path() -> str:
    """Resolve ADB executable path from env / known locations."""
    env = os.getenv("TITAN_ADB_PATH", "")
    if env and os.path.isfile(env):
        return env

    known = [
        r"F:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        r"D:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        r"F:\LDPlayer\LDPlayer9\adb.exe",
        r"C:\LDPlayer\LDPlayer9\adb.exe",
    ]
    for p in known:
        if os.path.isfile(p):
            return p
    return ""


def _resolve_adb_device() -> str:
    """Resolve ADB device serial from env / defaults."""
    env = os.getenv("TITAN_ADB_DEVICE", "").strip()
    if env:
        return env
    return "127.0.0.1:16384"  # MuMu default


# ──────────────────────────────────────────────────────────────────────
#  Main harvest loop
# ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Titan Harvester — Gold-Standard Dataset Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python titan_harvester.py                    # 500 frames, 3s interval
  python titan_harvester.py --target 1000      # 1000 unique frames
  python titan_harvester.py --adb              # force ADB backend
  python titan_harvester.py --hamming 5        # stricter dedup
  python titan_harvester.py --duration 1800    # run for 30 min
""",
    )
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"Capture interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET,
                        help=f"Stop after N unique frames (default: {DEFAULT_TARGET})")
    parser.add_argument("--duration", type=float, default=0,
                        help="Max runtime in seconds (0 = no limit)")
    parser.add_argument("--hamming", type=int, default=DEFAULT_HAMMING,
                        help=f"Perceptual hash hamming threshold (default: {DEFAULT_HAMMING})")
    parser.add_argument("--adb", action="store_true",
                        help="Force ADB screencap backend")
    parser.add_argument("--win32", action="store_true",
                        help="Force Win32+mss backend (default)")
    parser.add_argument("--no-filter", action="store_true",
                        help="Disable quality filter (save everything)")
    parser.add_argument("--outdir", type=str, default="",
                        help="Custom output directory")
    args = parser.parse_args()

    # Heavy imports
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        print(f"[FATAL] Missing dependency: {e}")
        print("        pip install opencv-python numpy mss")
        sys.exit(1)

    # Output directory
    outdir = Path(args.outdir) if args.outdir else OUTPUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    # Count existing frames (for resume support)
    existing = list(outdir.glob("titan_*.png"))
    existing_count = len(existing)

    # Banner
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║       TITAN HARVESTER — Gold Standard Dataset Builder       ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Interval:      {args.interval:.1f}s" + " " * (45 - len(f"{args.interval:.1f}s")) + "║")
    print(f"║  Target:        {args.target} unique frames" + " " * (45 - len(f"{args.target} unique frames")) + "║")
    print(f"║  Duration:      {'unlimited' if not args.duration else f'{args.duration:.0f}s'}" + " " * (45 - len(f"{'unlimited' if not args.duration else f'{args.duration:.0f}s'}")) + "║")
    print(f"║  Hamming:       ≤{args.hamming} (perceptual hash)" + " " * (45 - len(f"≤{args.hamming} (perceptual hash)")) + "║")
    print(f"║  Quality:       {'OFF' if args.no_filter else 'ON (auto-reject bad frames)'}" + " " * (45 - len(f"{'OFF' if args.no_filter else 'ON (auto-reject bad frames)'}")) + "║")
    print(f"║  Output:        {str(outdir)[-45:]}" + " " * max(0, 45 - len(str(outdir)[-45:])) + "║")
    if existing_count:
        msg = f"RESUMING — {existing_count} existing frames"
        print(f"║  Resume:        {msg}" + " " * (45 - len(msg)) + "║")
    print("╚══════════════════════════════════════════════════════════════╝")

    # Select backend
    backend: _CaptureBackend
    if args.adb:
        print("\n[...] Connecting via ADB...")
        backend = ADBBackend()
        if not backend.connect():
            print("[FATAL] ADB connection failed!")
            print(f"        Tried: {backend.info()}")
            sys.exit(1)
        print(f"[OK]  ADB connected: {backend.info()}")
    else:
        print("\n[...] Finding MuMu Player window (Win32)...")
        backend = Win32Backend()
        if not backend.connect():
            print("[WARN] Win32 window not found, falling back to ADB...")
            backend = ADBBackend()
            if not backend.connect():
                print("[FATAL] Neither Win32 nor ADB available!")
                sys.exit(1)
            print(f"[OK]  ADB fallback: {backend.info()}")
        else:
            print(f"[OK]  Window found: {backend.info()}")

    # Test capture
    print("[...] Test capture...")
    test = backend.grab()
    if test is None:
        print("[FATAL] Test capture failed — is the emulator visible?")
        sys.exit(1)
    print(f"[OK]  Test frame: {test.shape[1]}×{test.shape[0]}  "
          f"mean={np.mean(test):.0f}  dtype={test.dtype}")

    # Init perceptual hash engine
    phash = PHashEngine(threshold=args.hamming)

    # Pre-register existing frames to avoid re-saving duplicates on resume
    if existing_count > 0:
        print(f"[...] Hashing {existing_count} existing frames for resume...")
        for i, fp in enumerate(existing):
            img = cv2.imread(str(fp))
            if img is not None:
                h = phash.compute(img)
                phash.register(h)
            if (i + 1) % 50 == 0:
                print(f"       ...hashed {i + 1}/{existing_count}")
        print(f"[OK]  {phash.count} hashes registered")

    # Harvest loop
    saved = 0
    skipped_dup = 0
    skipped_quality = 0
    skipped_capture = 0
    start_time = time.time()

    print(f"\n{'─' * 62}")
    print(f"  HARVESTING — target {args.target} unique frames — Ctrl+C to stop")
    print(f"{'─' * 62}\n")

    try:
        while saved < args.target:
            # Duration limit
            elapsed = time.time() - start_time
            if args.duration > 0 and elapsed >= args.duration:
                print(f"\n[TIME] Duration limit reached ({args.duration:.0f}s)")
                break

            # Capture
            t0 = time.perf_counter()
            frame = backend.grab()
            capture_ms = (time.perf_counter() - t0) * 1000

            if frame is None:
                skipped_capture += 1
                _print_status(saved, args.target, skipped_dup, skipped_quality,
                              skipped_capture, "CAPTURE_FAIL", 0, elapsed)
                time.sleep(args.interval)
                continue

            # Quality filter
            if not args.no_filter:
                valid, reason = _is_valid_game_frame(frame)
                if not valid:
                    skipped_quality += 1
                    _print_status(saved, args.target, skipped_dup, skipped_quality,
                                  skipped_capture, f"QUALITY:{reason}", capture_ms, elapsed)
                    time.sleep(args.interval)
                    continue

            # Perceptual hash dedup
            t1 = time.perf_counter()
            frame_hash = phash.compute(frame)
            hash_ms = (time.perf_counter() - t1) * 1000

            if phash.is_duplicate(frame_hash):
                skipped_dup += 1
                _print_status(saved, args.target, skipped_dup, skipped_quality,
                              skipped_capture, "DUPLICATE", capture_ms, elapsed)
                time.sleep(args.interval)
                continue

            # Save frame
            phash.register(frame_hash)
            saved += 1
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"titan_{ts}.png"
            filepath = outdir / filename
            cv2.imwrite(str(filepath), frame,
                        [cv2.IMWRITE_PNG_COMPRESSION, 1])  # fast compression

            size_kb = filepath.stat().st_size / 1024
            pct = saved / args.target * 100
            bar = _progress_bar(pct, 20)
            print(f"  [{_ts()}] {bar} {saved:4d}/{args.target}  "
                  f"{filename}  {size_kb:.0f}KB  "
                  f"cap={capture_ms:.0f}ms hash={hash_ms:.0f}ms  "
                  f"dup={skipped_dup} qf={skipped_quality}")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n\n[STOP] Interrupted by user (Ctrl+C)")

    # Summary
    elapsed = time.time() - start_time
    total_frames = sum(1 for _ in outdir.glob("titan_*.png"))
    total_size_mb = sum(f.stat().st_size for f in outdir.glob("titan_*.png")) / 1024 / 1024

    print(f"\n{'═' * 62}")
    print(f"  HARVEST COMPLETE")
    print(f"{'═' * 62}")
    print(f"  Frames saved this session:  {saved}")
    print(f"  Total frames in dataset:    {total_frames}")
    print(f"  Duplicates rejected:        {skipped_dup}")
    print(f"  Quality rejected:           {skipped_quality}")
    print(f"  Capture failures:           {skipped_capture}")
    print(f"  Runtime:                    {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Dataset size:               {total_size_mb:.1f} MB")
    print(f"  Output:                     {outdir}")
    if saved > 0:
        print(f"  Avg interval:               {elapsed/saved:.1f}s/frame")
    if saved >= args.target:
        print(f"\n  ✓ TARGET REACHED: {args.target} unique frames collected!")
    else:
        remaining = args.target - total_frames
        if remaining > 0:
            est_time = remaining * args.interval
            print(f"\n  → {remaining} more frames needed")
            print(f"  → Re-run to resume (existing frames are auto-detected)")
            print(f"  → ETA at {args.interval}s interval: ~{est_time/60:.0f} min")
    print(f"{'═' * 62}\n")


# ── UI helpers ────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:5.1f}%"


def _print_status(saved: int, target: int, dup: int, qf: int, cf: int,
                  reason: str, cap_ms: float, elapsed: float) -> None:
    """Print skip status on same line pattern (every Nth skip)."""
    total_skip = dup + qf + cf
    # Print every 5th skip to avoid flooding
    if total_skip % 5 == 1 or total_skip <= 3:
        print(f"  [{_ts()}]  skip #{total_skip:3d}  {reason:<25s}  "
              f"saved={saved}/{target}  dup={dup} qf={qf} cf={cf}")


if __name__ == "__main__":
    main()
