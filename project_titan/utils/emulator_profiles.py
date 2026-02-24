"""Emulator abstraction layer for Titan.

Supports **MuMu Player 12** (default) and LDPlayer 9 (legacy).

The active profile is selected via:
  1. ``TITAN_EMULATOR`` env var (``"mumu"`` or ``"ldplayer"``)
  2. ``input.backend`` key in config YAML
  3. Falls back to ``"mumu"``

Usage::

    from utils.emulator_profiles import get_profile, find_render_hwnd

    profile = get_profile()            # -> EmulatorProfile for MuMu
    hwnd = find_render_hwnd(profile)   # -> int | None
    exe = find_console_exe(profile)    # -> str | None
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import subprocess
from dataclasses import dataclass, field

_log = logging.getLogger("EmulatorProfiles")

# ═══════════════════════════════════════════════════════════════════════════
# Profile definition
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EmulatorProfile:
    """Immutable configuration for a supported Android emulator."""

    name: str                          # internal key: "mumu", "ldplayer"
    display_name: str                  # friendly name for logs
    title_pattern: str                 # substring to match in window title

    # Win32 window class names (used by FindWindowW / EnumWindows)
    main_window_classes: tuple[str, ...] = ()
    # Render child class names (prioritised — first match wins)
    render_child_classes: tuple[str, ...] = ()

    # Console CLI tool
    console_exe_name: str = ""
    console_paths: tuple[str, ...] = ()  # default search locations
    env_console_path: str = ""           # env var override

    # ADB
    adb_exe_name: str = "adb.exe"
    adb_paths: tuple[str, ...] = ()
    env_adb_path: str = "TITAN_ADB_PATH"
    default_adb_device: str = ""

    # Chrome borders (pixels to remove from window capture)
    chrome_top: int = 0
    chrome_bottom: int = 0
    chrome_left: int = 0
    chrome_right: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Built-in profiles
# ═══════════════════════════════════════════════════════════════════════════

PROFILES: dict[str, EmulatorProfile] = {
    "mumu": EmulatorProfile(
        name="mumu",
        display_name="MuMu Player 12",
        title_pattern="MuMu",

        # MuMu uses a Qt-based window.  The exact class name varies by
        # Qt build; we include the known variants.  Title-based search
        # ("MuMu" or "Android Device") is the primary discovery mechanism.
        main_window_classes=("Qt5156QWindowIcon", "Qt5154QWindowIcon", "Qt5QWindowIcon"),
        # The render child in MuMu is "nemuwin" — the actual Android
        # render surface inside the Qt shell.
        render_child_classes=("nemuwin",),

        console_exe_name="MuMuManager.exe",
        console_paths=(
            r"F:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
            r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
            r"D:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
            r"E:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
            r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
            r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
        ),
        env_console_path="TITAN_MUMU_MANAGER_PATH",

        adb_paths=(
            r"F:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
            r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
            r"D:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
            r"E:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
            r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
        ),
        default_adb_device="127.0.0.1:16384",

        # MuMu Player 12 has no right-side toolbar and a thin title bar.
        # The render child "nemuwin" gives us the pure Android surface
        # so chrome should be 0 when capturing the child directly.
        chrome_top=0,
        chrome_bottom=0,
        chrome_left=0,
        chrome_right=0,
    ),

    "ldplayer": EmulatorProfile(
        name="ldplayer",
        display_name="LDPlayer 9",
        title_pattern="LDPlayer",

        main_window_classes=("LDPlayerMainFrame", "LDPlayer"),
        render_child_classes=("subWin", "sub", "TheRender", "RenderWindow"),

        console_exe_name="ldconsole.exe",
        console_paths=(
            r"F:\LDPlayer\LDPlayer9\ldconsole.exe",
            r"C:\LDPlayer\LDPlayer9\ldconsole.exe",
            r"C:\Program Files\LDPlayer\LDPlayer9\ldconsole.exe",
        ),
        env_console_path="TITAN_LDCONSOLE_PATH",

        adb_paths=(
            r"F:\LDPlayer\LDPlayer9\adb.exe",
            r"C:\LDPlayer\LDPlayer9\adb.exe",
        ),
        default_adb_device="emulator-5554",

        chrome_top=35,
        chrome_bottom=0,
        chrome_left=0,
        chrome_right=38,
    ),
}

DEFAULT_PROFILE_NAME = "mumu"


# ═══════════════════════════════════════════════════════════════════════════
# Profile selection
# ═══════════════════════════════════════════════════════════════════════════

def get_profile(name: str | None = None) -> EmulatorProfile:
    """Return the active emulator profile.

    Resolution order:
      1. Explicit *name* argument
      2. ``TITAN_EMULATOR`` env var
      3. ``DEFAULT_PROFILE_NAME`` (``"mumu"``)
    """
    key = (
        name
        or os.getenv("TITAN_EMULATOR", "").strip()
        or DEFAULT_PROFILE_NAME
    ).lower().strip()

    # Accept backend aliases: "ldplayer" config value selects ldplayer profile
    if key not in PROFILES:
        # Check if it matches any profile's name or display name
        for pname, profile in PROFILES.items():
            if key in (pname, profile.display_name.lower()):
                return profile
        _log.warning(
            f"Unknown emulator profile {key!r} — falling back to {DEFAULT_PROFILE_NAME!r}"
        )
        key = DEFAULT_PROFILE_NAME

    return PROFILES[key]


# ═══════════════════════════════════════════════════════════════════════════
# Console / ADB executable discovery
# ═══════════════════════════════════════════════════════════════════════════

def find_console_exe(profile: EmulatorProfile | None = None) -> str | None:
    """Find the emulator's console/manager CLI executable."""
    if profile is None:
        profile = get_profile()

    # 1. Environment override
    if profile.env_console_path:
        custom = os.getenv(profile.env_console_path, "").strip()
        if custom and os.path.isfile(custom):
            return custom

    # 2. Search default paths
    for p in profile.console_paths:
        if os.path.isfile(p):
            return p

    return None


