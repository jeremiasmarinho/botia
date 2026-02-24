"""Discover emulator window structure (MuMu Player 12 / LDPlayer).

Run with the emulator open to identify:
  - Main window HWND, class name, title
  - Child window hierarchy (class names, sizes, positions)
  - Render surface candidate (largest child)

Usage::

    python debug_discover_emulator.py
    python debug_discover_emulator.py --title "MuMu"
    python debug_discover_emulator.py --title "LDPlayer"
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

user32 = ctypes.windll.user32


def _get_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _get_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _get_client_rect(hwnd: int) -> tuple[int, int]:
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def discover_emulator(title_pattern: str = "MuMu") -> None:
    """Enumerate all matching windows and their children."""
    title_pat = title_pattern.lower()
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        cls_name = _get_class(hwnd)
        win_title = _get_title(hwnd)
        if title_pat in win_title.lower() or title_pat in cls_name.lower():
            matches.append(hwnd)
        return True

    user32.EnumWindows(_enum_top, 0)

    if not matches:
        print(f"\n❌ Nenhuma janela encontrada com título contendo '{title_pattern}'")
        print("   Verifique se o emulador está aberto e visível.")
        return

    print(f"\n{'═' * 70}")
    print(f"  Emulator Window Discovery — padrão: '{title_pattern}'")
    print(f"  Encontradas: {len(matches)} janela(s)")
    print(f"{'═' * 70}")

    for idx, hwnd in enumerate(matches):
        cls_name = _get_class(hwnd)
        win_title = _get_title(hwnd)
        x, y, w, h = _get_rect(hwnd)
        cw, ch = _get_client_rect(hwnd)

        print(f"\n┌─ Janela {idx + 1}/{len(matches)} ─────────────────────────────")
        print(f"│  HWND:       {hwnd:#010x}  ({hwnd})")
        print(f"│  Classe:     {cls_name!r}")
        print(f"│  Título:     {win_title!r}")
        print(f"│  Posição:    ({x}, {y})")
        print(f"│  Tamanho:    {w} x {h}  (window rect)")
        print(f"│  Client:     {cw} x {ch}  (client area)")
        print(f"│  Chrome:")
        print(f"│    top:      {h - ch - 0}px (≈ title bar)")
        print(f"│    right:    {w - cw}px (≈ sidebar)")
        print(f"│")

        # Enumerate children
        children: list[tuple[int, str, str, int, int]] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def _enum_child(child: int, _lp: int) -> bool:
            c_cls = _get_class(child)
            c_title = _get_title(child)
            c_w, c_h = _get_client_rect(child)
            children.append((child, c_cls, c_title, c_w, c_h))
            return True

        user32.EnumChildWindows(hwnd, _enum_child, 0)

        if children:
            print(f"│  Children ({len(children)}):")
            print(f"│  {'HWND':<14} {'Classe':<30} {'Título':<20} {'Client W×H'}")
            print(f"│  {'─' * 14} {'─' * 30} {'─' * 20} {'─' * 15}")

            best_child = None
            best_area = 0
            for c_hwnd, c_cls, c_title, c_w, c_h in children:
                area = c_w * c_h
                marker = ""
                if area > best_area and area >= 100:
                    best_area = area
                    best_child = c_hwnd
                    marker = " ◄── largest"
                print(
                    f"│  {c_hwnd:#014x} "
                    f"{c_cls!r:<30} "
                    f"{c_title!r:<20} "
                    f"{c_w:>5} × {c_h:<5}"
                    f"{marker}"
                )

            if best_child is not None:
                bc_cls = _get_class(best_child)
                bc_w, bc_h = _get_client_rect(best_child)
                pt = wt.POINT(0, 0)
                user32.ClientToScreen(best_child, ctypes.byref(pt))
                print(f"│")
                print(f"│  ★ Render surface candidate:")
                print(f"│    HWND:   {best_child:#010x}")
                print(f"│    Classe: {bc_cls!r}")
                print(f"│    Client: {bc_w} × {bc_h}")
                print(f"│    Screen: ({pt.x}, {pt.y})")
        else:
            print(f"│  Children: (nenhum)")
            print(f"│  → Main window será usada como render surface")

        print(f"└{'─' * 59}")

    # Profile recommendation
    print(f"\n{'═' * 70}")
    print(f"  Recomendações para config YAML:")
    print(f"{'═' * 70}")

    if matches:
        hwnd = matches[0]
        cls_name = _get_class(hwnd)
        x, y, w, h = _get_rect(hwnd)
        cw, ch = _get_client_rect(hwnd)
        chrome_top = h - ch  # approximate
        chrome_right = w - cw

        # Re-enumerate to find best child
        children2: list[tuple[int, str, int, int]] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def _enum2(child: int, _lp: int) -> bool:
            c_cls = _get_class(child)
            c_w, c_h = _get_client_rect(child)
            children2.append((child, c_cls, c_w, c_h))
            return True

        user32.EnumChildWindows(hwnd, _enum2, 0)

        render_cls = cls_name
        if children2:
            best = max(children2, key=lambda x: x[2] * x[3])
            render_cls = best[1]
            # Recalculate chrome from render child position
            pt = wt.POINT(0, 0)
            user32.ClientToScreen(best[0], ctypes.byref(pt))
            chrome_top = pt.y - y
            chrome_right = (x + w) - (pt.x + best[2])
            chrome_left = pt.x - x
            chrome_bottom = (y + h) - (pt.y + best[3])
            print(f"  chrome_top:    {chrome_top}")
            print(f"  chrome_bottom: {chrome_bottom}")
            print(f"  chrome_left:   {chrome_left}")
            print(f"  chrome_right:  {chrome_right}")
        else:
            print(f"  chrome_top:    {chrome_top} (estimated)")
            print(f"  chrome_right:  {chrome_right} (estimated)")

        print(f"  main_class:    {cls_name!r}")
        print(f"  render_class:  {render_cls!r}")
        print(f"  title_pattern: {title_pattern!r}")

    # Also test the profile discovery
    print(f"\n{'═' * 70}")
    print(f"  Teste do EmulatorProfile:")
    print(f"{'═' * 70}")
    try:
        from utils.emulator_profiles import get_profile, find_render_hwnd, find_console_exe

        for profile_name in ("mumu", "ldplayer"):
            profile = get_profile(profile_name)
            hwnd = find_render_hwnd(profile)
            console = find_console_exe(profile)
            status = "✅ HWND encontrado" if hwnd else "❌ não encontrado"
            console_status = f"✅ {console}" if console else "❌ não encontrado"
            print(f"  {profile.display_name}:")
            print(f"    Render HWND: {status}" + (f" ({hwnd:#x})" if hwnd else ""))
            print(f"    Console:     {console_status}")
    except Exception as exc:
        print(f"  Erro ao testar profiles: {exc}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover emulator window structure")
    parser.add_argument(
        "--title", type=str, default="MuMu",
        help="Title substring to search for (default: MuMu)",
    )
    args = parser.parse_args()
    discover_emulator(args.title)
