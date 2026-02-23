#!/usr/bin/env python3
"""Diagnóstico urgente: extrai os limites ABS_X / ABS_Y do digitizer do LDPlayer.

Executa `adb shell getevent -p` e parseia os eixos de todos os dispositivos
de input, focando nos ABS_MT_POSITION_X/Y do touchscreen virtual.

Resultado esperado: os valores max reais do digitizer (frequentemente 0-32767
ou 0-720/0-1280 dependendo do driver VirtIO do emulador).

Uso:
    python scripts/diag_getevent.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ADB_EXE = os.getenv("TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe")
ADB_DEVICE = os.getenv("TITAN_ADB_DEVICE", "emulator-5554")


def run_getevent_p() -> str:
    """Executa `adb shell getevent -p` e retorna stdout."""
    result = subprocess.run(
        [ADB_EXE, "-s", ADB_DEVICE, "shell", "getevent", "-p"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout


def run_getevent_il(device: str, seconds: int = 5) -> str:
    """Executa `adb shell getevent -il <device>` por N segundos (para ver eventos live)."""
    try:
        result = subprocess.run(
            [ADB_EXE, "-s", ADB_DEVICE, "shell",
             "timeout", str(seconds), "getevent", "-il", device],
            capture_output=True,
            text=True,
            timeout=seconds + 5,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return "(timeout)"


def parse_devices(raw: str) -> list[dict]:
    """Parseia a saída do getevent -p em uma lista de dispositivos."""
    devices = []
    current: dict | None = None

    for line in raw.splitlines():
        # Novo dispositivo
        m = re.match(r'^add device \d+:\s*(.+)', line)
        if m:
            if current:
                devices.append(current)
            current = {
                "path": m.group(1).strip(),
                "name": "",
                "abs_axes": {},
                "raw_lines": [],
            }
            continue

        if current is None:
            continue

        current["raw_lines"].append(line)

        # Nome
        nm = re.match(r'\s*name:\s*"(.+)"', line)
        if nm:
            current["name"] = nm.group(1)

        # ABS axis: ex "    ABS_MT_POSITION_X : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0"
        # ou formato hex "    0035  : value 0, min 0, max 32767, ..."
        abs_m = re.match(
            r'\s+(ABS_\w+|[0-9a-fA-F]{4})\s*:\s*value\s+(\d+),\s*min\s+(\d+),\s*max\s+(\d+)',
            line,
        )
        if abs_m:
            axis_name = abs_m.group(1)
            # Converter hex para nomes conhecidos
            hex_to_name = {
                "0035": "ABS_MT_POSITION_X",
                "0036": "ABS_MT_POSITION_Y",
                "0030": "ABS_MT_TOUCH_MAJOR",
                "0031": "ABS_MT_TOUCH_MINOR",
                "003a": "ABS_MT_PRESSURE",
                "0039": "ABS_MT_TRACKING_ID",
                "002f": "ABS_MT_SLOT",
                "0000": "ABS_X",
                "0001": "ABS_Y",
            }
            if axis_name in hex_to_name:
                axis_name = hex_to_name[axis_name]

            current["abs_axes"][axis_name] = {
                "value": int(abs_m.group(2)),
                "min": int(abs_m.group(3)),
                "max": int(abs_m.group(4)),
            }

    if current:
        devices.append(current)

    return devices


def main():
    print("=" * 70)
    print("  DIAGNÓSTICO DO DIGITIZER — Project Titan")
    print("=" * 70)
    print(f"ADB:    {ADB_EXE}")
    print(f"Device: {ADB_DEVICE}")
    print()

    # 1) getevent -p
    print("[1] Executando: adb shell getevent -p ...")
    raw = run_getevent_p()
    if not raw.strip():
        print("ERRO: getevent -p retornou vazio. Verifique a conexão ADB.")
        sys.exit(1)

    devices = parse_devices(raw)
    print(f"    Encontrados {len(devices)} dispositivos de input.\n")

    # 2) Listar todos os dispositivos e seus eixos ABS
    touch_devices = []
    for dev in devices:
        has_touch_axes = any(
            "MT_POSITION" in k or k in ("ABS_X", "ABS_Y")
            for k in dev["abs_axes"]
        )
        if has_touch_axes:
            touch_devices.append(dev)

        prefix = ">>> " if has_touch_axes else "    "
        print(f"{prefix}{dev['path']}  —  \"{dev['name']}\"")
        if dev["abs_axes"]:
            for axis, info in sorted(dev["abs_axes"].items()):
                marker = " <<<" if "MT_POSITION" in axis else ""
                print(f"        {axis}: min={info['min']}  max={info['max']}  current={info['value']}{marker}")
        print()

    # 3) Resumo do digitizer
    print("=" * 70)
    print("  RESUMO DO DIGITIZER")
    print("=" * 70)
    if not touch_devices:
        print("NENHUM dispositivo touch encontrado!")
        print("Raw output:")
        print(raw)
        sys.exit(1)

    for dev in touch_devices:
        print(f"\nDispositivo: {dev['path']}  ({dev['name']})")
        x_axis = dev["abs_axes"].get("ABS_MT_POSITION_X", dev["abs_axes"].get("ABS_X"))
        y_axis = dev["abs_axes"].get("ABS_MT_POSITION_Y", dev["abs_axes"].get("ABS_Y"))

        if x_axis:
            print(f"  ABS_X range: [{x_axis['min']}, {x_axis['max']}]")
        if y_axis:
            print(f"  ABS_Y range: [{y_axis['min']}, {y_axis['max']}]")

        if x_axis and y_axis:
            x_max = x_axis["max"]
            y_max = y_axis["max"]
            print()
            print(f"  === FORMULA DE INTERPOLACAO ===")
            print(f"  Para converter display (720x1280) -> digitizer:")
            print(f"    touch_x = int(display_x / 720 * {x_max})")
            print(f"    touch_y = int(display_y / 1280 * {y_max})")
            print()
            print(f"  Exemplo: display (361, 1220) → touch ({int(361/720*x_max)}, {int(1220/1280*y_max)})")
            print(f"  Exemplo: display (126, 1220) → touch ({int(126/720*x_max)}, {int(1220/1280*y_max)})")
            print(f"  Exemplo: display (596, 1220) → touch ({int(596/720*x_max)}, {int(1220/1280*y_max)})")

    # 4) Verificar wm size
    print()
    print("=" * 70)
    print("  VERIFICAÇÃO wm size")
    print("=" * 70)
    wm_result = subprocess.run(
        [ADB_EXE, "-s", ADB_DEVICE, "shell", "wm", "size"],
        capture_output=True, text=True, timeout=5,
    )
    print(f"  {wm_result.stdout.strip()}")
    if "Override" in wm_result.stdout:
        print("  ⚠️  OVERRIDE ATIVO! Isso pode quebrar input. Remova com: adb shell wm size reset")

    # 5) LDConsole test
    print()
    print("=" * 70)
    print("  TESTE LDCONSOLE")
    print("=" * 70)
    ldconsole = r"F:\LDPlayer\LDPlayer9\ldconsole.exe"
    if os.path.exists(ldconsole):
        print(f"  ldconsole.exe encontrado: {ldconsole}")
        # Tentar listar
        ld_result = subprocess.run(
            [ldconsole, "list2"], capture_output=True, text=True, timeout=5,
        )
        print(f"  Emuladores: {ld_result.stdout.strip()}")
    else:
        print(f"  ldconsole.exe NÃO encontrado em: {ldconsole}")

    # 6) Salvar raw output
    out_path = os.path.join(os.path.dirname(__file__), "..", "reports", "getevent_diag.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(raw)
    print(f"\n  Raw getevent -p salvo em: {out_path}")

    print("\n✅ Diagnóstico concluído.")


if __name__ == "__main__":
    main()