def find_adb_exe(profile: EmulatorProfile | None = None) -> str | None:
    """Find an ADB executable (profile-specific paths first)."""
    if profile is None:
        profile = get_profile()

    custom = os.getenv(profile.env_adb_path, "").strip()
    if custom and os.path.isfile(custom):
        return custom

    for p in profile.adb_paths:
        if os.path.isfile(p):
            return p

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Win32 render-window discovery (emulator-agnostic)
# ═══════════════════════════════════════════════════════════════════════════

_user32 = ctypes.windll.user32 if os.name == "nt" else None


def find_render_hwnd(profile: EmulatorProfile | None = None) -> int | None:
    """Find the emulator's render surface HWND.

    Discovery strategy (works for any emulator):
      1. Enumerate top-level windows matching the profile's
         ``main_window_classes`` **or** ``title_pattern``.
      2. For each match, enumerate child windows:
         a. If ``render_child_classes`` is defined, prefer children whose
            Win32 class matches (prioritised by order).
         b. Otherwise, pick the **largest visible child** by client area
            (reliable for MuMu/Qt-based emulators).
      3. If no child found, return the main window HWND directly.
    """
    if _user32 is None:
        return None

    if profile is None:
        profile = get_profile()

    main_classes_set = set(profile.main_window_classes)
    title_pat = profile.title_pattern.lower()
    render_classes = list(profile.render_child_classes)  # ordered by priority

    # Collect candidate top-level windows
    main_hwnds: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True

        # Match by class name
        cname = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cname, 256)
        if cname.value in main_classes_set:
            main_hwnds.append(hwnd)
            return True

        # Match by title substring
        title = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        if title_pat and title_pat in title.value.lower():
            main_hwnds.append(hwnd)

        return True

    _user32.EnumWindows(_enum_top, 0)

    if not main_hwnds:
        return None

    # For each main window, find the best render child
    for main_hwnd in main_hwnds:
        child = _find_best_child(main_hwnd, render_classes)
        if child is not None:
            _log.info(
                f"{profile.display_name} render HWND: {child:#x} "
                f"(parent={main_hwnd:#x})"
            )
            return child

    # Fallback: return the first main HWND itself
    _log.info(
        f"{profile.display_name} using main HWND: {main_hwnds[0]:#x} "
        f"(no suitable child found)"
    )
    return main_hwnds[0]


