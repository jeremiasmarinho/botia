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

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Resolve raiz do projeto (onde config.yaml vive)
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR if (_SCRIPT_DIR / "config.yaml").exists() else _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


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
    """Abre overlay standalone numa imagem."""
    print(_c(_MAGENTA, "\n=== OVERLAY STANDALONE ===\n"))

    image = input("  Caminho da imagem (ou Enter para frame ao vivo): ").strip()

    if image:
        python = _find_python()
        _run_command([python, "-m", "tools.visual_overlay", "--image", image])
    else:
        print("  Capturando frame ao vivo do emulador...\n")
        python = _find_python()
        code = (
            "from agent.vision_yolo import VisionYolo; "
            "import cv2; "
            "v = VisionYolo(model_path=''); "
            "v.find_window(); "
            "f = v.capture_frame(); "
            "print('Frame capturado' if f is not None else 'ERRO: frame None'); "
            "cv2.imshow('Titan Frame', f) if f is not None else None; "
            "cv2.waitKey(0) if f is not None else None; "
            "cv2.destroyAllWindows() if f is not None else None"
        )
        _run_command([python, "-c", code])


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
    _enable_ansi()
    os.chdir(str(_PROJECT_ROOT))

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
