"""
Project Titan — GhostMouse Calibration Tool

Ferramenta interativa para capturar coordenadas reais dos botoes do emulador
e salvar como perfil de calibracao reutilizavel.

Modos:
  interactive  — Captura coordenadas via click (requer PyAutoGUI)
  manual       — Entrada manual de coordenadas via argumento
  show         — Exibe perfil salvo
  validate     — Valida perfil existente
  env          — Gera variaveis de ambiente para o perfil

Uso:
    python training/calibrate_ghost.py interactive --profile emulator_default
    python training/calibrate_ghost.py manual --fold 600,700 --call 800,700 --raise-small 1000,700 --raise-big 1000,700 --profile emu1
    python training/calibrate_ghost.py show --profile emulator_default
    python training/calibrate_ghost.py validate --profile emulator_default
    python training/calibrate_ghost.py env --profile emulator_default

Os perfis sao salvos em reports/calibration_profiles/ como JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PROFILES_DIR = PROJECT_ROOT / "reports" / "calibration_profiles"
BUTTON_NAMES = ["fold", "call", "raise_small", "raise_big"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GhostMouse Calibration Tool")
    sub = parser.add_subparsers(dest="mode", required=True)

    # interactive
    p_int = sub.add_parser("interactive", help="Capture coords via mouse click")
    p_int.add_argument("--profile", type=str, default="default", help="Profile name")
    p_int.add_argument("--profiles-dir", type=str, default=None, dest="profiles_dir")
    p_int.add_argument("--timeout", type=int, default=30, help="Seconds to wait per button")

    # manual
    p_man = sub.add_parser("manual", help="Set coords manually")
    p_man.add_argument("--profile", type=str, default="default", help="Profile name")
    p_man.add_argument("--profiles-dir", type=str, default=None, dest="profiles_dir")
    p_man.add_argument("--fold", type=str, required=True, help="Fold button x,y")
    p_man.add_argument("--call", type=str, required=True, help="Call button x,y")
    p_man.add_argument("--raise-small", type=str, required=True, dest="raise_small", help="Raise small x,y")
    p_man.add_argument("--raise-big", type=str, required=True, dest="raise_big", help="Raise big x,y")

    # show
    p_show = sub.add_parser("show", help="Show calibration profile")
    p_show.add_argument("--profile", type=str, default="default", help="Profile name")
    p_show.add_argument("--profiles-dir", type=str, default=None, dest="profiles_dir")
    p_show.add_argument("--json", action="store_true")

    # validate
    p_val = sub.add_parser("validate", help="Validate calibration profile")
    p_val.add_argument("--profile", type=str, default="default", help="Profile name")
    p_val.add_argument("--profiles-dir", type=str, default=None, dest="profiles_dir")

    # env
    p_env = sub.add_parser("env", help="Generate env vars for profile")
    p_env.add_argument("--profile", type=str, default="default", help="Profile name")
    p_env.add_argument("--profiles-dir", type=str, default=None, dest="profiles_dir")
    p_env.add_argument("--powershell", action="store_true", help="PowerShell syntax")

    return parser.parse_args()


def _profiles_dir(args: argparse.Namespace) -> Path:
    if hasattr(args, "profiles_dir") and args.profiles_dir:
        p = Path(args.profiles_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p
    return DEFAULT_PROFILES_DIR


def _profile_path(args: argparse.Namespace) -> Path:
    return _profiles_dir(args) / f"calibration_{args.profile}.json"


def _parse_xy(value: str) -> tuple[int, int]:
    """Parse 'x,y' string into (x, y) tuple."""
    parts = value.strip().split(",")
    if len(parts) != 2:
        raise ValueError(f"Formato invalido (esperado x,y): {value}")
    return int(parts[0].strip()), int(parts[1].strip())


def _save_profile(path: Path, profile: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"[CALIBRATE] Perfil salvo: {path}")


def _load_profile(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Perfil nao encontrado: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_profile(profile: dict) -> list[str]:
    """Validate profile structure, return list of issues."""
    issues: list[str] = []

    if "buttons" not in profile:
        issues.append("Campo 'buttons' ausente")
        return issues

    buttons = profile["buttons"]
    for name in BUTTON_NAMES:
        if name not in buttons:
            issues.append(f"Botao '{name}' ausente")
            continue

        btn = buttons[name]
        if "x" not in btn or "y" not in btn:
            issues.append(f"Botao '{name}' sem coordenadas x/y")
            continue

        x, y = btn["x"], btn["y"]
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            issues.append(f"Botao '{name}' coordenadas invalidas: x={x}, y={y}")
        elif x < 0 or y < 0:
            issues.append(f"Botao '{name}' coordenadas negativas: x={x}, y={y}")
        elif x > 5000 or y > 5000:
            issues.append(f"Botao '{name}' coordenadas suspeitamente grandes: x={x}, y={y}")

    return issues


def _make_profile(buttons: dict[str, tuple[int, int]], source: str) -> dict:
    """Create a profile dict."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "buttons": {
            name: {"x": xy[0], "y": xy[1]}
            for name, xy in buttons.items()
        },
    }


