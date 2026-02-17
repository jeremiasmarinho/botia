"""titan_control.py — Central de Comando do Project Titan (Cockpit).

Menu interativo no terminal que abstrai os comandos complexos de
PowerShell/Python, permitindo iniciar modos, rodar diagnósticos e
ajustar configurações sem memorizar argumentos.

Uso::

    python titan_control.py

    # ou, do diretório raiz:
    python project_titan/titan_control.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from utils.titan_config import cfg

# Resolve raiz do projeto (onde config.yaml vive)
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR if (_SCRIPT_DIR / "config.yaml").exists() else _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_CLI_IMAGE_PATH: str = ""
_CLI_CONFIG_PATH: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# ANSI Colors
# ═══════════════════════════════════════════════════════════════════════════

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"
_BLUE = "\033[94m"
_WHITE = "\033[97m"


def _enable_ansi() -> None:
    """Habilita ANSI no Windows 10+."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def _c(color: str, text: str) -> str:
    """Colore texto com ANSI."""
    return f"{color}{text}{_RESET}"


# ═══════════════════════════════════════════════════════════════════════════
# Localizar Python da venv
# ═══════════════════════════════════════════════════════════════════════════

def _find_python() -> str:
    """Localiza o Python da venv do projeto."""
    candidates = [
        _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        _PROJECT_ROOT.parent / ".venv" / "Scripts" / "python.exe",
        _PROJECT_ROOT / ".venv" / "bin" / "python",
        _PROJECT_ROOT.parent / ".venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable


def _find_powershell_script(name: str) -> str:
    """Localiza um script PowerShell no diretório scripts/."""
    candidate = _PROJECT_ROOT / "scripts" / name
    if candidate.exists():
        return str(candidate)
    return name


# ═══════════════════════════════════════════════════════════════════════════
# Execução de comandos
# ═══════════════════════════════════════════════════════════════════════════

def _run_python_module(module: str, env_overrides: dict[str, str] | None = None) -> None:
    """Executa um módulo Python no contexto do projeto."""
    python = _find_python()
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    cmd = [python, "-m", module]
    print(f"\n{_DIM}> {' '.join(cmd)}{_RESET}\n")
    try:
        subprocess.run(cmd, cwd=str(_PROJECT_ROOT), env=env)
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Interrompido pelo usuario.{_RESET}")


def _run_powershell(script_name: str, args: list[str] | None = None) -> None:
    """Executa um script PowerShell."""
    script = _find_powershell_script(script_name)
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script]
    if args:
        cmd.extend(args)

    print(f"\n{_DIM}> {' '.join(cmd)}{_RESET}\n")
    try:
        subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Interrompido pelo usuario.{_RESET}")
    except FileNotFoundError:
        print(f"{_RED}PowerShell nao encontrado. Instale ou use os comandos Python diretamente.{_RESET}")


