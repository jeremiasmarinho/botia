"""debug_adb_monitor.py — Monitor all subprocess calls for ADB leaks.

Patches subprocess.run / subprocess.Popen to log any call that invokes
adb.exe.  Run this BEFORE importing any Titan module to catch every ADB
call during the entire lifecycle.

Usage:
    python debug_adb_monitor.py
"""

from __future__ import annotations

import subprocess
import os
import sys
import traceback
import time

# Patch subprocess to intercept ADB calls
_orig_run = subprocess.run
_orig_popen = subprocess.Popen.__init__
_adb_calls: list[dict] = []


def _patched_run(*args, **kwargs):
    cmd = args[0] if args else kwargs.get("args", [])
    cmd_str = str(cmd).lower()
    if "adb" in cmd_str and "ldconsole" not in cmd_str:
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "cmd": cmd,
            "stack": traceback.format_stack(),
        }
        _adb_calls.append(entry)
        print(f"\n{'='*60}")
        print(f"⚠️  ADB CALL DETECTED at {entry['time']}!")
        print(f"    Command: {cmd}")
        print(f"    Stack trace:")
        for line in traceback.format_stack()[-5:-1]:
            print(f"      {line.strip()}")
        print(f"{'='*60}\n")
    return _orig_run(*args, **kwargs)


def _patched_popen(self, *args, **kwargs):
    cmd = args[0] if args else kwargs.get("args", [])
    cmd_str = str(cmd).lower()
    if "adb" in cmd_str and "ldconsole" not in cmd_str:
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "cmd": cmd,
            "stack": traceback.format_stack(),
        }
        _adb_calls.append(entry)
        print(f"\n{'='*60}")
        print(f"⚠️  ADB POPEN DETECTED at {entry['time']}!")
        print(f"    Command: {cmd}")
        print(f"    Stack trace:")
        for line in traceback.format_stack()[-5:-1]:
            print(f"      {line.strip()}")
        print(f"{'='*60}\n")
    return _orig_popen(self, *args, **kwargs)


# Apply patches
subprocess.run = _patched_run
subprocess.Popen.__init__ = _patched_popen


def main():
    print("🔍 ADB Monitor ativo — interceptando todas as chamadas subprocess")
    print("   Importando módulos do Titan...")

    # Simulate what run_titan.py does
    from run_titan import check_emulator_resolution, check_emulator, check_dependencies

    print("\n--- Testando check_emulator_resolution() ---")
    check_emulator_resolution()

    print("\n--- Testando check_emulator() ---")
    check_emulator("LDPlayer")

    print("\n--- Importando PokerAgent ---")
    from agent.poker_agent import PokerAgent

    print("\n--- Criando GhostMouse ---")
    os.environ["TITAN_INPUT_BACKEND"] = "ldplayer"
    os.environ["TITAN_GHOST_MOUSE"] = "1"
    from agent.ghost_mouse import GhostMouse
    gm = GhostMouse()

    print("\n--- Criando VisionTool ---")
    from tools.vision_tool import VisionTool
    vt = VisionTool()

    print("\n--- Testando VisionTool._capture_frame() ---")
    frame = vt._capture_frame()
    print(f"    Frame shape: {frame.shape if frame is not None else 'None'}")

    # Summary
    print(f"\n{'='*60}")
    if _adb_calls:
        print(f"❌ {len(_adb_calls)} chamada(s) ADB detectada(s)!")
        for i, call in enumerate(_adb_calls, 1):
            print(f"   {i}. [{call['time']}] {call['cmd']}")
    else:
        print("✅ ZERO chamadas ADB — rede do emulador está segura!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
