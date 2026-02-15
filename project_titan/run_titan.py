"""run_titan.py — Script de inicialização do Project Titan.

Verifica pré-requisitos, inicia o HiveBrain (servidor ZMQ) e o(s)
agente(s) de poker com logs coloridos no terminal.

Fluxo de startup
-----------------
  1. Verifica se o Redis está acessível.
  2. Inicia o HiveBrain (core.hive_brain) em processo separado.
  3. Aguarda 2 segundos para o servidor estabilizar.
  4. Inicia o(s) PokerAgent(s) (agent.poker_agent).
  5. Monitora os processos e exibe logs coloridos.

Uso::

    python run_titan.py
    python run_titan.py --agents 2 --emulator "MEmu"
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
import time


# ═══════════════════════════════════════════════════════════════════════════
# Cores ANSI para o terminal
# ═══════════════════════════════════════════════════════════════════════════

_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"


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
╚══════════════════════════════════════════════════════════╝{_RESET}
""")


def _log(level: str, msg: str) -> None:
    """Imprime uma mensagem com prefixo colorido."""
    colors = {
        "INFO": _CYAN,
        "OK": _GREEN,
        "WARN": _YELLOW,
        "ERROR": _RED,
        "STEP": _MAGENTA,
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
        _log("OK", f"Redis acessível em {redis_url}")
        return True
    except ImportError:
        _log("ERROR", "Módulo 'redis' não instalado. Execute: pip install redis")
        return False
    except Exception as err:
        _log("ERROR", f"Redis inacessível em {redis_url}: {err}")
        _log("WARN", "Inicie o Redis antes de executar o Titan:")
        _log("WARN", "  Windows: redis-server (ou via Docker: docker run -d -p 6379:6379 redis)")
        return False


def check_dependencies() -> bool:
    """Verifica se as dependências críticas estão instaladas."""
    required = ["zmq", "redis", "mss", "pyautogui", "pygetwindow", "colorama"]
    missing: list[str] = []
    for mod in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        _log("ERROR", f"Dependências faltando: {', '.join(missing)}")
        _log("WARN", "Execute: pip install -r requirements.txt")
        return False

    _log("OK", "Todas as dependências instaladas")
    return True


def find_python() -> str:
    """Retorna o caminho do Python (preferindo o venv ativo)."""
    # Se estamos num venv, usa o Python dele
    venv_python = os.path.join(sys.prefix, "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


# ═══════════════════════════════════════════════════════════════════════════
# Inicialização dos processos
# ═══════════════════════════════════════════════════════════════════════════

def start_hive_brain(python: str, cwd: str, env: dict[str, str]) -> subprocess.Popen:
    """Inicia o HiveBrain (servidor ZMQ) em processo separado."""
    _log("STEP", "Iniciando HiveBrain (servidor ZMQ)...")
    proc = subprocess.Popen(
        [python, "-m", "core.hive_brain"],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _log("OK", f"HiveBrain iniciado (PID={proc.pid})")
    return proc


def start_agent(
    python: str,
    cwd: str,
    env: dict[str, str],
    agent_id: str,
    table_id: str,
) -> subprocess.Popen:
    """Inicia um PokerAgent em processo separado."""
    _log("STEP", f"Iniciando Agente {agent_id} (mesa={table_id})...")
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
    """Entry-point principal do run_titan.py."""
    _enable_ansi_windows()
    _banner()

    parser = argparse.ArgumentParser(
        description="Project Titan — Startup Script",
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
        "--ghost-mouse", action="store_true",
        help="Ativar controle real do mouse via GhostMouse",
    )
    args = parser.parse_args()

    # Resolve paths
    project_dir = os.path.dirname(os.path.abspath(__file__))
    python = find_python()

    _log("INFO", f"Python: {python}")
    _log("INFO", f"Diretório: {project_dir}")

    # ── 1. Verificar dependências ──────────────────────────────────────
    _log("STEP", "═══ ETAPA 1/4: Verificando dependências ═══")
    if not check_dependencies():
        return 1

    # ── 2. Verificar Redis ─────────────────────────────────────────────
    _log("STEP", "═══ ETAPA 2/4: Verificando Redis ═══")
    redis_url = args.redis or os.getenv("TITAN_REDIS_URL", "redis://127.0.0.1:6379/0")
    if not check_redis(redis_url):
        _log("WARN", "Continuando sem Redis (modo in-memory)...")

    # Monta variáveis de ambiente para os subprocessos
    env = dict(os.environ)
    env["TITAN_REDIS_URL"] = redis_url
    if args.emulator:
        env["TITAN_EMULATOR_TITLE"] = args.emulator
    if args.model:
        env["TITAN_YOLO_MODEL"] = args.model
    if args.ghost_mouse:
        env["TITAN_GHOST_MOUSE"] = "1"

    # ── 3. Iniciar HiveBrain ──────────────────────────────────────────
    _log("STEP", "═══ ETAPA 3/4: Iniciando HiveBrain ═══")
    hive_proc = start_hive_brain(python, project_dir, env)

    _log("INFO", "Aguardando 2 segundos para o servidor estabilizar...")
    time.sleep(2.0)

    # Verifica se o HiveBrain ainda está rodando
    if hive_proc.poll() is not None:
        _log("ERROR", "HiveBrain encerrou prematuramente!")
        if hive_proc.stdout:
            remaining = hive_proc.stdout.read()
            if remaining:
                print(f"  {_RED}{remaining}{_RESET}")
        return 1

    _log("OK", "HiveBrain estável e escutando")

    # ── 4. Iniciar Agente(s) ──────────────────────────────────────────
    _log("STEP", f"═══ ETAPA 4/4: Iniciando {args.agents} agente(s) ═══")
    agent_procs: list[subprocess.Popen] = []
    for i in range(args.agents):
        agent_id = f"{i + 1:02d}"
        proc = start_agent(python, project_dir, env, agent_id, args.table)
        agent_procs.append(proc)
        time.sleep(0.5)  # Pequeno delay entre agentes

    # ── Monitoramento ──────────────────────────────────────────────────
    print()
    _log("OK", f"{_GREEN}{_BOLD}Project Titan rodando!{_RESET}")
    _log("INFO", "Pressione Ctrl+C para encerrar todos os processos.")
    print()

    try:
        while True:
            # Stream output do HiveBrain
            stream_output(hive_proc, "HiveBrain")

            # Stream output dos agentes
            for idx, proc in enumerate(agent_procs):
                stream_output(proc, f"Agent-{idx + 1:02d}")

            # Verifica se algum processo morreu
            if hive_proc.poll() is not None:
                _log("ERROR", "HiveBrain encerrou inesperadamente!")
                break

            dead_agents = [i for i, p in enumerate(agent_procs) if p.poll() is not None]
            for idx in dead_agents:
                _log("WARN", f"Agente {idx + 1:02d} encerrou (restarting...)")
                agent_procs[idx] = start_agent(
                    python, project_dir, env,
                    f"{idx + 1:02d}", args.table,
                )

            time.sleep(0.1)

    except KeyboardInterrupt:
        print()
        _log("WARN", "Ctrl+C detectado. Encerrando processos...")

    finally:
        # Encerra todos os processos
        for proc in agent_procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        try:
            hive_proc.terminate()
            hive_proc.wait(timeout=5)
        except Exception:
            try:
                hive_proc.kill()
            except Exception:
                pass

        _log("OK", "Todos os processos encerrados. Até a próxima! 🃏")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
