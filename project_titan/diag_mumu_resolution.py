#!/usr/bin/env python3
"""Diagnóstico de Resolução — MuMu Player 12 Sanity Check.

Valida as TRÊS camadas da arquitetura visual do Titan antes de iniciar
o Game Loop:

  1. **ADB (SO Android)**  — ``wm size`` + ``wm density`` via MuMuManager
  2. **Win32 (Host)**      — HWND nemuwin, Client Area, DPI awareness
  3. **VisionYolo (Cérebro)** — Canvas, offset, chrome, ROI shape

Se qualquer camada divergir do "Padrão Ouro" (720×1280 @ 320 DPI), o
script aborta com EXIT 1 e o bot NÃO recebe luz verde.

Uso::

    python diag_mumu_resolution.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import textwrap
import time

# ── ANSI colors ──────────────────────────────────────────────────────────
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

# Enable ANSI on Windows + set DPI awareness BEFORE any HWND operations
if os.name == "nt":
    k = ctypes.windll.kernel32
    k.SetConsoleMode(k.GetStdHandle(-11), 7)
    # Per-Monitor DPI awareness V2 — must be set before any Win32 calls
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ── Golden standard ──────────────────────────────────────────────────────
GOLD_WIDTH = 720
GOLD_HEIGHT = 1280
GOLD_DPI = 320

PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0


def _ok(msg: str) -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"  {_GREEN}✔ PASS{_RESET}  {msg}")


def _fail(msg: str) -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"  {_RED}✘ FAIL{_RESET}  {msg}")


def _warn(msg: str) -> None:
    global WARN_COUNT
    WARN_COUNT += 1
    print(f"  {_YELLOW}⚠ WARN{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}ℹ INFO{_RESET}  {msg}")


def _header(title: str) -> None:
    bar = "═" * 60
    print(f"\n{_CYAN}{_BOLD}{bar}")
    print(f"  {title}")
    print(f"{bar}{_RESET}")


# ═════════════════════════════════════════════════════════════════════════
# CAMADA 1: ADB — SO Android interno do MuMu
# ═════════════════════════════════════════════════════════════════════════
def check_adb_layer() -> bool:
    """Verifica resolução e DPI via ADB shell."""
    _header("CAMADA 1/3 — ADB (SO Android)")

    # Importar perfil para obter o adb.exe e device serial
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from utils.emulator_profiles import get_profile, find_adb_exe
        profile = get_profile()
        adb_exe = find_adb_exe(profile)
        device = profile.default_adb_device or "127.0.0.1:16384"
    except Exception as e:
        _fail(f"Não conseguiu importar emulator_profiles: {e}")
        return False

    if not adb_exe or not os.path.isfile(adb_exe):
        _fail(f"ADB não encontrado: {adb_exe}")
        return False

    _info(f"ADB exe:    {adb_exe}")
    _info(f"Device:     {device}")

    ok = True

    # ── wm size ──────────────────────────────────────────────────────
    try:
        r = subprocess.run(
            [adb_exe, "-s", device, "shell", "wm", "size"],
            capture_output=True, text=True, timeout=10,
        )
        raw = r.stdout.strip()
        _info(f"wm size → {raw!r}")

        # Pode ter "Physical size: 720x1280" e/ou "Override size: ..."
        lines = raw.splitlines()
        physical = override = None
        for line in lines:
            if "override" in line.lower():
                override = line.split(":")[-1].strip()
            elif "physical" in line.lower():
                physical = line.split(":")[-1].strip()

        effective = override or physical
        if effective:
            parts = effective.lower().split("x")
            w, h = int(parts[0]), int(parts[1])
            if w == GOLD_WIDTH and h == GOLD_HEIGHT:
                _ok(f"Resolução Android: {w}×{h} ✓")
            else:
                _fail(f"Resolução Android: {w}×{h}  (esperado {GOLD_WIDTH}×{GOLD_HEIGHT})")
                ok = False
            if override and physical and override != physical:
                _warn(f"Override ativo! Physical={physical}, Override={override}")
        else:
            _fail(f"Não conseguiu parsear wm size: {raw}")
            ok = False
    except Exception as e:
        _fail(f"Erro ao executar wm size: {e}")
        ok = False

    # ── wm density ───────────────────────────────────────────────────
    try:
        r = subprocess.run(
            [adb_exe, "-s", device, "shell", "wm", "density"],
            capture_output=True, text=True, timeout=10,
        )
        raw = r.stdout.strip()
        _info(f"wm density → {raw!r}")

        lines = raw.splitlines()
        physical_dpi = override_dpi = None
        for line in lines:
            if "override" in line.lower():
                override_dpi = int(line.split(":")[-1].strip())
            elif "physical" in line.lower():
                physical_dpi = int(line.split(":")[-1].strip())

        effective_dpi = override_dpi or physical_dpi
        if effective_dpi == GOLD_DPI:
            _ok(f"DPI Android: {effective_dpi} ✓")
        elif effective_dpi is not None:
            _fail(f"DPI Android: {effective_dpi}  (esperado {GOLD_DPI})")
            ok = False
        else:
            _fail(f"Não conseguiu parsear wm density: {raw}")
            ok = False
        if override_dpi and physical_dpi and override_dpi != physical_dpi:
            _warn(f"Override DPI ativo! Physical={physical_dpi}, Override={override_dpi}")
    except Exception as e:
        _fail(f"Erro ao executar wm density: {e}")
        ok = False

    # ── dumpsys display (confirmar orientação) ───────────────────────
    try:
        r = subprocess.run(
            [adb_exe, "-s", device, "shell",
             "dumpsys", "display", "|", "grep", "-i", "mBaseDisplayInfo"],
            capture_output=True, text=True, timeout=10,
        )
        raw = r.stdout.strip()
        if raw:
            _info(f"Display info: {raw[:200]}")
            if "rotation=0" in raw.lower() or "rotation 0" in raw.lower():
                _ok("Orientação: Portrait (rotation=0) ✓")
            else:
                _warn(f"Orientação pode não ser portrait: {raw[:120]}")
        else:
            _info("dumpsys display: sem BaseDisplayInfo (não-crítico)")
    except Exception:
        pass  # não-crítico

    return ok


# ═════════════════════════════════════════════════════════════════════════
# CAMADA 2: Win32 — Host Windows (nemuwin surface)
# ═════════════════════════════════════════════════════════════════════════
def check_win32_layer() -> bool:
    """Verifica HWND, Client Area e DPI awareness."""
    _header("CAMADA 2/3 — Win32 (Host Windows)")

    if os.name != "nt":
        _fail("Não é Windows — camada Win32 não aplicável")
        return False

    try:
        from utils.emulator_profiles import get_profile, find_render_hwnd
        profile = get_profile()
    except Exception as e:
        _fail(f"Não conseguiu importar emulator_profiles: {e}")
        return False

    ok = True

    # ── DPI awareness ────────────────────────────────────────────────
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        dpi_map = {0: "Unaware", 1: "System", 2: "Per-Monitor"}
        dpi_label = dpi_map.get(awareness.value, f"Unknown({awareness.value})")
        _info(f"DPI Awareness: {dpi_label}")
        if awareness.value == 2:
            _ok("DPI Awareness: Per-Monitor (ideal) ✓")
        elif awareness.value == 1:
            _ok("DPI Awareness: System-aware (aceitável)")
        else:
            _warn("DPI Awareness: Unaware — coordenadas podem ser escaladas pelo Windows!")
    except Exception:
        _info("Não conseguiu verificar DPI awareness")

    user32 = ctypes.windll.user32

    # ── Encontrar HWND do render surface ─────────────────────────────
    render_hwnd = find_render_hwnd(profile)
    if not render_hwnd:
        _fail("HWND do render surface (nemuwin) não encontrado! O MuMu está aberto?")
        return False

    _ok(f"Render HWND encontrado: {render_hwnd:#010x}")

    # Classe da janela
    cname = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(render_hwnd, cname, 256)
    _info(f"Window class: {cname.value!r}")

    if cname.value in profile.render_child_classes:
        _ok(f"Classe {cname.value!r} é o render child esperado ✓")
    else:
        _warn(f"Classe {cname.value!r} ≠ render_child_classes esperado {profile.render_child_classes}")

    # ── Client Area ──────────────────────────────────────────────────
    rect = wintypes.RECT()
    user32.GetClientRect(render_hwnd, ctypes.byref(rect))
    client_w = rect.right - rect.left
    client_h = rect.bottom - rect.top
    _info(f"Client Area: {client_w} × {client_h} px")

    # Verificar aspect ratio (720:1280 = 9:16 = 0.5625)
    if client_w > 0 and client_h > 0:
        ratio = client_w / client_h
        gold_ratio = GOLD_WIDTH / GOLD_HEIGHT  # 0.5625
        if abs(ratio - gold_ratio) < 0.01:
            _ok(f"Aspect ratio: {ratio:.4f} (≈ 9:16 = {gold_ratio}) ✓")
        else:
            _fail(f"Aspect ratio: {ratio:.4f}  (esperado ≈ {gold_ratio} = 9:16)")
            ok = False

        # Confirmar que é 720×1280 exacto ou múltiplo inteiro
        scale_w = client_w / GOLD_WIDTH
        scale_h = client_h / GOLD_HEIGHT
        if client_w == GOLD_WIDTH and client_h == GOLD_HEIGHT:
            _ok(f"Client Area = Padrão Ouro exacto: {GOLD_WIDTH}×{GOLD_HEIGHT} ✓")
        elif abs(scale_w - scale_h) < 0.001:
            _ok(f"Client Area escalada uniformemente: {scale_w:.3f}x ({client_w}×{client_h})")
        else:
            _warn(
                f"Client Area {client_w}×{client_h} não é {GOLD_WIDTH}×{GOLD_HEIGHT} exacto. "
                f"Scale W={scale_w:.3f}, H={scale_h:.3f}"
            )
    else:
        _fail(f"Client Area inválida: {client_w}×{client_h}")
        ok = False

    # ── Window rect (com borda) ──────────────────────────────────────
    wrect = wintypes.RECT()
    user32.GetWindowRect(render_hwnd, ctypes.byref(wrect))
    _info(
        f"Window Rect:  left={wrect.left}, top={wrect.top}, "
        f"right={wrect.right}, bottom={wrect.bottom}  "
        f"({wrect.right - wrect.left}×{wrect.bottom - wrect.top})"
    )

    # ── Parent HWND (main window) ────────────────────────────────────
    parent = user32.GetParent(render_hwnd)
    if parent:
        ptitle = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(parent, ptitle, 512)
        pcname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(parent, pcname, 256)
        _info(f"Parent HWND: {parent:#010x}  class={pcname.value!r}  title={ptitle.value!r}")

    return ok


# ═════════════════════════════════════════════════════════════════════════
# CAMADA 3: VisionYolo — Cérebro (Canvas, offset, ROI)
# ═════════════════════════════════════════════════════════════════════════
def check_vision_layer() -> bool:
    """Instancia VisionYolo e valida canvas/offset."""
    _header("CAMADA 3/3 — VisionYolo (Cérebro)")

    try:
        from agent.vision_yolo import VisionYolo
    except ImportError as e:
        _fail(f"Não conseguiu importar VisionYolo: {e}")
        return False

    ok = True

    # Instanciar sem modelo (apenas para teste de inicialização)
    v = VisionYolo(model_path="")
    found = v.find_window()

    if not found:
        _fail("VisionYolo.find_window() falhou — janela do emulador não encontrada")
        return False

    _ok("VisionYolo.find_window() → janela detectada ✓")

    emu = v.emulator
    _info(f"HWND:       {emu._hwnd:#010x}")
    _info(f"Canvas:     {emu._canvas_w} × {emu._canvas_h}")
    _info(f"Offset:     ({emu._offset_x}, {emu._offset_y})")
    _info(f"Win size:   {emu._win_width} × {emu._win_height}")
    _info(f"Chrome:     top={emu._chrome_top}, bottom={emu._chrome_bottom}, "
          f"left={emu._chrome_left}, right={emu._chrome_right}")

    # O canvas deve ter aspect ratio 9:16
    if emu._canvas_w > 0 and emu._canvas_h > 0:
        ratio = emu._canvas_w / emu._canvas_h
        gold_ratio = GOLD_WIDTH / GOLD_HEIGHT
        if abs(ratio - gold_ratio) < 0.02:
            _ok(f"Canvas aspect ratio: {ratio:.4f} (≈ 9:16) ✓")
        else:
            _fail(f"Canvas aspect ratio: {ratio:.4f}  (esperado ≈ {gold_ratio})")
            ok = False
    else:
        _fail(f"Canvas inválido: {emu._canvas_w}×{emu._canvas_h}")
        ok = False

    # Chrome deve ser 0 no MuMu (captura direto da nemuwin)
    from utils.emulator_profiles import get_profile
    profile = get_profile()
    if profile.name == "mumu":
        total_chrome = emu._chrome_top + emu._chrome_bottom + emu._chrome_left + emu._chrome_right
        if total_chrome == 0:
            _ok("Chrome total = 0 (MuMu nemuwin direto) ✓")
        else:
            _warn(f"Chrome total = {total_chrome}  (esperado 0 para MuMu nemuwin)")

    # ── Teste de captura rápida ──────────────────────────────────────
    _info("Testando captura de frame via mss...")
    try:
        frame = v.capture_frame()
        if frame is not None:
            det = frame
            # frame is a DetectionFrame; get the raw image if available
            img = getattr(det, "frame", None)
            if img is not None:
                h, w = img.shape[:2]
                _ok(f"Captura OK: {w}×{h} (shape={img.shape})")
                if abs(w / h - GOLD_WIDTH / GOLD_HEIGHT) < 0.02:
                    _ok(f"Frame aspect ratio: {w/h:.4f} (≈ 9:16) ✓")
                else:
                    _warn(f"Frame aspect ratio: {w/h:.4f} ≠ 9:16")
            else:
                _ok("Captura retornou DetectionFrame (sem atributo .frame exposto)")
        else:
            _warn("capture_frame() retornou None (pode ser normal sem modelo YOLO)")
    except Exception as e:
        _warn(f"Captura falhou: {e}  (pode ser normal sem modelo YOLO carregado)")

    return ok


# ═════════════════════════════════════════════════════════════════════════
# RESUMO FINAL
# ═════════════════════════════════════════════════════════════════════════
def main() -> int:
    start = time.perf_counter()

    print(f"\n{_BOLD}{'=' * 60}")
    print(f"  DIAGNÓSTICO DE RESOLUÇÃO — MuMu Player 12")
    print(f"  Padrão Ouro: {GOLD_WIDTH}×{GOLD_HEIGHT} @ {GOLD_DPI} DPI")
    print(f"{'=' * 60}{_RESET}")

    adb_ok = check_adb_layer()
    win32_ok = check_win32_layer()
    vision_ok = check_vision_layer()

    elapsed = time.perf_counter() - start

    _header("RESULTADO FINAL")

    camadas = [
        ("ADB (SO Android)", adb_ok),
        ("Win32 (Host)",     win32_ok),
        ("VisionYolo",       vision_ok),
    ]
    for name, status in camadas:
        icon = f"{_GREEN}✔ OK{_RESET}" if status else f"{_RED}✘ FAIL{_RESET}"
        print(f"  {icon}   {name}")

    all_ok = adb_ok and win32_ok and vision_ok

    print()
    print(f"  {_BOLD}Passes: {PASS_COUNT}  |  Fails: {FAIL_COUNT}  |  Warns: {WARN_COUNT}{_RESET}")
    print(f"  {_DIM}Tempo: {elapsed:.2f}s{_RESET}")
    print()

    if all_ok and FAIL_COUNT == 0:
        print(f"  {_GREEN}{_BOLD}🟢 LUZ VERDE — Bot autorizado a iniciar.{_RESET}")
        print(f"  {_DIM}   Todas as 3 camadas coincidem com o Padrão Ouro.{_RESET}\n")
        return 0
    else:
        print(f"  {_RED}{_BOLD}🔴 LUZ VERMELHA — Bot NÃO autorizado.{_RESET}")
        print(f"  {_DIM}   Corrija os itens FAIL acima antes de iniciar o Game Loop.{_RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