def cmd_interactive(args: argparse.Namespace) -> None:
    """Interactive calibration via mouse position capture."""
    try:
        import pyautogui
    except ImportError:
        print("[CALIBRATE] ERRO: PyAutoGUI nao instalado. Use modo 'manual'.")
        sys.exit(1)

    print("[CALIBRATE] === Calibracao Interativa ===")
    print("[CALIBRATE] Para cada botao, posicione o cursor sobre ele")
    print("[CALIBRATE] e pressione ENTER. Timeout por botao:", args.timeout, "s")
    print()

    buttons: dict[str, tuple[int, int]] = {}
    for name in BUTTON_NAMES:
        label = name.replace("_", " ").upper()
        input(f"  Posicione o cursor sobre [{label}] e pressione ENTER...")

        pos = pyautogui.position()
        x, y = int(pos.x), int(pos.y)
        buttons[name] = (x, y)
        print(f"  -> {name} = ({x}, {y})")

    profile = _make_profile(buttons, "interactive")
    path = _profile_path(args)
    _save_profile(path, profile)

    issues = _validate_profile(profile)
    if issues:
        print(f"[CALIBRATE] AVISO: {len(issues)} problemas detectados:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("[CALIBRATE] Perfil validado com sucesso.")


def cmd_manual(args: argparse.Namespace) -> None:
    """Manual calibration from CLI args."""
    buttons: dict[str, tuple[int, int]] = {
        "fold": _parse_xy(args.fold),
        "call": _parse_xy(args.call),
        "raise_small": _parse_xy(args.raise_small),
        "raise_big": _parse_xy(args.raise_big),
    }

    profile = _make_profile(buttons, "manual")
    path = _profile_path(args)
    _save_profile(path, profile)

    issues = _validate_profile(profile)
    if issues:
        print(f"[CALIBRATE] AVISO: {len(issues)} problemas detectados:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("[CALIBRATE] Perfil validado com sucesso.")


def cmd_show(args: argparse.Namespace) -> None:
    """Show saved calibration profile."""
    path = _profile_path(args)
    profile = _load_profile(path)

    if hasattr(args, "json") and args.json:
        print(json.dumps(profile, indent=2))
    else:
        print(f"[CALIBRATE] Perfil: {args.profile}")
        print(f"  Gerado em: {profile.get('generated_at', 'N/A')}")
        print(f"  Fonte:     {profile.get('source', 'N/A')}")
        print(f"  Botoes:")
        for name, coords in profile.get("buttons", {}).items():
            print(f"    {name:15s}  ({coords['x']}, {coords['y']})")


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate saved calibration profile."""
    path = _profile_path(args)
    profile = _load_profile(path)
    issues = _validate_profile(profile)

    if issues:
        print(f"[CALIBRATE] {len(issues)} problemas encontrados:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print(f"[CALIBRATE] Perfil '{args.profile}' validado com sucesso.")


def cmd_env(args: argparse.Namespace) -> None:
    """Generate env vars for a calibration profile."""
    path = _profile_path(args)
    profile = _load_profile(path)

    env_map = {
        "fold": "TITAN_BTN_FOLD",
        "call": "TITAN_BTN_CALL",
        "raise_small": "TITAN_BTN_RAISE_SMALL",
        "raise_big": "TITAN_BTN_RAISE_BIG",
    }

    ps = hasattr(args, "powershell") and args.powershell
    buttons = profile.get("buttons", {})

    for name, env_var in env_map.items():
        coords = buttons.get(name, {})
        x, y = coords.get("x", 0), coords.get("y", 0)
        if ps:
            print(f'$env:{env_var}="{x},{y}"')
        else:
            print(f'export {env_var}="{x},{y}"')

    if ps:
        print('$env:TITAN_GHOST_MOUSE="1"')
    else:
        print('export TITAN_GHOST_MOUSE="1"')


def main() -> None:
    args = _parse_args()

    dispatch = {
        "interactive": cmd_interactive,
        "manual": cmd_manual,
        "show": cmd_show,
        "validate": cmd_validate,
        "env": cmd_env,
    }

    handler = dispatch.get(args.mode)
    if handler is None:
        print(f"[CALIBRATE] Modo desconhecido: {args.mode}")
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
