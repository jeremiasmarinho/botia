"""
Project Titan — E2E Smoke Test

Valida o pipeline end-to-end completo em modo simulado:
  1. Valida imports do E2E runner
  2. Dry-run do E2E runner
  3. Executa 3 ciclos sim com cenario cycle
  4. Valida overlay (imports + generate_simulated_bboxes)
  5. Valida report gerado

Uso:
    python tools/smoke_e2e.py
    python tools/smoke_e2e.py --json
    python tools/smoke_e2e.py --save-report reports/smoke_e2e_latest.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="E2E smoke test")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save-report", type=str, default=None, dest="save_report")
    return parser.parse_args()


def _find_python() -> str:
    venv = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.exists():
        return str(venv)
    venv_posix = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_posix.exists():
        return str(venv_posix)
    return sys.executable


def _check(name: str, func) -> dict:
    try:
        ok, detail = func()
        status = "pass" if ok else "fail"
        return {"name": name, "status": status, "detail": detail}
    except Exception as exc:
        return {"name": name, "status": "fail", "detail": str(exc)}


def check_imports() -> tuple[bool, str]:
    """Validate that all E2E module imports work."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from tools.visual_overlay import (
            BBox, OverlayConfig, classify_label_category,
            draw_detections, draw_hud, generate_simulated_bboxes,
        )
        from tools.e2e_runner import E2ECycleResult, E2EReport
        return True, "All E2E imports OK"
    except ImportError as e:
        return False, f"Import error: {e}"


def check_dryrun() -> tuple[bool, str]:
    """Run E2E runner in dry-run mode."""
    python = _find_python()
    script = TOOLS_DIR / "e2e_runner.py"
    if not script.exists():
        return False, f"Script nao encontrado: {script}"

    result = subprocess.run(
        [python, str(script), "--mode", "sim", "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return False, f"exit={result.returncode} stderr={result.stderr[:200]}"
    return True, "dry-run OK"


def check_sim_cycles() -> tuple[bool, str]:
    """Execute 3 sim cycles and validate output."""
    python = _find_python()
    script = TOOLS_DIR / "e2e_runner.py"

    report_path = PROJECT_ROOT / "reports" / "_smoke_e2e_temp.json"

    result = subprocess.run(
        [python, str(script),
         "--mode", "sim", "--cycles", "3", "--scenario", "cycle",
         "--tick-seconds", "0.05",
         "--save-report", str(report_path)],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode != 0:
        return False, f"exit={result.returncode} stderr={result.stderr[:300]}"

    if not report_path.exists():
        return False, "Report nao foi gerado"

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except Exception as e:
        return False, f"Report invalido: {e}"
    finally:
        report_path.unlink(missing_ok=True)

    status = report.get("overall_status", "?")
    completed = report.get("completed_cycles", 0)
    if status != "pass":
        return False, f"overall_status={status}"
    if completed < 3:
        return False, f"completed_cycles={completed} (esperado 3)"

    return True, f"3 cycles pass, avg_latency={report.get('avg_latency_ms', '?')}ms"


def check_overlay() -> tuple[bool, str]:
    """Validate overlay bbox generation (no OpenCV required)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.visual_overlay import (
        BBox, OverlayConfig, classify_label_category,
        generate_simulated_bboxes,
    )

    # Test category classification
    tests = [
        ("hero_Ah", "hero"),
        ("board_Kd", "board"),
        ("fold", "button"),
        ("pot_120", "pot"),
        ("dead_7c", "dead"),
        ("opponent_v1", "opponent"),
    ]
    for label, expected in tests:
        got = classify_label_category(label)
        if got != expected:
            return False, f"classify({label})={got}, expected={expected}"

    # Test simulated bbox generation with a mock snapshot
    class MockSnapshot:
        hero_cards = ["Ah", "Kd", "Qs", "Jc"]
        board_cards = ["7d", "8h", "9s"]
        pot = 120
        stack = 500
        action_points = {}

    bboxes = generate_simulated_bboxes(MockSnapshot())
    if len(bboxes) < 7:
        return False, f"Expected >= 7 bboxes, got {len(bboxes)}"

    hero_bboxes = [b for b in bboxes if b.category == "hero"]
    board_bboxes = [b for b in bboxes if b.category == "board"]
    if len(hero_bboxes) != 4:
        return False, f"Expected 4 hero bboxes, got {len(hero_bboxes)}"
    if len(board_bboxes) != 3:
        return False, f"Expected 3 board bboxes, got {len(board_bboxes)}"

    return True, f"{len(bboxes)} bboxes generated, categories OK"


def check_report_schema() -> tuple[bool, str]:
    """Validate E2E report schema."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.e2e_runner import E2EReport

    required_fields = [
        "mode", "total_cycles", "completed_cycles", "pass_count",
        "fail_count", "overall_status", "avg_latency_ms",
        "action_distribution", "cycles", "generated_at", "duration_seconds",
    ]
    report_fields = [f.name for f in E2EReport.__dataclass_fields__.values()]
    missing = [f for f in required_fields if f not in report_fields]
    if missing:
        return False, f"Missing fields: {missing}"

    return True, f"Schema OK ({len(required_fields)} fields validated)"


def main() -> None:
    args = _parse_args()

    checks = [
        _check("e2e_imports", check_imports),
        _check("e2e_dryrun", check_dryrun),
        _check("overlay_logic", check_overlay),
        _check("report_schema", check_report_schema),
        _check("sim_cycles", check_sim_cycles),
    ]

    overall = "pass" if all(c["status"] == "pass" for c in checks) else "fail"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "checks": {c["name"]: {"status": c["status"], "detail": c["detail"]} for c in checks},
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for c in checks:
            mark = "OK" if c["status"] == "pass" else "FAIL"
            detail = f" -- {c['detail']}" if c["status"] == "fail" else ""
            print(f"  [{mark}] {c['name']}{detail}")
        print(f"\n[SMOKE-E2E] overall_status={overall}")

    if args.save_report:
        os.makedirs(os.path.dirname(args.save_report) or ".", exist_ok=True)
        with open(args.save_report, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"[SMOKE-E2E] report salvo: {args.save_report}")

    sys.exit(0 if overall == "pass" else 1)


if __name__ == "__main__":
    main()
