"""run_titan.py — Script de inicialização do Project Titan.

Orquestra a inicialização completa do sistema autônomo de poker PLO6:

  1. Habilita cores ANSI no terminal Windows.
  2. Verifica se o Redis está acessível (fallback in-memory OK).
  3. Localiza a janela do LDPlayer9 via ``VisionYolo.find_window()``.
  4. Inicia o HiveBrain (servidor ZMQ) em **thread** dedicada.
  5. Inicia o loop principal do PokerAgent na thread principal.
  6. Exibe logs coloridos em tempo real para monitoramento.

Uso::

    python run_titan.py
    python run_titan.py --agents 2 --emulator "LDPlayer"
    python run_titan.py --model best.pt --table mesa_1

Variáveis de ambiente (opcionais — sobrescrevem args)
-----------------------------------------------------
``TITAN_REDIS_URL``         URL do Redis (default ``redis://127.0.0.1:6379/0``).
``TITAN_ZMQ_BIND``          Bind do HiveBrain (default ``tcp://0.0.0.0:5555``).
``TITAN_YOLO_MODEL``        Caminho do modelo YOLO ``.pt``.
``TITAN_EMULATOR_TITLE``    Título da janela do emulador.
``TITAN_GHOST_MOUSE``       Ativar controle real do mouse (``1``/``0``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from typing import Any

try:
    import yaml as _yaml  # type: ignore[import-untyped]
except Exception:
    _yaml = None


# ═══════════════════════════════════════════════════════════════════════════
# Cores ANSI para o terminal
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


def _enable_ansi_windows() -> None:
    """Habilita ANSI no Windows 10+ (conhost / Windows Terminal)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def _banner() -> None:
    """Exibe o banner do Project Titan."""
    print(f"""
{_CYAN}{_BOLD}╔══════════════════════════════════════════════════════════╗
║              🃏  PROJECT TITAN  🃏                        ║
║         Autonomous PLO6 Poker Engine                     ║
║         PC Host Controller Architecture                  ║
║                                                          ║
║   Visão: win32gui + mss + YOLOv8                         ║
║   Cérebro: HiveBrain + Monte-Carlo PLO6                  ║
║   Memória: Redis (squad) / In-Memory (solo)              ║
╚══════════════════════════════════════════════════════════╝{_RESET}
""")


def _log(level: str, msg: str) -> None:
    """Imprime uma mensagem com prefixo colorido e timestamp."""
    colors = {
        "INFO":   _CYAN,
        "OK":     _GREEN,
        "WARN":   _YELLOW,
        "ERROR":  _RED,
        "STEP":   _MAGENTA,
        "HIVE":   _BLUE,
        "AGENT":  _WHITE,
        "VISION": _CYAN,
    }
    color = colors.get(level, _RESET)
    timestamp = time.strftime("%H:%M:%S")
    print(f"{_BOLD}[{timestamp}]{_RESET} {color}[{level}]{_RESET} {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# Verificações de pré-requisitos
# ═══════════════════════════════════════════════════════════════════════════

def check_redis(redis_url: str) -> bool:
    """Verifica se o Redis está acessível. Retorna True se OK."""
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_timeout=3)
        client.ping()
        _log("OK", f"Redis acessível em {_GREEN}{redis_url}{_RESET}")
        return True
    except ImportError:
        _log("WARN", "Módulo 'redis' não instalado — modo in-memory ativo")
        return False
    except Exception as err:
        _log("WARN", f"Redis inacessível ({err}) — modo in-memory ativo")
        return False


def check_emulator(title_pattern: str) -> bool:
    """Localiza a janela do emulador usando VisionYolo.

    Usa o EmulatorWindow com win32gui para buscar a janela pelo título
    parcial e validar que a ROI (canvas do jogo) é utilizável.

    Returns:
        ``True`` se a janela foi encontrada com ROI válida.
    """
    try:
        from agent.vision_yolo import VisionYolo

        vision = VisionYolo()
        vision.emulator.title_pattern = title_pattern
        found = vision.find_window()

        if found:
            emu = vision.emulator
            _log("OK", f"LDPlayer encontrado: {_GREEN}{emu!r}{_RESET}")
            _log("VISION", (
                f"Janela: ({emu.left},{emu.top}) {emu.width}x{emu.height}  "
                f"ROI: ({emu.offset_x},{emu.offset_y}) {emu.canvas_width}x{emu.canvas_height}  "
                f"Chrome: top={emu._chrome_top} right={emu._chrome_right}"
            ))
            return True
        else:
            _log("WARN", f"Janela '{title_pattern}' não encontrada — verificar emulador")
            return False
    except ImportError as err:
        _log("WARN", f"Módulo de visão indisponível: {err}")
        return False
    except Exception as err:
        _log("WARN", f"Erro ao localizar emulador: {err}")
        return False