def find_main_hwnd(profile: EmulatorProfile | None = None) -> int | None:
    """Find the emulator's main (top-level) window HWND.

    Used by ``EmulatorWindow`` in ``vision_yolo.py`` to locate the
    emulator by title substring match.
    """
    if _user32 is None:
        return None

    if profile is None:
        profile = get_profile()

    title_pat = profile.title_pattern.lower()
    main_classes_set = set(profile.main_window_classes)

    result: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True

        cname = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cname, 256)
        if cname.value in main_classes_set:
            result.append(hwnd)
            return False  # stop

        title = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        if title_pat and title_pat in title.value.lower():
            result.append(hwnd)
            return False

        return True

    _user32.EnumWindows(_enum_top, 0)
    return result[0] if result else None


def _find_best_child(
    parent_hwnd: int,
    preferred_classes: list[str],
) -> int | None:
    """Find the best child HWND of *parent_hwnd*.

    If *preferred_classes* are given, children matching earlier entries
    are preferred.  Otherwise, the largest visible child is selected.
    """
    children: list[tuple[int, str, int]] = []  # (hwnd, class, area)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_child(child: int, _lp: int) -> bool:
        cn = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(child, cn, 256)
        rect = wintypes.RECT()
        _user32.GetClientRect(child, ctypes.byref(rect))
        area = rect.right * rect.bottom
        children.append((child, cn.value, area))
        return True

    _user32.EnumChildWindows(parent_hwnd, _enum_child, 0)

    if not children:
        return None

    # Strategy A: match by preferred class names (in priority order)
    if preferred_classes:
        class_priority = {cls: idx for idx, cls in enumerate(preferred_classes)}
        matched = [
            (class_priority[cn], hwnd)
            for hwnd, cn, _area in children
            if cn in class_priority
        ]
        if matched:
            matched.sort()
            return matched[0][1]

    # Strategy B: pick the largest child by client area (≥ 100 px²)
    large = [(area, hwnd) for hwnd, _cn, area in children if area >= 100]
    if large:
        large.sort(reverse=True)
        return large[0][1]

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Console-based tap (emulator-specific)
# ═══════════════════════════════════════════════════════════════════════════