def _run_command(cmd: list[str], env_overrides: dict[str, str] | None = None) -> int:
    """Executa um comando genérico."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    print(f"\n{_DIM}> {' '.join(cmd)}{_RESET}\n")
    try:
        result = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), env=env)
        return result.returncode
    except KeyboardInterrupt:
        print(f"\n{_YELLOW}Interrompido pelo usuario.{_RESET}")
        return 1
    except FileNotFoundError:
        print(f"{_RED}Comando nao encontrado: {cmd[0]}{_RESET}")
        return 1


def _parse_cli_args() -> argparse.Namespace:
    """Parseia argumentos CLI opcionais do Cockpit."""
    parser = argparse.ArgumentParser(
        description="Project Titan Cockpit",
        add_help=True,
    )
    parser.add_argument(
        "--image_path",
        default="",
        help="Caminho de imagem estática para calibração no menu 8",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Arquivo de configuração YAML (ex: config_club.yaml)",
    )
    return parser.parse_args()


def _apply_runtime_config(config_path: str) -> None:
    """Define ``TITAN_CONFIG_FILE`` e recarrega o loader central."""
    normalized = str(config_path or "").strip()
    if not normalized:
        return

    cfg_candidate = Path(normalized)
    if not cfg_candidate.is_absolute():
        cfg_candidate = (_PROJECT_ROOT / cfg_candidate).resolve()

    if not cfg_candidate.exists():
        print(_c(_RED, f"  Config nao encontrado: {cfg_candidate}"))
        return

    os.environ["TITAN_CONFIG_FILE"] = str(cfg_candidate)
    cfg.reload()
    print(_c(_MAGENTA, f"  Config ativo: {cfg_candidate.name}"))


# ═══════════════════════════════════════════════════════════════════════════
# Diagnósticos
# ═══════════════════════════════════════════════════════════════════════════

def _check_python() -> bool:
    """Verifica se o Python da venv está acessível."""
    python = _find_python()
    try:
        result = subprocess.run(
            [python, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        version = result.stdout.strip()
        print(f"  {_GREEN}[OK]{_RESET} Python: {version} ({python})")
        return True
    except Exception as e:
        print(f"  {_RED}[FALHA]{_RESET} Python: {e}")
        return False


def _check_redis() -> bool:
    """Verifica conectividade Redis."""
    try:
        import redis as _redis
        client = _redis.Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
        client.ping()
        print(f"  {_GREEN}[OK]{_RESET} Redis: conectado")
        return True
    except Exception:
        print(f"  {_YELLOW}[WARN]{_RESET} Redis: nao disponivel (fallback in-memory OK)")
        return False


def _check_emulator() -> bool:
    """Verifica se o emulador LDPlayer está aberto."""
    try:
        from agent.vision_yolo import VisionYolo
        v = VisionYolo(model_path="")
        found = v.find_window()
        if found:
            emu = v.emulator
            print(
                f"  {_GREEN}[OK]{_RESET} Emulador: encontrado "
                f"(window={emu.width}x{emu.height}, "
                f"canvas={emu.canvas_width}x{emu.canvas_height})"
            )
        else:
            print(f"  {_RED}[FALHA]{_RESET} Emulador: janela nao encontrada")
        return found
    except Exception as e:
        print(f"  {_RED}[FALHA]{_RESET} Emulador: {e}")
        return False


def _check_yolo_model() -> bool:
    """Verifica se o modelo YOLO está acessível."""
    model_path = os.getenv("TITAN_YOLO_MODEL", "").strip()
    if not model_path:
        try:
            from utils.titan_config import cfg
            model_path = cfg.get_str("vision.model_path", "")
        except Exception:
            pass

    if not model_path:
        print(f"  {_YELLOW}[WARN]{_RESET} Modelo YOLO: nao configurado (TITAN_YOLO_MODEL)")
        return False

    if Path(model_path).exists():
        size_mb = Path(model_path).stat().st_size / (1024 * 1024)
        print(f"  {_GREEN}[OK]{_RESET} Modelo YOLO: {model_path} ({size_mb:.1f} MB)")
        return True
    else:
        print(f"  {_RED}[FALHA]{_RESET} Modelo YOLO: arquivo nao encontrado ({model_path})")
        return False


def _check_config() -> bool:
    """Verifica se config.yaml existe e é válido."""
    config_path = _PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print(f"  {_RED}[FALHA]{_RESET} config.yaml: nao encontrado")
        return False
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        sections = list(data.keys()) if isinstance(data, dict) else []
        print(f"  {_GREEN}[OK]{_RESET} config.yaml: {len(sections)} secoes ({', '.join(sections)})")
        return True
    except Exception as e:
        print(f"  {_RED}[FALHA]{_RESET} config.yaml: {e}")
        return False


def _check_dependencies() -> bool:
    """Verifica pacotes críticos."""
    packages = {
        "numpy": "numpy",
        "cv2": "opencv-python",
        "zmq": "pyzmq",
        "yaml": "pyyaml",
        "mss": "mss",
        "ultralytics": "ultralytics",
    }
    all_ok = True
    for import_name, pip_name in packages.items():
        try:
            __import__(import_name)
            print(f"  {_GREEN}[OK]{_RESET} {pip_name}")
        except ImportError:
            print(f"  {_RED}[FALHA]{_RESET} {pip_name} (pip install {pip_name})")
            all_ok = False
    return all_ok


# ═══════════════════════════════════════════════════════════════════════════
# Ações do Menu
# ═══════════════════════════════════════════════════════════════════════════

def _action_mock_training() -> None:
    """Modo de treino com visão simulada."""
    print(_c(_YELLOW, "\n=== MODO DE TREINO (Mock Vision) ===\n"))

    cycles = input(f"  Quantos ciclos? (default: 10): ").strip() or "10"
    scenario = input(f"  Cenario (A/B/ALT)? (default: ALT): ").strip().upper() or "ALT"
    overlay = input(f"  Ativar Overlay? (s/N): ").strip().lower()

    env = {
        "TITAN_USE_MOCK_VISION": "1",
        "TITAN_MOCK_SCENARIO": scenario,
        "TITAN_AGENT_MAX_CYCLES": cycles,
        "TITAN_GHOST_MOUSE": "0",
    }
    if overlay in ("s", "sim", "y", "yes"):
        env["TITAN_OVERLAY_ENABLED"] = "1"

    print(_c(_CYAN, f"\nIniciando treino: {cycles} ciclos, cenario {scenario}...\n"))
    _run_powershell("start_squad.ps1", [
        "-UseMockVision",
        f"-MaxCycles", cycles,
    ])


def _action_collect_data() -> None:
    """Coleta de dados observando mesa real."""
    print(_c(_CYAN, "\n=== COLETA DE DADOS (Observador) ===\n"))
    print("  O bot vai observar a mesa e salvar frames unicos em data/raw/")
    print("  Nenhuma acao sera executada no jogo.\n")

    cycles = input(f"  Quantos ciclos? (default: 100): ").strip() or "100"
    overlay = input(f"  Ativar Overlay? (s/N): ").strip().lower()

    args = ["-CollectData", "-MaxCycles", cycles]
    if overlay in ("s", "sim", "y", "yes"):
        args.append("-Overlay")

    _run_powershell("start_squad.ps1", args)


def _action_real_play() -> None:
    """Modo de jogo real (com cautela!)."""
    print(_c(_RED, "\n=== MODO DE JOGO REAL ===\n"))
    print(_c(_RED, "  ATENCAO: O bot vai controlar o mouse e tomar decisoes reais!"))
    print(_c(_RED, "  Certifique-se que esta numa mesa de valor baixo.\n"))

    confirm = input(f"  Digitar 'JOGAR' para confirmar: ").strip()
    if confirm != "JOGAR":
        print(_c(_YELLOW, "  Cancelado.\n"))
        return

    overlay = input(f"  Ativar Overlay? (s/N): ").strip().lower()

    env_overrides: dict[str, str] = {
        "TITAN_GHOST_MOUSE": "1",
    }
    if overlay in ("s", "sim", "y", "yes"):
        env_overrides["TITAN_OVERLAY_ENABLED"] = "1"

    args = ["-Agents", "1"]
    if overlay in ("s", "sim", "y", "yes"):
        args.append("-Overlay")

    print(_c(_GREEN, "\n  Iniciando jogo real...\n"))
    _run_powershell("start_squad.ps1", args)


def _action_diagnostics() -> None:
    """Diagnóstico completo do sistema."""
    print(_c(_CYAN, "\n=== DIAGNOSTICO DO SISTEMA ===\n"))

    checks = [
        ("Python", _check_python),
        ("config.yaml", _check_config),
        ("Dependencias", _check_dependencies),
        ("Redis", _check_redis),
        ("Emulador", _check_emulator),
        ("Modelo YOLO", _check_yolo_model),
    ]

    results: dict[str, bool] = {}
    for name, check_fn in checks:
        print(f"\n  {_BOLD}Verificando {name}...{_RESET}")
        results[name] = check_fn()

    # Resumo
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n  {_BOLD}Resultado: {passed}/{total} verificacoes OK{_RESET}")

    if passed == total:
        print(_c(_GREEN, "  Sistema pronto para operar!\n"))
    elif passed >= total - 2:
        print(_c(_YELLOW, "  Sistema parcialmente pronto (alguns avisos).\n"))
    else:
        print(_c(_RED, "  Sistema com problemas. Corrija os itens marcados.\n"))


def _action_endurance_test() -> None:
    """Teste de endurance com 100 ciclos."""
    print(_c(_MAGENTA, "\n=== TESTE DE ENDURANCE ===\n"))
    print("  Roda 100 ciclos alternando cenarios A/B (mock)")
    print("  Verifica: estabilidade, timing e correspondencia motor.\n")

    overlay = input(f"  Ativar Overlay? (s/N): ").strip().lower()
    args = ["-EnduranceMode", "-UseMockVision"]
    if overlay in ("s", "sim", "y", "yes"):
        args.append("-Overlay")

    _run_powershell("start_squad.ps1", args)


def _action_edit_config() -> None:
    """Abre config.yaml para edição."""
    config_path = _PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print(_c(_RED, f"  config.yaml nao encontrado em {config_path}\n"))
        return

    print(_c(_CYAN, f"\n  Abrindo: {config_path}\n"))

    editors = ["code", "notepad++", "nano", "notepad"]
    for editor in editors:
        try:
            subprocess.Popen([editor, str(config_path)])
            print(f"  Aberto com: {editor}")
            return
        except FileNotFoundError:
            continue

    print(_c(_YELLOW, "  Nenhum editor encontrado. Abra manualmente:"))
    print(f"  {config_path}\n")


def _action_overlay_standalone() -> None:
    """Abre overlay standalone — ao vivo ou com imagem estática."""
    print(_c(_MAGENTA, "\n=== OVERLAY STANDALONE ===\n"))

    club_config = _PROJECT_ROOT / "config_club.yaml"

    # Detecta config club e ativa se aceito
    previous_config = ""
    if club_config.exists():
        use_club = input(
            "Detectei config_club.yaml. Usar configuração de Clube? [S/N] "
        ).strip().lower()
        if use_club in ("s", "sim", "y", "yes"):
            previous_config = os.getenv("TITAN_CONFIG_FILE", "")
            os.environ["TITAN_CONFIG_FILE"] = "config_club.yaml"
            cfg.reload()
            print(_c(_MAGENTA, "  Modo Clube: usando config_club.yaml"))

    try:
        print()
        print(_c(_CYAN, "  [1] AO VIVO — captura do emulador em tempo real"))
        print(_c(_CYAN, "  [2] ESTÁTICO — usar imagem de referência"))
        choice = input("\n  Escolha (1/2): ").strip()

        if choice == "2":
            club_image = _PROJECT_ROOT / "club_table_reference.png"
            prompt_default = _CLI_IMAGE_PATH or ""
            if club_image.exists():
                print(f"  Dica: imagem de clube detectada = {club_image.name}")
                prompt_default = prompt_default or str(club_image)
            image = input(f"  Caminho da imagem [{prompt_default or 'nenhum'}]: ").strip() or prompt_default
            if image:
                _preview_static_overlay(image)
            else:
                print(_c(_YELLOW, "  Nenhuma imagem fornecida.\n"))
        else:
            _preview_live_overlay()
    finally:
        if previous_config:
            os.environ["TITAN_CONFIG_FILE"] = previous_config
        elif "TITAN_CONFIG_FILE" in os.environ and club_config.exists():
            os.environ.pop("TITAN_CONFIG_FILE", None)
        cfg.reload()


def _preview_live_overlay() -> None:
    """Overlay AO VIVO — captura contínua do emulador com YOLO + OCR.

    Loop principal:
      1. Captura frame do emulador via mss (ROI sem chrome)
      2. Roda inferência YOLO no frame
      3. Roda OCR nas regiões calibradas (pot, stack, call)
      4. Atualiza TerminatorVision em tempo real
      5. Repete até o usuário pressionar Q
    """
    try:
        import cv2  # type: ignore[import-untyped]
    except ImportError:
        print(_c(_RED, "  OpenCV nao encontrado. Instale: pip install opencv-python\n"))
        return

    from tools.terminator_vision import TerminatorVision
    from utils.config import OCRRuntimeConfig
    from agent.vision_yolo import VisionYolo

    # --- Carregar modelo YOLO ---
    model_path = cfg.get_str("vision.model_path", "")
    model_file_str = ""
    if model_path:
        model_file = Path(model_path)
        if not model_file.is_absolute():
            model_file = (_PROJECT_ROOT / model_file).resolve()
        model_file_str = str(model_file)
        if not model_file.exists():
            print(_c(_YELLOW, f"  Modelo não encontrado: {model_file}"))
            model_file_str = ""

    yolo_model = None
    if model_file_str:
        try:
            from ultralytics import YOLO  # type: ignore[import-untyped]
            yolo_model = YOLO(model_file_str)
            print(_c(_GREEN, f"  Modelo YOLO carregado: {model_path}"))
        except Exception as err:
            print(_c(_YELLOW, f"  Aviso YOLO: {err}"))
    else:
        print(_c(_YELLOW, "  Sem modelo YOLO — overlay mostrará apenas o frame."))

    confidence = cfg.get_float("vision.confidence_threshold", 0.35)

    # --- Localizar emulador ---
    emulator_title = cfg.get_str("vision.emulator_title", "LDPlayer")
    vision = VisionYolo(model_path=model_file_str)
    if not vision.find_window():
        print(_c(_RED, f"  Emulador '{emulator_title}' não encontrado. Está aberto?\n"))
        return
    print(_c(_GREEN, f"  Emulador encontrado: {emulator_title}"))

    # --- OCR setup ---
    ocr_pot_str = cfg.get_str("ocr.pot_region", "")
    ocr_stack_str = cfg.get_str("ocr.stack_region", "")
    ocr_call_str = cfg.get_str("ocr.call_region", "")
    if ocr_pot_str:
        os.environ["TITAN_OCR_POT_REGION"] = ocr_pot_str
    if ocr_stack_str:
        os.environ["TITAN_OCR_STACK_REGION"] = ocr_stack_str
    if ocr_call_str:
        os.environ["TITAN_OCR_CALL_REGION"] = ocr_call_str

    ocr_regions = OCRRuntimeConfig().regions()
    ocr_engine = None
    try:
        from agent.vision_ocr import TitanOCR
        ocr_cfg_use_easy = cfg.get_bool("ocr.use_easyocr", False)
        ocr_cfg_tess_cmd = cfg.get_str("ocr.tesseract_cmd", "")
        ocr_engine = TitanOCR(use_easyocr=ocr_cfg_use_easy, tesseract_cmd=ocr_cfg_tess_cmd or None)
    except Exception as err:
        print(_c(_YELLOW, f"  OCR indisponível: {err}"))

    # --- Action points ---
    action_buttons_cfg = cfg.get_dict("action_buttons")
    action_points: dict[str, tuple[int, int]] = {}
    for action_name in ("fold", "call", "raise_small", "raise_big"):
        raw = action_buttons_cfg.get(action_name)
        if isinstance(raw, list) and len(raw) == 2:
            try:
                action_points[action_name] = (int(raw[0]), int(raw[1]))
            except (TypeError, ValueError):
                continue

    # --- Overlay ---
    overlay = TerminatorVision(
        max_fps=cfg.get_int("overlay.max_fps", 10),
        hud_width=cfg.get_int("overlay.hud_width", 320),
        show_grid=cfg.get_bool("overlay.show_grid", False),
        grid_size=cfg.get_int("overlay.grid_size", 50),
        window_name="TITAN: Visao AO VIVO",
    )
    overlay.update_ocr_regions(ocr_regions)
    overlay.start()

    print(_c(_CYAN, "\n  Overlay AO VIVO ativo. Pressione Q na janela para fechar."))
    print(_c(_CYAN, f"  Modelo: {model_path or '(nenhum)'}  |  Botões: {len(action_points)}\n"))

    cycle = 0
    try:
        while overlay.is_running:
            cycle += 1
            t0 = time.perf_counter()

            # 1) Capturar frame do emulador
            frame = vision.capture_frame()
            if frame is None:
                # Tenta re-localizar a janela
                vision.find_window()
                time.sleep(0.3)
                continue

            # 2) YOLO inference
            yolo_detections: list[dict[str, object]] = []
            if yolo_model is not None:
                try:
                    results = yolo_model.predict(source=frame, conf=confidence, verbose=False)
                    if results and len(results) > 0:
                        result = results[0]
                        names: dict[int, str] = getattr(result, "names", {})
                        boxes = getattr(result, "boxes", None)
                        if boxes is not None:
                            cls_list = boxes.cls.tolist() if boxes.cls is not None else []
                            xyxy_list = boxes.xyxy.tolist() if boxes.xyxy is not None else []
                            conf_list = boxes.conf.tolist() if boxes.conf is not None else []
                            for idx, (cls_idx, xyxy) in enumerate(zip(cls_list, xyxy_list)):
                                label = names.get(int(cls_idx), "")
                                conf_val = float(conf_list[idx]) if idx < len(conf_list) else 0.0
                                x1, y1, x2, y2 = (float(v) for v in xyxy)
                                cx = int((x1 + x2) / 2.0)
                                cy = int((y1 + y2) / 2.0)
                                w_det = int(x2 - x1)
                                h_det = int(y2 - y1)
                                yolo_detections.append({
                                    "label": label, "confidence": conf_val,
                                    "cx": cx, "cy": cy, "w": w_det, "h": h_det,
                                })
                except Exception:
                    pass

            # 3) OCR
            pot_val = 0.0
            stack_val = 0.0
            call_val = 0.0
            if ocr_engine is not None:
                fh, fw = frame.shape[:2]
                for region_name, (rx, ry, rw, rh) in ocr_regions.items():
                    if rw <= 0 or rh <= 0:
                        continue
                    if ry + rh > fh or rx + rw > fw:
                        continue
                    crop = frame[ry : ry + rh, rx : rx + rw]
                    if crop is not None and crop.size > 0:
                        value = ocr_engine.read_numeric_region(crop, key=region_name)
                        if "pot" in region_name:
                            pot_val = value
                        elif "stack" in region_name or "hero" in region_name:
                            stack_val = value
                        elif "call" in region_name:
                            call_val = value

            # 4) Atualizar overlay
            cycle_ms = (time.perf_counter() - t0) * 1000.0
            snapshot = SimpleNamespace(
                hero_cards=[],
                board_cards=[],
                dead_cards=[],
                pot=pot_val,
                stack=stack_val,
                call_amount=call_val,
                active_players=0,
                is_my_turn=False,
                action_points=action_points,
            )
            overlay.update_frame(frame)
            overlay.update_detections(yolo_detections)
            overlay.update_snapshot(snapshot)
            overlay.update_decision(
                "wait", cycle_id=cycle, cycle_ms=cycle_ms,
                equity=0.0, spr=99.0, street="preflop",
            )

    except KeyboardInterrupt:
        pass
    finally:
        overlay.stop()
        print(_c(_CYAN, f"  Overlay encerrado após {cycle} ciclos.\n"))


def _preview_static_overlay(image_path: str) -> None:
    """Abre uma imagem estática no Terminator Vision com inferência YOLO + OCR.

    Renderiza no frame:
      - Detecções YOLO (bounding boxes) usando o modelo configurado
      - Valores OCR lidos das regiões calibradas (pot, stack, call)
      - Pontos de `action_buttons` do config.yaml
      - Regiões OCR (`pot`, `hero_stack`, `call_amount`)
      - Grid opcional (`overlay.show_grid`)
    """
    try:
        import cv2  # type: ignore[import-untyped]
    except ImportError:
        print(_c(_RED, "  OpenCV nao encontrado. Instale: pip install opencv-python\n"))
        return

    from tools.terminator_vision import TerminatorVision
    from utils.config import OCRRuntimeConfig

    image_candidate = Path(image_path)
    if not image_candidate.is_absolute():
        image_candidate = (_PROJECT_ROOT / image_candidate).resolve()

    if not image_candidate.exists():
        print(_c(_RED, f"  Imagem nao encontrada: {image_candidate}\n"))
        return

    frame = cv2.imread(str(image_candidate))
    if frame is None:
        print(_c(_RED, f"  Falha ao abrir imagem: {image_candidate}\n"))
        return

    # --- Inferência YOLO no frame estático ---
    yolo_detections: list[dict[str, object]] = []
    model_path = cfg.get_str("vision.model_path", "")
    if model_path:
        model_file = Path(model_path)
        if not model_file.is_absolute():
            model_file = (_PROJECT_ROOT / model_file).resolve()
        if model_file.exists():
            try:
                from ultralytics import YOLO  # type: ignore[import-untyped]

                confidence = cfg.get_float("vision.confidence_threshold", 0.35)
                model = YOLO(str(model_file))
                results = model.predict(source=frame, conf=confidence, verbose=False)
                if results and len(results) > 0:
                    result = results[0]
                    names: dict[int, str] = getattr(result, "names", {})
                    boxes = getattr(result, "boxes", None)
                    if boxes is not None:
                        cls_list = boxes.cls.tolist() if boxes.cls is not None else []
                        xyxy_list = boxes.xyxy.tolist() if boxes.xyxy is not None else []
                        conf_list = boxes.conf.tolist() if boxes.conf is not None else []
                        for idx, (cls_idx, xyxy) in enumerate(zip(cls_list, xyxy_list)):
                            label = names.get(int(cls_idx), "")
                            conf_val = float(conf_list[idx]) if idx < len(conf_list) else 0.0
                            x1, y1, x2, y2 = (float(v) for v in xyxy)
                            cx = int((x1 + x2) / 2.0)
                            cy = int((y1 + y2) / 2.0)
                            w = int(x2 - x1)
                            h = int(y2 - y1)
                            yolo_detections.append({
                                "label": label, "confidence": conf_val,
                                "cx": cx, "cy": cy, "w": w, "h": h,
                            })
                print(_c(_GREEN, f"  YOLO: {len(yolo_detections)} detecções no frame."))
            except Exception as err:
                print(_c(_YELLOW, f"  Aviso YOLO: {err}"))
        else:
            print(_c(_YELLOW, f"  Modelo não encontrado: {model_file}"))
    else:
        print(_c(_YELLOW, "  vision.model_path vazio — sem inferência YOLO."))

    # --- OCR nas regiões calibradas ---
    # OCRRuntimeConfig lê env vars; para modo club precisamos injetar do YAML
    ocr_pot_str = cfg.get_str("ocr.pot_region", "")
    ocr_stack_str = cfg.get_str("ocr.stack_region", "")
    ocr_call_str = cfg.get_str("ocr.call_region", "")
    if ocr_pot_str:
        os.environ["TITAN_OCR_POT_REGION"] = ocr_pot_str
    if ocr_stack_str:
        os.environ["TITAN_OCR_STACK_REGION"] = ocr_stack_str
    if ocr_call_str:
        os.environ["TITAN_OCR_CALL_REGION"] = ocr_call_str

    ocr_regions = OCRRuntimeConfig().regions()
    pot_val = 0.0
    stack_val = 0.0
    call_val = 0.0
    try:
        from agent.vision_ocr import TitanOCR

        ocr_cfg_use_easy = cfg.get_bool("ocr.use_easyocr", False)
        ocr_cfg_tess_cmd = cfg.get_str("ocr.tesseract_cmd", "")
        ocr_engine = TitanOCR(use_easyocr=ocr_cfg_use_easy, tesseract_cmd=ocr_cfg_tess_cmd or None)

        for region_name, (rx, ry, rw, rh) in ocr_regions.items():
            if rw <= 0 or rh <= 0:
                continue
            crop = frame[ry : ry + rh, rx : rx + rw]
            if crop is not None and crop.size > 0:
                value = ocr_engine.read_numeric_region(crop, key=region_name)
                if "pot" in region_name:
                    pot_val = value
                elif "stack" in region_name or "hero" in region_name:
                    stack_val = value
                elif "call" in region_name:
                    call_val = value
        print(_c(_GREEN, f"  OCR: pot={pot_val:.1f}  stack={stack_val:.1f}  call={call_val:.1f}"))
    except Exception as err:
        print(_c(_YELLOW, f"  Aviso OCR: {err}"))

    # --- Montar action_points ---
    action_buttons_cfg = cfg.get_dict("action_buttons")
    action_points: dict[str, tuple[int, int]] = {}
    for action_name in ("fold", "call", "raise_small", "raise_big"):
        raw = action_buttons_cfg.get(action_name)
        if isinstance(raw, list) and len(raw) == 2:
            try:
                action_points[action_name] = (int(raw[0]), int(raw[1]))
            except (TypeError, ValueError):
                continue

    snapshot = SimpleNamespace(
        hero_cards=[],
        board_cards=[],
        dead_cards=[],
        pot=pot_val,
        stack=stack_val,
        call_amount=call_val,
        active_players=0,
        is_my_turn=False,
        action_points=action_points,
    )

    overlay = TerminatorVision(
        max_fps=cfg.get_int("overlay.max_fps", 10),
        hud_width=cfg.get_int("overlay.hud_width", 320),
        show_grid=cfg.get_bool("overlay.show_grid", False),
        grid_size=cfg.get_int("overlay.grid_size", 50),
        window_name="TITAN: Calibracao Estatica",
    )
    overlay.update_frame(frame)
    overlay.update_detections(yolo_detections)
    overlay.update_snapshot(snapshot)
    overlay.update_ocr_regions(ocr_regions)
    overlay.update_decision("wait", cycle_id=0, cycle_ms=0.0, equity=0.0, spr=99.0, street="preflop")

    print(_c(_CYAN, "  Preview aberto. Pressione Q na janela para fechar."))
    print(_c(_CYAN, f"  Modelo: {model_path or '(nenhum)'}"))
    print(_c(_CYAN, f"  Detecções: {len(yolo_detections)} | Botões: {len(action_points)}\n"))

    overlay.start()
    try:
        while overlay.is_running:
            time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    finally:
        overlay.stop()


def _action_show_config() -> None:
    """Mostra configuração atual (yaml + env)."""
    print(_c(_CYAN, "\n=== CONFIGURACAO ATUAL ===\n"))

    try:
        from utils.titan_config import cfg
        cfg.reload()

        sections = {
            "poker": ["table_profile", "table_position", "opponents", "aggression_level",
                       "god_mode_bonus", "commitment_spr", "commitment_equity", "simulations"],
            "vision": ["model_path", "confidence_threshold", "emulator_title", "collect_data"],
            "overlay": ["enabled", "max_fps", "show_hud"],
            "agent": ["agent_id", "table_id", "heartbeat_seconds", "max_cycles",
                       "use_mock_vision", "mock_vision_scenario"],
            "ghost_mouse": ["enabled"],
            "server": ["zmq_bind", "redis_url"],
        }

        for section, keys in sections.items():
            print(f"  {_BOLD}{_CYAN}[{section}]{_RESET}")
            for key in keys:
                full_key = f"{section}.{key}"
                value = cfg.get_raw(full_key, "---")
                env_key = cfg._env_key(full_key)
                env_val = os.getenv(env_key, "")
                source = f" {_DIM}(env: {env_key}={env_val}){_RESET}" if env_val else ""
                print(f"    {key}: {_GREEN}{value}{_RESET}{source}")
            print()

    except Exception as e:
        print(f"  {_RED}Erro ao ler config: {e}{_RESET}\n")


def _action_visual_calibrator() -> None:
    """Abre calibrador visual interativo com mouse (OpenCV)."""
    print(_c(_MAGENTA, "\n=== CALIBRADOR VISUAL (RATO) ===\n"))

    image_default = _CLI_IMAGE_PATH or os.getenv("TITAN_IMAGE_PATH", "").strip() or "club_table_reference.png"
    if image_default:
        print(f"  Dica: imagem padrão ativa = {image_default}")
    image_value = input("  Caminho da imagem (Enter para padrão): ").strip() or image_default

    config_default = _CLI_CONFIG_PATH or os.getenv("TITAN_CONFIG_FILE", "").strip() or "config_club.yaml"
    print(f"  Config alvo padrão: {config_default}")
    config_value = input("  Config YAML alvo (Enter para padrão): ").strip() or config_default

    if not image_value:
        print(_c(_RED, "  Nenhuma imagem informada. Use --image_path ou TITAN_IMAGE_PATH.\n"))
        return

    python = _find_python()
    _run_command([
        python,
        "-m",
        "tools.visual_calibrator",
        "--image",
        image_value,
        "--config",
        config_value,
    ])


def _action_card_annotator() -> None:
    """Abre anotador interativo de cartas (OpenCV)."""
    print(_c(_MAGENTA, "\n=== ANOTADOR DE CARTAS ===\n"))
    print(_c(_CYAN, "  Ferramenta para anotar cartas de poker nos frames capturados."))
    print(_c(_CYAN, "  Clique+arraste ao redor de cada carta, depois digite o label (ex: Ah, 9s).\n"))

    source_default = "data/to_annotate"
    source = input(f"  Diretório de imagens [{source_default}]: ").strip() or source_default

    config_default = _CLI_CONFIG_PATH or os.getenv("TITAN_CONFIG_FILE", "").strip() or "config_club.yaml"
    config_val = input(f"  Config YAML [{config_default}]: ").strip() or config_default

    start = input("  Começar da imagem nº [0]: ").strip() or "0"

    python = _find_python()
    _run_command([
        python,
        "-m",
        "tools.card_annotator",
        "--source",
        source,
        "--config",
        config_val,
        "--start-from",
        start,
    ])


def _action_train_yolo() -> None:
    """Treina ou retoma treinamento YOLO."""
    print(_c(_MAGENTA, "\n=== TREINO YOLO ===\n"))

    # Count current dataset
    dataset_dir = _PROJECT_ROOT / "datasets" / "titan_cards"
    if dataset_dir.exists():
        train_imgs = list((dataset_dir / "images" / "train").glob("*.png"))
        val_imgs = list((dataset_dir / "images" / "val").glob("*.png"))
        print(_c(_CYAN, f"  Dataset atual: {len(train_imgs)} train / {len(val_imgs)} val"))
    else:
        print(_c(_YELLOW, "  Dataset não encontrado. Rode prepare_dataset primeiro."))

    print()
    print(_c(_CYAN, "  [1] Reconstruir dataset (prepare_dataset + auto_labeler)"))
    print(_c(_CYAN, "  [2] Treinar do zero (yolov8n.pt base)"))
    print(_c(_CYAN, "  [3] Fine-tune modelo existente (titan_v1.pt → v2)"))
    print(_c(_CYAN, "  [4] Apenas reconstruir dataset (sem treinar)"))

    choice = input("\n  Escolha (1/2/3/4): ").strip()
    python = _find_python()

    if choice in ("1", "4"):
        # Rebuild dataset
        print(_c(_CYAN, "\n  Reconstruindo dataset...\n"))
        _run_command([python, "-m", "training.prepare_dataset", "--include-unlabeled"])
        if choice == "4":
            return

    epochs = input("  Epochs [150]: ").strip() or "150"
    batch = input("  Batch size [16]: ").strip() or "16"
    name = input("  Nome do run [titan_v2]: ").strip() or "titan_v2"

    if choice == "3":
        model_current = cfg.get_str("vision.model_path", "models/titan_v1.pt")
        model = input(f"  Modelo base [{model_current}]: ").strip() or model_current
    else:
        model = "yolov8n.pt"

    report_path = f"reports/train_report_{name}.json"

    cmd = [
        python, "training/train_yolo.py",
        "--data", "training/data.yaml",
        "--model", model,
        "--epochs", epochs,
        "--batch", batch,
        "--name", name,
        "--save-report", report_path,
    ]
    print(_c(_GREEN, f"\n  Comando: {' '.join(cmd)}\n"))
    _run_command(cmd)


# ═══════════════════════════════════════════════════════════════════════════
# Menu Principal
# ═══════════════════════════════════════════════════════════════════════════

_MENU_ITEMS = [
    ("1", "Iniciar Modo de Treino (Mock)", _action_mock_training),
    ("2", "Iniciar Coleta de Dados (Observar Mesa)", _action_collect_data),
    ("3", "Iniciar Jogo Real (Cuidado!)", _action_real_play),
    ("4", "Teste de Endurance (100 ciclos)", _action_endurance_test),
    ("5", "Diagnostico do Sistema", _action_diagnostics),
    ("6", "Ver Configuracao Atual", _action_show_config),
    ("7", "Editar config.yaml", _action_edit_config),
    ("8", "Overlay Standalone (Testar Visao)", _action_overlay_standalone),
    ("9", "Calibrador Visual (Rato)", _action_visual_calibrator),
    ("A", "Anotar Cartas (Card Annotator)", _action_card_annotator),
    ("T", "Treinar YOLO", _action_train_yolo),
    ("0", "Sair", None),
]


def _print_banner() -> None:
    """Exibe o banner do Cockpit."""
    banner = f"""
{_CYAN}{_BOLD}╔══════════════════════════════════════════════════════════╗
║          PROJECT TITAN: CENTRAL DE COMANDO              ║
║                     ◆ COCKPIT ◆                         ║
╚══════════════════════════════════════════════════════════╝{_RESET}
"""
    print(banner)


def _print_menu() -> None:
    """Exibe opções do menu."""
    for key, label, action in _MENU_ITEMS:
        if key == "0":
            color = _DIM
        elif key == "3":
            color = _RED  # jogo real = vermelho de cautela
        elif key in ("1", "4"):
            color = _GREEN
        elif key == "5":
            color = _CYAN
        else:
            color = _WHITE

        print(f"  {_BOLD}{key}.{_RESET} {color}{label}{_RESET}")

    print()


def main() -> None:
    """Loop principal do Cockpit."""
    global _CLI_IMAGE_PATH, _CLI_CONFIG_PATH

    args = _parse_cli_args()
    _CLI_IMAGE_PATH = str(args.image_path or "").strip()
    _CLI_CONFIG_PATH = str(args.config or "").strip()

    _enable_ansi()
    os.chdir(str(_PROJECT_ROOT))

    if _CLI_CONFIG_PATH:
        _apply_runtime_config(_CLI_CONFIG_PATH)

    while True:
        _print_banner()
        _print_menu()

        try:
            choice = input(f"  {_BOLD}Escolha: {_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{_YELLOW}Saindo...{_RESET}")
            break

        if choice == "0" or choice.lower() in ("q", "quit", "exit", "sair"):
            print(f"\n{_CYAN}Ate logo, Titan!{_RESET}\n")
            break

        found = False
        for key, label, action_fn in _MENU_ITEMS:
            if choice == key and action_fn is not None:
                action_fn()
                found = True
                break

        if not found and choice != "0":
            print(f"\n  {_RED}Opcao invalida: {choice}{_RESET}\n")

        # Pausa antes de mostrar menu novamente
        if found:
            input(f"\n  {_DIM}Pressione Enter para continuar...{_RESET}")


if __name__ == "__main__":
    main()
