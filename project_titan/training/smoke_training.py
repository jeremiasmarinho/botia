"""
Project Titan — Training Pipeline Smoke Test

Valida a infraestrutura de treinamento YOLO sem precisar de dataset real:
  1. Valida data.yaml (parse, classes, paths)
  2. Valida prepare_dataset.py (dry-run imports, class mapping)
  3. Valida train_yolo.py --dry-run
  4. Valida evaluate_yolo.py --dry-run
  5. Valida coerência de classes entre data.yaml e prepare_dataset.py

Uso:
    python training/smoke_training.py
    python training/smoke_training.py --json
    python training/smoke_training.py --save-report reports/smoke_training.json
"""

from __future__ import annotations

import argparse
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = PROJECT_ROOT / "training"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test for training pipeline")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--save-report", type=str, default=None, dest="save_report")
    return parser.parse_args()


def _find_python() -> str:
    """Find Python executable."""
    venv = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    venv_posix = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_posix.exists():
        return str(venv_posix)
    return sys.executable


def _check(name: str, func) -> dict:
    """Run a check and return result dict."""
    try:
        func()
        return {"name": name, "status": "pass", "error": None}
    except Exception as e:
        return {"name": name, "status": "fail", "error": str(e)}


def _validate_data_yaml() -> None:
    """Validate data.yaml structure."""
    try:
        import yaml
    except ImportError:
        # ultralytics installs PyYAML
        raise RuntimeError("PyYAML não instalado (pip install pyyaml)")

    data_file = TRAINING_DIR / "data.yaml"
    if not data_file.exists():
        raise FileNotFoundError(f"data.yaml não encontrado: {data_file}")

    with open(data_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    required_keys = ["path", "train", "val", "nc", "names"]
    for key in required_keys:
        if key not in data:
            raise KeyError(f"Campo obrigatório ausente em data.yaml: {key}")

    nc = data["nc"]
    names = data["names"]
    if not isinstance(names, dict):
        raise TypeError(f"'names' deve ser dict, encontrado {type(names).__name__}")

    if len(names) != nc:
        raise ValueError(f"nc={nc} mas names tem {len(names)} entradas")

    # Validate all 52 cards + 4 actions + 2 regions = 58
    if nc != 58:
        raise ValueError(f"Esperado nc=58, encontrado nc={nc}")

    # Verify card names
    ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    suits = ["c", "d", "h", "s"]
    expected_cards = [f"{r}{s}" for r in ranks for s in suits]
    actual_names = [names[i] for i in range(52)]
    if actual_names != expected_cards:
        raise ValueError("Nomes das 52 cartas não coincidem com o esperado")

    # Verify action + region names
    expected_extra = [
        "fold", "check", "raise", "raise_2x",
        "raise_2_5x", "raise_pot", "raise_confirm",
        "allin", "pot", "stack",
    ]
    actual_extra = [names[i] for i in range(52, 62)]
    if actual_extra != expected_extra:
        raise ValueError(f"Classes extras não coincidem: {actual_extra} != {expected_extra}")


def _validate_prepare_dataset_classes() -> None:
    """Validate class mapping coherence with data.yaml."""
    sys.path.insert(0, str(TRAINING_DIR))
    try:
        from prepare_dataset import ALL_CLASS_NAMES, CLASS_NAME_TO_ID
    finally:
        sys.path.pop(0)

    if len(ALL_CLASS_NAMES) != 58:
        raise ValueError(f"ALL_CLASS_NAMES tem {len(ALL_CLASS_NAMES)} classes, esperado 58")

    if len(CLASS_NAME_TO_ID) != 58:
        raise ValueError(f"CLASS_NAME_TO_ID tem {len(CLASS_NAME_TO_ID)} entradas, esperado 58")

    # Cross-check with data.yaml
    try:
        import yaml
        data_file = TRAINING_DIR / "data.yaml"
        with open(data_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for idx, name in data["names"].items():
            if ALL_CLASS_NAMES[int(idx)] != name:
                raise ValueError(f"Classe {idx}: data.yaml='{name}' != prepare_dataset='{ALL_CLASS_NAMES[int(idx)]}'")
    except ImportError:
        pass  # PyYAML not available, skip cross-check


def _validate_train_dryrun() -> None:
    """Validate train_yolo.py --dry-run."""
    python = _find_python()
    train_script = TRAINING_DIR / "train_yolo.py"
    if not train_script.exists():
        raise FileNotFoundError(f"train_yolo.py não encontrado: {train_script}")

    result = subprocess.run(
        [python, str(train_script), "--dry-run", "--data", str(TRAINING_DIR / "data.yaml")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        raise RuntimeError(f"train_yolo.py --dry-run falhou: {result.stderr}")

    # Verify JSON output
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            if not payload.get("dry_run"):
                raise ValueError("Payload dry_run esperado")
            return

    raise ValueError("Nenhum JSON output encontrado em train_yolo.py --dry-run")


def _validate_evaluate_dryrun() -> None:
    """Validate evaluate_yolo.py --dry-run."""
    python = _find_python()
    eval_script = TRAINING_DIR / "evaluate_yolo.py"
    if not eval_script.exists():
        raise FileNotFoundError(f"evaluate_yolo.py não encontrado: {eval_script}")

    result = subprocess.run(
        [python, str(eval_script), "--dry-run", "--model", "dummy.pt", "--data", str(TRAINING_DIR / "data.yaml"), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:
        raise RuntimeError(f"evaluate_yolo.py --dry-run falhou: {result.stderr}")


def _validate_scripts_exist() -> None:
    """Verify all training scripts exist."""
    required = ["data.yaml", "train_yolo.py", "prepare_dataset.py", "evaluate_yolo.py"]
    missing = [f for f in required if not (TRAINING_DIR / f).exists()]
    if missing:
        raise FileNotFoundError(f"Arquivos faltando: {missing}")


def main() -> None:
    args = _parse_args()

    checks = [
        _check("scripts_exist", _validate_scripts_exist),
        _check("data_yaml", _validate_data_yaml),
        _check("prepare_dataset_classes", _validate_prepare_dataset_classes),
        _check("train_dryrun", _validate_train_dryrun),
        _check("evaluate_dryrun", _validate_evaluate_dryrun),
    ]

    passed = all(c["status"] == "pass" for c in checks)
    overall = "pass" if passed else "fail"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "checks": checks,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            icon = "OK" if c["status"] == "pass" else "FAIL"
            msg = f"  [{icon}] {c['name']}"
            if c["error"]:
                msg += f" — {c['error']}"
            print(msg)
        print(f"\n[SMOKE-TRAINING] overall_status={overall}")

    if args.save_report:
        rp = Path(args.save_report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[SMOKE-TRAINING] report salvo: {rp}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