# ── Emulator configuration spec (fixed) ──────────────────────────────────
_EXPECTED_W = 720
_EXPECTED_H = 1280
_EXPECTED_DPI = 320


def check_emulator_resolution() -> bool:
    """Validate the emulator is running at the expected 720x1280 DPI 320.

    Queries ``adb shell wm size`` and ``adb shell wm density`` to verify
    the Android virtual display matches our fixed configuration.

    Returns:
        ``True`` if resolution and density match expectations.
    """
    adb = os.getenv("TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe")
    device = os.getenv("TITAN_ADB_DEVICE", "emulator-5554")

    ok = True

    # ── Check resolution ────────────────────────────────────────────
    try:
        res = subprocess.run(
            [adb, "-s", device, "shell", "wm", "size"],
            capture_output=True, text=True, timeout=5,
        )
        output = res.stdout.strip()
        # "Physical size: 720x1280" or "Physical size: 720x1280\nOverride size: ..."
        lines = output.splitlines()
        physical = None
        override = None
        for line in lines:
            if "Override" in line:
                override = line.split(":")[-1].strip()
            elif "Physical" in line:
                physical = line.split(":")[-1].strip()

        if override:
            _log("ERROR", (
                f"{_RED}WM SIZE OVERRIDE DETECTADO: {override}{_RESET}\n"
                f"         O override QUEBRA o input do Unity/PPPoker!\n"
                f"         Execute: adb -s {device} shell wm size reset"
            ))
            ok = False
        elif physical:
            expected = f"{_EXPECTED_W}x{_EXPECTED_H}"
            if physical != expected:
                _log("ERROR", (
                    f"{_RED}Resolução incorreta: {physical} (esperado {expected}){_RESET}\n"
                    f"         Altere no LDPlayer: Configuração → Tela → Celular → 720x1280 (DPI 320)"
                ))
                ok = False
            else:
                _log("OK", f"Resolução: {_GREEN}{physical}{_RESET}")
        else:
            _log("WARN", f"Não foi possível ler wm size: {output}")
    except Exception as exc:
        _log("WARN", f"Falha ao verificar resolução via ADB: {exc}")

    # ── Check density/DPI ───────────────────────────────────────────
    try:
        res = subprocess.run(
            [adb, "-s", device, "shell", "wm", "density"],
            capture_output=True, text=True, timeout=5,
        )
        output = res.stdout.strip()
        lines = output.splitlines()
        density = None
        for line in lines:
            if "Physical" in line or "density" in line.lower():
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        density = int(parts[-1].strip())
                    except ValueError:
                        pass

        if density is not None:
            if density != _EXPECTED_DPI:
                _log("WARN", (
                    f"DPI: {density} (esperado {_EXPECTED_DPI}) — "
                    f"pode afetar precisão do OCR"
                ))
            else:
                _log("OK", f"DPI: {_GREEN}{density}{_RESET}")
        else:
            _log("WARN", f"Não foi possível ler wm density: {output}")
    except Exception as exc:
        _log("WARN", f"Falha ao verificar DPI via ADB: {exc}")

    return ok


def check_dependencies() -> bool:
    """Verifica se as dependências críticas estão instaladas."""
    required = {
        "zmq": "pyzmq",
        "redis": "redis",
        "mss": "mss",
        "win32gui": "pywin32",
    }
    optional = {
        "ultralytics": "ultralytics (YOLO)",
        "numpy": "numpy",
    }

    missing_critical: list[str] = []
    for mod, pkg_name in required.items():
        try:
            __import__(mod)
            _log("OK", f"  {pkg_name}")
        except ImportError:
            missing_critical.append(pkg_name)
            _log("ERROR", f"  {pkg_name} — FALTANDO")

    for mod, pkg_name in optional.items():
        try:
            __import__(mod)
            _log("OK", f"  {pkg_name}")
        except ImportError:
            _log("WARN", f"  {pkg_name} — opcional, não instalado")

    if missing_critical:
        _log("ERROR", f"Dependências críticas faltando: {', '.join(missing_critical)}")
        _log("WARN", "Execute: pip install -r requirements.txt")
        return False

    _log("OK", "Todas as dependências críticas instaladas")
    return True