def console_tap(
    console_exe: str,
    android_x: int,
    android_y: int,
    emu_index: int = 0,
    profile: EmulatorProfile | None = None,
) -> bool:
    """Inject a tap via the emulator's console CLI.

    - **LDPlayer**: ``ldconsole.exe action --index N --key call.input --value "X Y"``
    - **MuMu**: ``MuMuManager.exe api -v <index> input_event tap <x> <y>``
      (if the API is available; MuMu Player 12 supports this in recent builds)

    Returns ``True`` on success.
    """
    if profile is None:
        profile = get_profile()

    try:
        if profile.name == "ldplayer":
            result = subprocess.run(
                [
                    console_exe,
                    "action",
                    "--index", str(emu_index),
                    "--key", "call.input",
                    "--value", f"{android_x} {android_y}",
                ],
                timeout=5,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

        elif profile.name == "mumu":
            # MuMu Manager: use ADB bridge for input tap
            result = subprocess.run(
                [
                    console_exe,
                    "adb", "-v", str(emu_index),
                    "-c", f"shell input tap {android_x} {android_y}",
                ],
                timeout=5,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

    except Exception as exc:
        _log.warning(f"Console tap failed ({profile.display_name}): {exc}")

    return False


# ═══════════════════════════════════════════════════════════════════════════
# Resolution check (emulator-specific)
# ═══════════════════════════════════════════════════════════════════════════

def check_resolution(
    console_exe: str | None = None,
    profile: EmulatorProfile | None = None,
    expected_w: int = 720,
    expected_h: int = 1280,
    expected_dpi: int = 320,
) -> tuple[bool, str]:
    """Check emulator resolution via console CLI.

    Returns ``(ok, message)`` where *ok* is True if resolution matches
    or cannot be determined (fail-open).
    """
    if profile is None:
        profile = get_profile()

    if console_exe is None:
        console_exe = find_console_exe(profile)

    if console_exe is None or not os.path.isfile(console_exe):
        return True, f"{profile.display_name} console não encontrado — assumindo resolução correta"

    try:
        if profile.name == "ldplayer":
            return _check_resolution_ldplayer(
                console_exe, expected_w, expected_h, expected_dpi
            )
        elif profile.name == "mumu":
            return _check_resolution_mumu(
                console_exe, expected_w, expected_h, expected_dpi
            )
    except Exception as exc:
        return True, f"{profile.display_name} console falhou: {exc} — assumindo resolução correta"

    return True, "Resolução não verificada"


def _check_resolution_ldplayer(
    ldconsole: str,
    expected_w: int,
    expected_h: int,
    expected_dpi: int,
) -> tuple[bool, str]:
    """Check LDPlayer resolution via ``ldconsole list2``."""
    res = subprocess.run(
        [ldconsole, "list2"],
        capture_output=True, text=True, timeout=5,
    )
    output = res.stdout.strip()
    if not output:
        return True, "ldconsole list2 retornou vazio — assumindo resolução correta"

    first_line = output.splitlines()[0].strip()
    fields = first_line.split(",")
    if len(fields) < 10:
        return True, f"ldconsole list2 formato inesperado ({len(fields)} campos)"

    emu_name = fields[1]
    is_running = fields[4] == "1"
    width = int(fields[7])
    height = int(fields[8])
    dpi = int(fields[9])

    if not is_running:
        return True, f"LDPlayer '{emu_name}' não está rodando"

    ok = width == expected_w and height == expected_h
    dpi_ok = dpi == expected_dpi

    msg = f"LDPlayer '{emu_name}': {width}x{height} DPI {dpi}"
    if not ok:
        msg += f" (esperado {expected_w}x{expected_h})"
    if not dpi_ok:
        msg += f" (DPI esperado {expected_dpi})"

    return ok and dpi_ok, msg


def _check_resolution_mumu(
    mumu_manager: str,
    expected_w: int,
    expected_h: int,
    expected_dpi: int,
) -> tuple[bool, str]:
    """Check MuMu Player 12 resolution via ``MuMuManager.exe setting``."""
    try:
        res = subprocess.run(
            [
                mumu_manager, "setting", "-v", "0",
                "-k", "resolution_width",
                "-k", "resolution_height",
                "-k", "resolution_dpi",
            ],
            capture_output=True, text=True, timeout=5,
        )
        import json
        data = json.loads(res.stdout)
        width = int(float(data.get("resolution_width", "0")))
        height = int(float(data.get("resolution_height", "0")))
        dpi = int(float(data.get("resolution_dpi", "0")))

        ok = width == expected_w and height == expected_h and dpi == expected_dpi
        msg = f"MuMu Player 12: {width}x{height} DPI {dpi}"
        if not ok:
            msg += f" (esperado {expected_w}x{expected_h} DPI {expected_dpi})"
        return ok, msg

    except Exception as exc:
        return True, (
            f"MuMu Player 12: verificação via CLI falhou ({exc}) — "
            f"verifique em Configurações → Tela: "
            f"{expected_w}x{expected_h} DPI {expected_dpi}"
        )