def find_python() -> str:
    """Retorna o caminho do Python (preferindo o venv ativo)."""
    venv_python = os.path.join(sys.prefix, "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


# ═══════════════════════════════════════════════════════════════════════════
# HiveBrain — thread dedicada
# ═══════════════════════════════════════════════════════════════════════════

_hive_thread: threading.Thread | None = None
_hive_running = threading.Event()
_hive_error: str = ""


def _run_hive_brain(bind_address: str, redis_url: str) -> None:
    """Entry point da thread do HiveBrain.

    Importa e inicia o servidor ZMQ.  Se falhar, sinaliza via
    ``_hive_error`` para que a thread principal possa reportar.
    """
    global _hive_error
    try:
        from core.hive_brain import HiveBrain

        brain = HiveBrain(bind_address=bind_address, redis_url=redis_url)
        _hive_running.set()
        _log("HIVE", f"HiveBrain escutando em {_BLUE}{bind_address}{_RESET}")
        brain.start()
    except Exception as err:
        _hive_error = str(err)
        _hive_running.set()  # Desbloqueia a thread principal para reportar erro
        _log("ERROR", f"HiveBrain falhou: {err}")


def start_hive_brain_thread(bind_address: str, redis_url: str) -> bool:
    """Inicia o HiveBrain em uma thread daemon.

    Returns:
        ``True`` se o HiveBrain iniciou com sucesso.
    """
    global _hive_thread
    _log("STEP", "Iniciando HiveBrain (servidor ZMQ)...")

    _hive_thread = threading.Thread(
        target=_run_hive_brain,
        args=(bind_address, redis_url),
        name="HiveBrain",
        daemon=True,
    )
    _hive_thread.start()

    # Aguarda até 5 segundos para o HiveBrain ficar pronto
    _hive_running.wait(timeout=5.0)

    if _hive_error:
        _log("ERROR", f"HiveBrain falhou ao iniciar: {_hive_error}")
        return False

    _log("OK", f"HiveBrain rodando (thread={_hive_thread.name})")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# PokerAgent — subprocess (mantém isolamento)
# ═══════════════════════════════════════════════════════════════════════════

def start_agent(
    python: str,
    cwd: str,
    env: dict[str, str],
    agent_id: str,
    table_id: str,
) -> subprocess.Popen:
    """Inicia um PokerAgent em processo separado."""
    _log("STEP", f"Iniciando Agente {_WHITE}{agent_id}{_RESET} (mesa={table_id})...")
    agent_env = dict(env)
    agent_env["TITAN_AGENT_ID"] = agent_id
    agent_env["TITAN_TABLE_ID"] = table_id

    proc = subprocess.Popen(
        [python, "-m", "agent.poker_agent"],
        cwd=cwd,
        env=agent_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _log("OK", f"Agente {agent_id} iniciado (PID={proc.pid})")
    return proc


def stream_output(proc: subprocess.Popen, label: str) -> None:
    """Lê e imprime a saída de um subprocess (não-bloqueante por linha)."""
    if proc.stdout is None:
        return
    try:
        line = proc.stdout.readline()
        if line:
            print(f"  {_BOLD}[{label}]{_RESET} {line.rstrip()}")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    """Entry-point principal do run_titan.py.

    Orquestra a sequência completa de startup:

    1. Habilita cores ANSI.
    2. Verifica dependências (zmq, redis, mss, win32gui).
    3. Verifica Redis (continua sem se indisponível).
    4. Localiza o LDPlayer via VisionYolo (win32gui + ROI).
    5. Inicia o HiveBrain em thread daemon.
    6. Inicia o(s) PokerAgent(s) em subprocessos.
    7. Monitora e exibe logs coloridos em tempo real.
    """
    _enable_ansi_windows()
    _banner()

    parser = argparse.ArgumentParser(
        description="Project Titan — PLO6 Autonomous Poker Engine",
    )
    parser.add_argument(
        "--agents", type=int, default=1,
        help="Número de agentes para iniciar (default: 1)",
    )
    parser.add_argument(
        "--emulator", type=str, default="",
        help="Título da janela do emulador (default: LDPlayer)",
    )
    parser.add_argument(
        "--model", type=str, default="",
        help="Caminho do modelo YOLO .pt",
    )
    parser.add_argument(
        "--table", type=str, default="table_default",
        help="ID da mesa lógica (default: table_default)",
    )
    parser.add_argument(
        "--redis", type=str, default="",
        help="URL do Redis (default: redis://127.0.0.1:6379/0)",
    )
    parser.add_argument(
        "--zmq-bind", type=str, default="",
        help="Endereço ZMQ bind do HiveBrain (default: tcp://0.0.0.0:5555)",
    )
    parser.add_argument(
        "--ghost-mouse", action="store_true",
        help="Ativar controle real do mouse via GhostMouse",
    )
    args = parser.parse_args()

    # Resolve paths
    project_dir = os.path.dirname(os.path.abspath(__file__))
    python = find_python()
    redis_url = args.redis or os.getenv("TITAN_REDIS_URL", "redis://:titan_secret@127.0.0.1:6379/0")
    zmq_bind = args.zmq_bind or os.getenv("TITAN_ZMQ_BIND", "tcp://0.0.0.0:5555")
    emulator_title = args.emulator or os.getenv("TITAN_EMULATOR_TITLE", "LDPlayer")

    # Resolve YOLO model path: --model arg > env var > config_club.yaml
    model_path = args.model or os.getenv("TITAN_YOLO_MODEL", "")
    if not model_path:
        for cfg_name in ("config_club.yaml", "config.yaml"):
            cfg_path = os.path.join(project_dir, cfg_name)
            if os.path.isfile(cfg_path) and _yaml is not None:
                try:
                    with open(cfg_path, "r", encoding="utf-8") as _f:
                        _cfg = _yaml.safe_load(_f)
                    if isinstance(_cfg, dict):
                        _vision = _cfg.get("vision", {})
                        if isinstance(_vision, dict):
                            _mp = _vision.get("model_path", "")
                            if _mp:
                                model_path = os.path.join(project_dir, str(_mp))
                                break
                except Exception:
                    pass
    if model_path and not os.path.isabs(model_path):
        model_path = os.path.join(project_dir, model_path)

    _log("INFO", f"Python:     {_CYAN}{python}{_RESET}")
    _log("INFO", f"Diretório:  {_CYAN}{project_dir}{_RESET}")
    _log("INFO", f"Modelo:     {_CYAN}{model_path or '(nenhum)'}{_RESET}")
    _log("INFO", f"Redis:      {_CYAN}{redis_url}{_RESET}")
    _log("INFO", f"ZMQ Bind:   {_CYAN}{zmq_bind}{_RESET}")
    _log("INFO", f"Emulador:   {_CYAN}{emulator_title}{_RESET}")
    _log("INFO", f"Agentes:    {_CYAN}{args.agents}{_RESET}")
    print()

    # ══════════════════════════════════════════════════════════════════
    # ETAPA 1/5: Verificar dependências
    # ══════════════════════════════════════════════════════════════════
    _log("STEP", f"{'═' * 50}")
    _log("STEP", "ETAPA 1/5: Verificando dependências")
    _log("STEP", f"{'═' * 50}")
    deps_ok = check_dependencies()
    if not deps_ok:
        _log("ERROR", "Dependências críticas faltando. Abortando.")
        return 1
    print()

    # ══════════════════════════════════════════════════════════════════
    # ETAPA 2/5: Verificar Redis
    # ══════════════════════════════════════════════════════════════════
    _log("STEP", f"{'═' * 50}")
    _log("STEP", "ETAPA 2/5: Verificando Redis")
    _log("STEP", f"{'═' * 50}")
    redis_ok = check_redis(redis_url)
    if not redis_ok:
        _log("WARN", "Continuando sem Redis — modo in-memory (squad limitado)")
    print()

    # ══════════════════════════════════════════════════════════════════
    # ETAPA 3/5: Localizar LDPlayer via VisionYolo
    # ══════════════════════════════════════════════════════════════════
    _log("STEP", f"{'═' * 50}")
    _log("STEP", "ETAPA 3/5: Localizando janela do emulador")
    _log("STEP", f"{'═' * 50}")
    emulator_ok = check_emulator(emulator_title)
    if not emulator_ok:
        _log("WARN", "Emulador não encontrado — agente rodará em modo simulação")

    # Verificar resolução ADB (720x1280, DPI 320, sem wm size override)
    resolution_ok = check_emulator_resolution()
    if not resolution_ok:
        _log("ERROR", (
            f"{_RED}Resolução do emulador INCORRETA!{_RESET}\n"
            "         Configure no LDPlayer:\n"
            "           Tela → Celular → 720 x 1280 (DPI 320)\n"
            "           60 FPS, Rotação automática ON, Fixar tamanho OFF\n"
            "         Se houver wm size override, execute:\n"
            "           adb shell wm size reset"
        ))
        return 1
    print()

    # Monta variáveis de ambiente para os subprocessos
    env = dict(os.environ)
    env["TITAN_REDIS_URL"] = redis_url
    env["TITAN_ZMQ_BIND"] = zmq_bind
    if args.emulator:
        env["TITAN_EMULATOR_TITLE"] = emulator_title
    if model_path:
        env["TITAN_YOLO_MODEL"] = model_path
    if args.ghost_mouse:
        env["TITAN_GHOST_MOUSE"] = "1"
    else:
        # Auto-enable from config if ghost_mouse.enabled is true
        for cfg_name in ("config_club.yaml", "config.yaml"):
            cfg_path = os.path.join(project_dir, cfg_name)
            if os.path.isfile(cfg_path) and _yaml is not None:
                try:
                    with open(cfg_path, "r", encoding="utf-8") as _f:
                        _cfg = _yaml.safe_load(_f)
                    if isinstance(_cfg, dict):
                        _gm = _cfg.get("ghost_mouse", {})
                        if isinstance(_gm, dict) and _gm.get("enabled") is True:
                            env["TITAN_GHOST_MOUSE"] = "1"
                            break
                except Exception:
                    pass

    # ── Bridge OCR config from YAML → environment ───────────────────
    # OCRRuntimeConfig reads from env vars; here we load settings from
    # config_club.yaml so the agent subprocess picks them up.
    _ocr_yaml_loaded = False
    for cfg_name in ("config_club.yaml", "config.yaml"):
        cfg_path = os.path.join(project_dir, cfg_name)
        if os.path.isfile(cfg_path) and _yaml is not None:
            try:
                with open(cfg_path, "r", encoding="utf-8") as _f:
                    _cfg = _yaml.safe_load(_f)
                if isinstance(_cfg, dict):
                    _ocr = _cfg.get("ocr", {})
                    if isinstance(_ocr, dict):
                        _env_map = {
                            "pot_region":    "TITAN_OCR_POT_REGION",
                            "stack_region":  "TITAN_OCR_STACK_REGION",
                            "call_region":   "TITAN_OCR_CALL_REGION",
                            "tesseract_cmd": "TITAN_TESSERACT_CMD",
                            "use_easyocr":   "TITAN_OCR_USE_EASYOCR",
                            "enabled":       "TITAN_OCR_ENABLED",
                            "pot_min":       "TITAN_OCR_POT_MIN",
                            "pot_max":       "TITAN_OCR_POT_MAX",
                            "stack_min":     "TITAN_OCR_STACK_MIN",
                            "stack_max":     "TITAN_OCR_STACK_MAX",
                            "call_min":      "TITAN_OCR_CALL_MIN",
                            "call_max":      "TITAN_OCR_CALL_MAX",
                        }
                        for yaml_key, env_key in _env_map.items():
                            val = _ocr.get(yaml_key)
                            if val is not None and str(val).strip():
                                env[env_key] = str(val).strip()
                        _ocr_yaml_loaded = True
                        break
            except Exception:
                pass

    # Auto-detect Tesseract binary if not set
    if not env.get("TITAN_TESSERACT_CMD"):
        _tess_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.isfile(_tess_default):
            env["TITAN_TESSERACT_CMD"] = _tess_default
            _log("OK", f"Tesseract detectado: {_tess_default}")
        else:
            import shutil
            _tess_which = shutil.which("tesseract")
            if _tess_which:
                env["TITAN_TESSERACT_CMD"] = _tess_which
                _log("OK", f"Tesseract detectado: {_tess_which}")
            else:
                _log("WARN", "Tesseract NÃO encontrado — OCR ficará desabilitado!")
    else:
        _log("OK", f"Tesseract: {env['TITAN_TESSERACT_CMD']}")

    # Card reader: enable debug for first run diagnostics
    env.setdefault("TITAN_CARD_READER_DEBUG", "1")
    env.setdefault("TITAN_CARD_READER_HERO_OFFSET_Y_TOP", "-420")
    env.setdefault("TITAN_CARD_READER_HERO_OFFSET_Y_BOTTOM", "-260")

    # ══════════════════════════════════════════════════════════════════
    # ETAPA 4/5: Iniciar HiveBrain (thread)
    # ══════════════════════════════════════════════════════════════════
    _log("STEP", f"{'═' * 50}")
    _log("STEP", "ETAPA 4/5: Iniciando HiveBrain (thread ZMQ)")
    _log("STEP", f"{'═' * 50}")
    hive_ok = start_hive_brain_thread(bind_address=zmq_bind, redis_url=redis_url)
    if not hive_ok:
        _log("ERROR", "HiveBrain falhou ao iniciar. Abortando.")
        return 1

    # Aguarda estabilização
    _log("INFO", "Aguardando 2s para o HiveBrain estabilizar...")
    time.sleep(2.0)
    _log("OK", "HiveBrain estável e escutando")
    print()

    # ══════════════════════════════════════════════════════════════════
    # ETAPA 5/5: Iniciar Agente(s)
    # ══════════════════════════════════════════════════════════════════
    _log("STEP", f"{'═' * 50}")
    _log("STEP", f"ETAPA 5/5: Iniciando {args.agents} agente(s)")
    _log("STEP", f"{'═' * 50}")
    agent_procs: list[subprocess.Popen] = []
    for i in range(args.agents):
        agent_id = f"{i + 1:02d}"
        proc = start_agent(python, project_dir, env, agent_id, args.table)
        agent_procs.append(proc)
        time.sleep(0.5)  # Delay entre agentes para evitar contention

    # ══════════════════════════════════════════════════════════════════
    # Monitoramento em tempo real
    # ══════════════════════════════════════════════════════════════════
    print()
    _log("OK", f"{_GREEN}{_BOLD}{'=' * 50}{_RESET}")
    _log("OK", f"{_GREEN}{_BOLD}  PROJECT TITAN RODANDO!{_RESET}")
    _log("OK", f"{_GREEN}{_BOLD}  Modo: {'Squad (Redis)' if redis_ok else 'Solo (In-Memory)'}{_RESET}")
    _log("OK", f"{_GREEN}{_BOLD}  Emulador: {'Conectado' if emulator_ok else 'Simulação'}{_RESET}")
    _log("OK", f"{_GREEN}{_BOLD}  Agentes: {args.agents}{_RESET}")
    _log("OK", f"{_GREEN}{_BOLD}{'=' * 50}{_RESET}")
    _log("INFO", "Pressione Ctrl+C para encerrar todos os processos.")
    _log("INFO", f"{_YELLOW}Pressione F7 para ATIVAR/DESATIVAR automação.{_RESET}")
    print()

    try:
        while True:
            # Stream output dos agentes
            for idx, proc in enumerate(agent_procs):
                stream_output(proc, f"Agent-{idx + 1:02d}")

            # Verifica se a thread do HiveBrain ainda está viva
            if _hive_thread is not None and not _hive_thread.is_alive():
                _log("ERROR", "HiveBrain thread encerrou inesperadamente!")
                break

            # Verifica se algum agente morreu e reinicia
            dead_agents = [i for i, p in enumerate(agent_procs) if p.poll() is not None]
            for idx in dead_agents:
                _log("WARN", f"Agente {idx + 1:02d} encerrou — reiniciando...")
                agent_procs[idx] = start_agent(
                    python, project_dir, env,
                    f"{idx + 1:02d}", args.table,
                )

            time.sleep(0.1)

    except KeyboardInterrupt:
        print()
        _log("WARN", "Ctrl+C detectado. Encerrando processos...")

    finally:
        # Encerra todos os agentes
        for proc in agent_procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        _log("OK", f"{_GREEN}Todos os processos encerrados. Até a próxima! 🃏{_RESET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
