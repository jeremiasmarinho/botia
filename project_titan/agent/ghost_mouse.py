"""Ghost Mouse — movimento humanizado de cursor com curvas de Bézier.

Implementa o Ghost Protocol do Project Titan:
  • Curvas de Bézier cúbicas (nunca move em linha reta).
  • Injeção de ruído gaussiano (micro-arcos aleatórios).
  • Velocidade variável por dificuldade da decisão (tweening).
  • Coordenadas relativas à janela do emulador → absolutas na tela.
  • Backend via PyAutoGUI para controle real do cursor.
  • **Velocity curve** — ease-in/ease-out (aceleração natural no meio do
    trajeto, desaceleração perto do alvo, como um humano real).
  • **Micro-overshoots** — com probabilidade configurável, o cursor
    ultrapassa o alvo em 5–12px e corrige (movimento humano natural).
  • **Log-normal click hold** — tempo de pressionamento segue uma
    distribuição log-normal (cauda longa → cliques longos ocasionais).
  • **Poisson reaction delay** — tempo de "pensamento" baseado em
    distribuição de Poisson, modulado pela equity/dificuldade.
  • **Idle jitter** — micro-movimentos do mouse entre ações para
    simular mão humana descansando no mouse.

Segurança anti-detecção
------------------------
O movimento do mouse segue uma curva cúbica com 2 pontos de controle
randômicos, ruído gaussiano por waypoint e hold-time variável no clique.
A velocidade (ms/px) varia conforme a distância com perfil natural de
aceleração, impedindo padrões lineares.

Best-in-class click injection (v2)
-----------------------------------
  • **Persistent ADB shell** — keeps a single ``adb shell`` subprocess
    alive and sends commands via stdin.  Eliminates the ~300 ms overhead
    of spawning a new ``adb`` process per click (~10 ms latency).
  • **Raw sendevent fallback** — writes kernel-level multi-touch protocol
    B events directly to ``/dev/input/event2``.  Bypasses Android's
    InputManager entirely for maximum reliability.
  • **Fallback chain** — persistent shell → new subprocess → sendevent.
  • **Thread-safe** — shared shell process guarded by ``threading.Lock``.
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes as wintypes
import math
import os
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from random import gauss, lognormvariate, random, uniform
from typing import Any

from utils.logger import TitanLogger
from tools.mouse_protocol import (  # canonical definitions
    ClickPoint,
    GhostMouseConfig,
    classify_difficulty,
    _DIFFICULTY_EASY,
    _DIFFICULTY_MEDIUM,
    _DIFFICULTY_HARD,
)

try:
    import pyautogui  # type: ignore[import-untyped]

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.0
    _HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None  # type: ignore[assignment]
    _HAS_PYAUTOGUI = False


# ---------------------------------------------------------------------------
# Win32 helpers for LDPlayer window discovery
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32 if os.name == "nt" else None  # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None  # type: ignore[attr-defined]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _find_ldplayer_render_hwnd() -> int | None:
    """Return the HWND of LDPlayer's RenderWindow, or *None*."""
    if _user32 is None:
        return None

    result: list[int] = []

    # noinspection PyUnusedLocal
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        cname = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cname, 256)
        if cname.value == "LDPlayerMainFrame":
            # Search children for RenderWindow
            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def _enum_child(child: int, _lp2: int) -> bool:
                cn2 = ctypes.create_unicode_buffer(256)
                _user32.GetClassNameW(child, cn2, 256)
                if cn2.value == "RenderWindow":
                    result.append(child)
                    return False  # stop
                return True

            _user32.EnumChildWindows(hwnd, _enum_child, 0)
            if result:
                return False  # stop top enumeration
        return True

    _user32.EnumWindows(_enum_top, 0)
    return result[0] if result else None


def _get_render_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (screen_left, screen_top, client_w, client_h) of the render window."""
    pt = _POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    crect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(crect))
    return pt.x, pt.y, crect.right, crect.bottom


# ---------------------------------------------------------------------------
# Persistent ADB shell — best-in-class click injection
# ---------------------------------------------------------------------------

_log_mod = TitanLogger("PersistentADBShell")


class PersistentADBShell:
    """Keep a single ``adb shell`` process alive and pipe commands via stdin.

    **Why this matters**: every ``subprocess.run(["adb", ..., "shell",
    "input", "tap", ...])`` spawns a new process, connects to the ADB
    daemon, opens a shell, runs the command, and tears it all down.
    That takes ~200–400 ms on Windows.  A persistent shell eliminates
    the startup overhead: commands reach the device in ~5–15 ms.

    This is the same approach used by professional automation frameworks
    (OpenSTF, Appium UiAutomator2 bootstrap, scrcpy input injection).

    Thread-safety: all writes are guarded by a ``threading.Lock``.
    """

    def __init__(self, adb_exe: str, device: str) -> None:
        self._adb_exe = adb_exe
        self._device = device
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._alive = False

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> bool:
        """Open the persistent ``adb shell`` subprocess.

        Returns ``True`` on success.  Safe to call multiple times.
        """
        with self._lock:
            if self._alive and self._proc and self._proc.poll() is None:
                return True  # already running
            try:
                self._proc = subprocess.Popen(
                    [self._adb_exe, "-s", self._device, "shell"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,  # unbuffered
                )
                self._alive = True
                _log_mod.info(
                    f"persistent adb shell started "
                    f"pid={self._proc.pid} device={self._device}"
                )
                return True
            except Exception as exc:
                _log_mod.error(f"failed to start persistent shell: {exc}")
                self._alive = False
                return False

    def stop(self) -> None:
        """Terminate the persistent shell (idempotent)."""
        with self._lock:
            self._alive = False
            if self._proc:
                try:
                    self._proc.stdin.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

    @property
    def is_alive(self) -> bool:
        with self._lock:
            if not self._alive or not self._proc:
                return False
            if self._proc.poll() is not None:
                self._alive = False
                return False
            return True

    # ── command execution ───────────────────────────────────────────

    def send(self, cmd: str, *, timeout: float = 5.0) -> bool:
        """Send a shell command (newline-terminated) via stdin.

        Returns ``True`` if the command was written successfully.
        Does NOT wait for output — ``input tap`` produces none.
        """
        with self._lock:
            if not self._alive or not self._proc:
                return False
            if self._proc.poll() is not None:
                self._alive = False
                return False
            try:
                self._proc.stdin.write((cmd.rstrip("\n") + "\n").encode())  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
                return True
            except (BrokenPipeError, OSError) as exc:
                _log_mod.warning(f"persistent shell pipe broken: {exc}")
                self._alive = False
                return False

    def tap(self, x: int, y: int) -> bool:
        """Send ``input touchscreen tap x y`` via the persistent shell."""
        return self.send(f"input touchscreen tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, dur_ms: int) -> bool:
        """Send ``input touchscreen swipe`` via the persistent shell."""
        return self.send(
            f"input touchscreen swipe {x1} {y1} {x2} {y2} {dur_ms}"
        )

    def sendevent_tap(self, x: int, y: int, device: str = "/dev/input/event2") -> bool:
        """Send a raw multi-touch-B tap via ``sendevent`` through the shell.

        This writes directly to the kernel input device, bypassing
        Android's InputManager.  The coordinate transform maps display
        coordinates (portrait 720×1280) → kernel touch axes:
          - ABS_MT_POSITION_X (code 0x35) : range 0–1279 (display Y)
          - ABS_MT_POSITION_Y (code 0x36) : range 0–719  (display X)

        Transform: touch_x = display_y, touch_y = display_x

        Protocol B sequence (slot-based):
          DOWN: SLOT(0) → TRACKING_ID(1) → POS_X → POS_Y → PRESSURE(1)
                → BTN_TOUCH(1) → BTN_TOOL_FINGER(1) → SYN_REPORT
          UP:   SLOT(0) → TRACKING_ID(-1) → BTN_TOUCH(0) → BTN_TOOL_FINGER(0)
                → SYN_REPORT
        """
        # EV_ABS=3, EV_KEY=1, EV_SYN=0
        # ABS_MT_SLOT=0x2f(47), ABS_MT_TRACKING_ID=0x39(57)
        # ABS_MT_POSITION_X=0x35(53), ABS_MT_POSITION_Y=0x36(54)
        # ABS_MT_PRESSURE=0x3a(58)
        # BTN_TOUCH=0x14a(330), BTN_TOOL_FINGER=0x145(325)
        # SYN_REPORT=0

        # Coordinate transform: display (x, y) → touch axes (y, x)
        touch_x = y   # display Y → kernel ABS_MT_POSITION_X (0–1279)
        touch_y = x   # display X → kernel ABS_MT_POSITION_Y (0–719)

        d = device
        down_cmds = (
            f"sendevent {d} 3 47 0;"    # ABS_MT_SLOT = 0
            f"sendevent {d} 3 57 1;"    # ABS_MT_TRACKING_ID = 1
            f"sendevent {d} 3 53 {touch_x};"  # ABS_MT_POSITION_X
            f"sendevent {d} 3 54 {touch_y};"  # ABS_MT_POSITION_Y
            f"sendevent {d} 3 58 1;"    # ABS_MT_PRESSURE = 1
            f"sendevent {d} 1 330 1;"   # BTN_TOUCH = DOWN
            f"sendevent {d} 1 325 1;"   # BTN_TOOL_FINGER = DOWN
            f"sendevent {d} 0 0 0"      # SYN_REPORT
        )
        up_cmds = (
            f"sendevent {d} 3 47 0;"    # ABS_MT_SLOT = 0
            f"sendevent {d} 3 57 -1;"   # ABS_MT_TRACKING_ID = -1 (lift)
            f"sendevent {d} 1 330 0;"   # BTN_TOUCH = UP
            f"sendevent {d} 1 325 0;"   # BTN_TOOL_FINGER = UP
            f"sendevent {d} 0 0 0"      # SYN_REPORT
        )

        ok1 = self.send(down_cmds)
        # Brief hold to simulate finger contact (~30-60ms)
        time.sleep(uniform(0.030, 0.060))
        ok2 = self.send(up_cmds)
        return ok1 and ok2


# Module-level shared instance (lazily initialised by GhostMouse)
_persistent_shell: PersistentADBShell | None = None
_persistent_shell_lock = threading.Lock()


def _get_persistent_shell(adb_exe: str, device: str) -> PersistentADBShell:
    """Get or create the global persistent ADB shell singleton."""
    global _persistent_shell
    with _persistent_shell_lock:
        if _persistent_shell is None or not _persistent_shell.is_alive:
            if _persistent_shell is not None:
                _persistent_shell.stop()
            _persistent_shell = PersistentADBShell(adb_exe, device)
            _persistent_shell.start()
        return _persistent_shell


def _shutdown_persistent_shell() -> None:
    """Clean up the persistent shell on interpreter exit."""
    global _persistent_shell
    if _persistent_shell is not None:
        _persistent_shell.stop()


atexit.register(_shutdown_persistent_shell)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# Re-exported from tools.mouse_protocol for back-compat
# ClickPoint, GhostMouseConfig, classify_difficulty already imported above


@dataclass(slots=True)
class CurvePoint:
    x: float
    y: float


# GhostMouseConfig imported from tools.mouse_protocol (canonical definition)


# ---------------------------------------------------------------------------
# Bézier maths
# ---------------------------------------------------------------------------

def _bezier_point(t: float, p0: CurvePoint, p1: CurvePoint, p2: CurvePoint, p3: CurvePoint) -> CurvePoint:
    """Evaluate a cubic Bézier curve at parameter *t* ∈ [0, 1]."""
    u = 1.0 - t
    u2 = u * u
    t2 = t * t
    coeff0 = u2 * u
    coeff1 = 3.0 * u2 * t
    coeff2 = 3.0 * u * t2
    coeff3 = t2 * t
    return CurvePoint(
        x=coeff0 * p0.x + coeff1 * p1.x + coeff2 * p2.x + coeff3 * p3.x,
        y=coeff0 * p0.y + coeff1 * p1.y + coeff2 * p2.y + coeff3 * p3.y,
    )


def _generate_bezier_path(
    start: CurvePoint,
    end: CurvePoint,
    spread: float = 0.35,
    noise_amp: float = 3.0,
    density: int = 18,
) -> list[CurvePoint]:
    """Return a list of waypoints along a noisy cubic Bézier from *start* to *end*."""
    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.hypot(dx, dy)

    # Number of interpolation steps proportional to distance
    num_steps = max(int(distance / 100.0 * density), 8)

    # Random control points, offset perpendicular to the straight line
    max_offset = max(distance * spread, 20.0)
    cp1 = CurvePoint(
        x=start.x + dx * uniform(0.2, 0.45) + uniform(-max_offset, max_offset),
        y=start.y + dy * uniform(0.2, 0.45) + uniform(-max_offset, max_offset),
    )
    cp2 = CurvePoint(
        x=start.x + dx * uniform(0.55, 0.8) + uniform(-max_offset, max_offset),
        y=start.y + dy * uniform(0.55, 0.8) + uniform(-max_offset, max_offset),
    )

    path: list[CurvePoint] = []
    for i in range(num_steps + 1):
        t = i / num_steps
        pt = _bezier_point(t, start, cp1, cp2, end)
        # Add Gaussian noise (except at exact endpoints)
        if 0 < i < num_steps:
            pt = CurvePoint(
                x=pt.x + gauss(0, noise_amp),
                y=pt.y + gauss(0, noise_amp),
            )
        path.append(pt)

    return path


# ---------------------------------------------------------------------------
# Decision-difficulty classifier (canonical source: tools.mouse_protocol)
# classify_difficulty, _DIFFICULTY_* imported at module top
# ---------------------------------------------------------------------------


def classify_difficulty_by_equity(action: str, street: str = "preflop", equity: float = 0.5) -> str:
    """Enhanced difficulty classification that considers equity.

    Low-equity decisions are inherently harder (player hesitates more).
    Very high equity = easy decision (snap-call/raise).
    Marginal spots (equity ~0.45-0.55) are the hardest.
    """
    base = classify_difficulty(action, street)

    # Marginal equity = harder decision (player tank-thinks)
    if 0.40 <= equity <= 0.55:
        if base == _DIFFICULTY_EASY:
            return _DIFFICULTY_MEDIUM
        return _DIFFICULTY_HARD

    # Very low equity fold = easy (obvious fold)
    if equity < 0.20 and action.strip().lower() == "fold":
        return _DIFFICULTY_EASY

    # Nuts = easy (snap-call)
    if equity > 0.85:
        return _DIFFICULTY_EASY

    return base


# ---------------------------------------------------------------------------
# Velocity curve — ease-in/ease-out
# ---------------------------------------------------------------------------

def _ease_in_out(t: float, strength: float = 2.2) -> float:
    """Sinusoidal ease-in/ease-out curve.

    Maps parameter t ∈ [0, 1] to a new t' that:
    - Starts slow (ease-in at the beginning)
    - Accelerates in the middle
    - Decelerates at the end (ease-out near the target)

    This mimics how humans naturally move a mouse: slow start,
    fast middle, slow approach to the target.

    Args:
        t:        Interpolation parameter [0, 1].
        strength: Exponent controlling how pronounced the easing is.
                  2.0 = gentle, 3.0 = aggressive.
    """
    # Apply smoothstep-like ease using sine
    return 0.5 * (1.0 - math.cos(math.pi * t ** (1.0 / strength)))


# ---------------------------------------------------------------------------
# GhostMouse
# ---------------------------------------------------------------------------

class GhostMouse:
    """Controlador humanizado de mouse com curvas de Bézier e timing variável.

    O GhostMouse converte coordenadas **relativas à janela do emulador**
    em coordenadas absolutas da tela antes de mover o cursor, garantindo
    que cliques sempre caiam dentro da janela correta.

    Ativação
    --------
    O controle real do mouse só acontece quando:
      - ``PyAutoGUI`` está instalado, E
      - ``TITAN_GHOST_MOUSE=1`` está definido.

    Caso contrário, os métodos calculam delays e paths sem mover o cursor
    (modo seguro para CI / testes).
    """

    def __init__(self, config: GhostMouseConfig | None = None) -> None:
        self._log = TitanLogger("GhostMouse")
        self.config = config or GhostMouseConfig()

        # ── Input backend selection ─────────────────────────────────
        # TITAN_INPUT_BACKEND=ldplayer → ADB touchscreen tap via a
        #   *persistent* ADB shell (best-in-class ~10 ms latency).
        #   Fallback: new subprocess per click (~300 ms).
        #   Secondary fallback: raw sendevent to kernel input device.
        # TITAN_INPUT_BACKEND=adb  → same persistent shell for generic ADB.
        # TITAN_INPUT_BACKEND=pyautogui (default) → legacy PyAutoGUI.
        self._input_backend = os.getenv(
            "TITAN_INPUT_BACKEND", "pyautogui"
        ).strip().lower()

        self._enabled = False
        self._adb_exe = ""
        self._adb_device = ""

        # LDPlayer render window handle (auto-discovered)
        self._ld_render_hwnd: int | None = None
        # Android native resolution (720x1280 default for LDPlayer)
        self._ld_android_w: int = 720
        self._ld_android_h: int = 1280

        # Persistent ADB shell (initialised lazily on first click)
        self._persistent_shell: PersistentADBShell | None = None

        # Click statistics for monitoring
        self._click_stats = {
            "persistent_ok": 0,
            "subprocess_fallback": 0,
            "sendevent_fallback": 0,
            "total_failures": 0,
        }

        # ADB settings shared by both ldplayer and adb backends
        self._adb_exe = os.getenv(
            "TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe"
        ).strip()
        self._adb_device = os.getenv(
            "TITAN_ADB_DEVICE", "emulator-5554"
        ).strip()

        if self._input_backend == "ldplayer":
            self._ld_render_hwnd = _find_ldplayer_render_hwnd()
            self._ld_android_w = int(os.getenv("TITAN_ANDROID_W", "720"))
            self._ld_android_h = int(os.getenv("TITAN_ANDROID_H", "1280"))
            self._enabled = os.getenv(
                "TITAN_GHOST_MOUSE", "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            self._log.info(
                f"LDPlayer backend: render_hwnd={self._ld_render_hwnd} "
                f"android={self._ld_android_w}x{self._ld_android_h} "
                f"adb={self._adb_exe} device={self._adb_device} "
                f"enabled={self._enabled}"
            )
        elif self._input_backend == "adb":
            self._enabled = os.getenv(
                "TITAN_GHOST_MOUSE", "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            self._log.info(
                f"ADB backend: exe={self._adb_exe} device={self._adb_device} enabled={self._enabled}"
            )
        else:
            self._enabled = _HAS_PYAUTOGUI and os.getenv(
                "TITAN_GHOST_MOUSE", "0"
            ).strip().lower() in {"1", "true", "yes", "on"}

        # Offset da janela do emulador (definido pelo agente via set_window_offset)
        self._window_left: int = 0
        self._window_top: int = 0

    # -- Lifecycle -----------------------------------------------------------

    def shutdown(self) -> None:
        """Terminate the persistent ADB shell cleanly."""
        if self._persistent_shell is not None:
            self._persistent_shell.stop()
            self._persistent_shell = None
        self._log.info("GhostMouse shutdown complete")

    def get_click_stats(self) -> dict[str, int]:
        """Return a copy of the click method usage statistics."""
        return dict(self._click_stats)

    # -- Configuração da janela do emulador ----------------------------------

    def set_window_offset(self, left: int, top: int) -> None:
        """Define o offset da janela do emulador para conversão de coordenadas.

        Deve ser chamado pelo agente a cada ciclo, após localizar a janela,
        para que ``move_and_click`` converta coords relativas → absolutas.

        Args:
            left: Posição X da janela na tela.
            top:  Posição Y da janela na tela.
        """
        self._window_left = left
        self._window_top = top

    def _to_screen(self, point: ClickPoint) -> ClickPoint:
        """Converte ponto relativo à janela → absoluto na tela."""
        return ClickPoint(
            x=point.x + self._window_left,
            y=point.y + self._window_top,
        )

    # -- API pública ---------------------------------------------------------

    def move_and_click(
        self,
        point: ClickPoint,
        difficulty: str = _DIFFICULTY_EASY,
        relative: bool = True,
        action_name: str = "",
    ) -> float:
        """Move o cursor até *point* via Bézier, clica e retorna o delay de "pensamento" (segundos).

        Args:
            point:      Coordenada do clique. Se ``relative=True`` (padrão),
                        é relativa à janela do emulador.
            difficulty: Nível de dificuldade para calcular o delay humano.
            relative:   Se ``True``, aplica o offset da janela do emulador
                        antes de mover o cursor.

        Returns:
            O delay de "pensamento" em segundos (já aguardado internamente).
        """
        delay = self.thinking_delay(difficulty)
        target = self._to_screen(point) if relative else point
        label = (action_name or "unknown").strip().lower() or "unknown"
        self._log.info(
            f"moving_to action={label} button target=({target.x},{target.y}) "
            f"relative={1 if relative else 0} enabled={1 if self._enabled else 0}"
        )

        if self._enabled:
            if self._input_backend == "ldplayer":
                self._execute_ldplayer_click(point, delay)
            elif self._input_backend == "adb":
                self._execute_adb_tap(point, delay)
            elif pyautogui is not None:
                self._execute_move_and_click(target)

        return delay

    def move_and_click_sequence(
        self,
        points: list[ClickPoint],
        difficulty: str = _DIFFICULTY_EASY,
        relative: bool = True,
        action_name: str = "",
        inter_click_delay: tuple[float, float] = (0.3, 0.7),
    ) -> float:
        """Execute a multi-step click sequence (e.g. open modal → select preset → confirm).

        Each point is clicked in order with a random humanised pause
        between clicks.  The *thinking delay* is applied only before the
        **first** click; subsequent clicks use *inter_click_delay*.

        Args:
            points:            Ordered list of click coordinates.
            difficulty:        Difficulty level for the initial thinking delay.
            relative:          If ``True``, applies emulator window offset.
            action_name:       Label for logging.
            inter_click_delay: ``(min, max)`` seconds between clicks.

        Returns:
            Total delay in seconds (thinking + inter-click pauses).
        """
        if not points:
            return self.thinking_delay(difficulty)

        label = (action_name or "unknown").strip().lower() or "unknown"
        total_delay = self.thinking_delay(difficulty)

        for idx, pt in enumerate(points):
            target = self._to_screen(pt) if relative else pt
            step_label = f"{label}[{idx + 1}/{len(points)}]"
            self._log.info(
                f"sequence step={step_label} target=({target.x},{target.y}) "
                f"relative={1 if relative else 0} enabled={1 if self._enabled else 0}"
            )

            if self._enabled:
                if self._input_backend == "ldplayer":
                    self._execute_ldplayer_click(pt, 0.0)
                elif self._input_backend == "adb":
                    self._execute_adb_tap(pt, 0.0)
                elif pyautogui is not None:
                    self._execute_move_and_click(target)

            # Inter-click pause (skip after last click)
            if idx < len(points) - 1:
                pause = uniform(*inter_click_delay)
                total_delay += pause
                time.sleep(pause)

        return total_delay

    def compute_path(self, start: ClickPoint, end: ClickPoint) -> list[CurvePoint]:
        """Retorna os waypoints Bézier sem executar movimentação (útil para debug/testes)."""
        return _generate_bezier_path(
            CurvePoint(start.x, start.y),
            CurvePoint(end.x, end.y),
            spread=self.config.control_point_spread,
            noise_amp=self.config.noise_amplitude,
            density=self.config.steps_per_100px,
        )

    # -- Helpers internos ----------------------------------------------------

    def thinking_delay(self, difficulty: str) -> float:
        """Retorna um delay baseado em distribuição de Poisson modulada pela dificuldade.

        Se Poisson está desativado, usa distribuição uniforme (legacy).
        O delay Poisson produz uma distribuição mais realista: a maioria
        dos tempos fica perto da média, com caudas longas ocasionais
        (jogador que demora muito pensando em um spot difícil).
        """
        if self.config.poisson_delay_enabled:
            # Use Poisson-inspired delay via exponential distribution
            # (inter-arrival time of a Poisson process)
            if difficulty == _DIFFICULTY_HARD:
                lam = self.config.poisson_lambda_hard
                lo, hi = self.config.timing_hard
            elif difficulty == _DIFFICULTY_MEDIUM:
                lam = self.config.poisson_lambda_medium
                lo, hi = self.config.timing_medium
            else:
                lam = self.config.poisson_lambda_easy
                lo, hi = self.config.timing_easy

            # Exponential variate with clamp to [lo, hi]
            import random as _rnd
            raw = _rnd.expovariate(1.0 / lam)
            # Add small Gaussian jitter for additional naturalism
            raw += gauss(0, lam * 0.1)
            return max(lo, min(raw, hi))

        # Legacy: uniform distribution
        if difficulty == _DIFFICULTY_HARD:
            lo, hi = self.config.timing_hard
        elif difficulty == _DIFFICULTY_MEDIUM:
            lo, hi = self.config.timing_medium
        else:
            lo, hi = self.config.timing_easy
        return uniform(lo, hi)

    def _log_normal_hold_time(self) -> float:
        """Sample a click hold time from a log-normal distribution.

        Log-normal is more realistic than uniform: most clicks are quick
        (~60-80ms) but occasionally a longer hold occurs (~150-200ms),
        mimicking human finger release timing.
        """
        raw = lognormvariate(self.config.click_hold_mu, self.config.click_hold_sigma)
        return max(self.config.click_hold_min, min(raw, self.config.click_hold_max))

    def _execute_move_and_click(self, target: ClickPoint) -> None:
        """Executa movimento Bézier real + clique via PyAutoGUI.

        O cursor percorre a curva interpolada com um perfil de velocidade
        ease-in/ease-out (aceleração natural no meio, desaceleração no
        alvo).  Opcionalmente adiciona micro-overshoots e correções.

        O clique usa duração log-normal ao invés de uniforme para
        mimetizar timing humano de soltar o botão.
        """
        if pyautogui is None:
            raise RuntimeError(
                "PyAutoGUI is required for real mouse control. "
                "Install it or set TITAN_GHOST_MOUSE=0."
            )

        current_x, current_y = pyautogui.position()
        path = _generate_bezier_path(
            CurvePoint(current_x, current_y),
            CurvePoint(target.x, target.y),
            spread=self.config.control_point_spread,
            noise_amp=self.config.noise_amplitude,
            density=self.config.steps_per_100px,
        )

        # Calculate total movement duration
        distance = math.hypot(target.x - current_x, target.y - current_y)
        total_duration = max(distance / 100.0 * self.config.move_duration_per_100px, 0.05)

        n = len(path)
        use_velocity_curve = self.config.velocity_curve_enabled and n > 2

        for i, pt in enumerate(path):
            pyautogui.moveTo(int(pt.x), int(pt.y), _pause=False)

            if use_velocity_curve:
                # Ease-in/ease-out: steps near the middle are faster,
                # steps near start and end are slower.
                t = i / max(n - 1, 1)
                # Compute the derivative of the easing function to get speed
                if i < n - 1:
                    t_next = (i + 1) / max(n - 1, 1)
                    dt_eased = abs(
                        _ease_in_out(t_next, self.config.velocity_ease_strength)
                        - _ease_in_out(t, self.config.velocity_ease_strength)
                    )
                    # Larger dt_eased means faster → shorter pause
                    # Smaller dt_eased means slower → longer pause
                    base_step = total_duration / max(n, 1)
                    # Inverse relationship: slow at edges, fast in middle
                    speed_factor = max(dt_eased * n, 0.3)
                    step_pause = base_step / speed_factor
                    step_pause = max(step_pause, 0.001)  # floor
                else:
                    step_pause = 0.005  # minimal pause at the final point
            else:
                # Legacy: uniform step pauses
                step_pause = total_duration / max(n, 1)

            pyautogui.sleep(step_pause)

        # ── Micro-overshoot ─────────────────────────────────────────
        # With some probability, overshoot past the target then correct.
        # This mimics human hand motor control inaccuracy.
        if random() < self.config.overshoot_probability:
            overshoot_dist = uniform(*self.config.overshoot_distance_px)
            angle = uniform(0, 2 * math.pi)
            overshoot_x = int(target.x + overshoot_dist * math.cos(angle))
            overshoot_y = int(target.y + overshoot_dist * math.sin(angle))
            pyautogui.moveTo(overshoot_x, overshoot_y, _pause=False)

            # Brief pause (human notices overshoot)
            correction_ms = uniform(*self.config.overshoot_correction_ms)
            pyautogui.sleep(correction_ms / 1000.0)

            # Correct back to target (short smooth movement)
            correction_path = _generate_bezier_path(
                CurvePoint(overshoot_x, overshoot_y),
                CurvePoint(target.x, target.y),
                spread=0.15,
                noise_amp=1.5,
                density=10,
            )
            for cpt in correction_path:
                pyautogui.moveTo(int(cpt.x), int(cpt.y), _pause=False)
                pyautogui.sleep(0.003)

        # ── Click with log-normal hold ──────────────────────────────
        jitter = self.config.click_jitter_px
        final_x = int(target.x + gauss(0, jitter))
        final_y = int(target.y + gauss(0, jitter))
        pyautogui.moveTo(final_x, final_y, _pause=False)
        hold = self._log_normal_hold_time()
        pyautogui.mouseDown(_pause=False)
        pyautogui.sleep(hold)
        pyautogui.mouseUp(_pause=False)

    def _execute_adb_tap(self, point: ClickPoint, pre_delay: float = 0.0) -> None:
        """Send a tap via ADB ``shell input tap`` (persistent shell first).

        The *pre_delay* (thinking time) is applied before the tap to
        maintain humanised timing even though ADB skips the Bézier path.
        Small random jitter is added to the coordinates.
        """
        if pre_delay > 0:
            time.sleep(pre_delay)

        jitter = self.config.click_jitter_px
        tx = int(point.x + gauss(0, jitter))
        ty = int(point.y + gauss(0, jitter))

        # Try persistent shell first
        shell = self._ensure_persistent_shell()
        if shell.send(f"input tap {tx} {ty}"):
            self._log.info(f"adb tap ({tx},{ty}) via persistent shell")
            return

        # Fallback to subprocess
        try:
            subprocess.run(
                [self._adb_exe, "-s", self._adb_device,
                 "shell", "input", "tap", str(tx), str(ty)],
                timeout=5,
                capture_output=True,
            )
            self._log.info(f"adb tap ({tx},{ty}) via subprocess fallback")
        except Exception as exc:
            self._log.error(f"ADB tap failed: {exc}")

    @staticmethod
    def _force_foreground(hwnd: int) -> None:
        """Force *hwnd* to the foreground using AttachThreadInput trick.

        Plain ``SetForegroundWindow`` fails when the calling process is
        not the current foreground process (Windows UIPI restriction).
        We attach our thread to the foreground thread first to gain the
        right, then call ``BringWindowToTop`` + ``SetForegroundWindow``.
        If that still fails, we minimize/restore the window as a fallback.
        """
        if _user32 is None:
            return
        _kernel32 = ctypes.windll.kernel32
        fg_hwnd = _user32.GetForegroundWindow()
        fg_thread = _user32.GetWindowThreadProcessId(fg_hwnd, None)
        my_thread = _kernel32.GetCurrentThreadId()
        attached = False
        if fg_thread != my_thread:
            attached = bool(_user32.AttachThreadInput(my_thread, fg_thread, True))
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        if attached:
            _user32.AttachThreadInput(my_thread, fg_thread, False)
        time.sleep(0.20)
        # Fallback: minimize/restore if focus didn't take
        if _user32.GetForegroundWindow() != hwnd:
            _user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
            time.sleep(0.15)
            _user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            time.sleep(0.25)

    def _ensure_persistent_shell(self) -> PersistentADBShell:
        """Lazily initialise (or restart) the persistent ADB shell."""
        if self._persistent_shell is None or not self._persistent_shell.is_alive:
            self._persistent_shell = _get_persistent_shell(
                self._adb_exe, self._adb_device
            )
        return self._persistent_shell

    def _execute_ldplayer_click(self, point: ClickPoint, pre_delay: float = 0.0) -> None:
        """Click inside LDPlayer via best-in-class fallback chain.

        Fallback order (fastest → most reliable):
          1. **Persistent ADB shell** — ``input touchscreen tap`` piped
             via stdin to a long-lived ``adb shell`` process (~10 ms).
          2. **New subprocess** — classic ``subprocess.run()`` (~300 ms).
          3. **Raw sendevent** — kernel-level multi-touch events written
             to ``/dev/input/event2`` via the persistent shell.

        At native 720×1280 resolution (without ``wm size`` override),
        ``input touchscreen tap`` successfully reaches PPPoker's Unity
        engine.  The ``wm size`` override to 1080×1920 **must not** be
        active: it breaks both physical clicks and reverses which ADB
        input source Unity accepts.

        Coordinates are in Android native resolution (720×1280).
        """
        if pre_delay > 0:
            time.sleep(pre_delay)

        jitter = self.config.click_jitter_px
        tx = int(point.x + gauss(0, jitter))
        ty = int(point.y + gauss(0, jitter))

        # ── Strategy 1: persistent shell (fastest, ~10 ms) ──────────
        shell = self._ensure_persistent_shell()
        if shell.tap(tx, ty):
            self._click_stats["persistent_ok"] += 1
            self._log.info(
                f"ldplayer click ({tx},{ty}) via persistent shell "
                f"[stats: ok={self._click_stats['persistent_ok']}]"
            )
            return

        # ── Strategy 2: new subprocess fallback (~300 ms) ───────────
        self._log.warning(
            f"persistent shell unavailable — falling back to subprocess"
        )
        try:
            subprocess.run(
                [self._adb_exe, "-s", self._adb_device,
                 "shell", "input", "touchscreen", "tap", str(tx), str(ty)],
                timeout=5,
                capture_output=True,
            )
            self._click_stats["subprocess_fallback"] += 1
            self._log.info(
                f"ldplayer click ({tx},{ty}) via subprocess fallback"
            )
            return
        except Exception as exc:
            self._log.warning(f"subprocess tap also failed: {exc}")

        # ── Strategy 3: raw sendevent (kernel bypass) ───────────────
        self._log.warning("trying sendevent fallback (kernel bypass)")
        shell = self._ensure_persistent_shell()
        if shell.sendevent_tap(tx, ty):
            self._click_stats["sendevent_fallback"] += 1
            self._log.info(
                f"ldplayer click ({tx},{ty}) via sendevent fallback"
            )
            return

        # ── All strategies exhausted ────────────────────────────────
        self._click_stats["total_failures"] += 1
        self._log.error(
            f"ALL click strategies failed at ({tx},{ty}) "
            f"[failures={self._click_stats['total_failures']}]"
        )

    def swipe(
        self,
        start: ClickPoint,
        end: ClickPoint,
        duration: float = 0.4,
        action_name: str = "",
    ) -> float:
        """Execute a swipe (drag) gesture from *start* to *end*.

        On the ``ldplayer`` backend this performs a real mouse drag on the
        Win32 render surface — essential for PPPoker's raise slider.

        On the ``adb`` backend it uses ``adb shell input swipe``.

        On the ``pyautogui`` backend it uses a Bézier-smoothed drag.

        Coordinates are in **Android virtual resolution** (e.g. 1080×1920).

        Args:
            start:       Start coordinate of the swipe.
            end:         End coordinate of the swipe.
            duration:    Total duration of the swipe in seconds.
            action_name: Label for logging.

        Returns:
            The swipe duration in seconds.
        """
        label = (action_name or "swipe").strip().lower()
        self._log.info(
            f"swipe action={label} from=({start.x},{start.y}) "
            f"to=({end.x},{end.y}) duration={duration:.2f}s "
            f"enabled={1 if self._enabled else 0}"
        )

        if not self._enabled:
            return duration

        if self._input_backend == "ldplayer":
            self._execute_ldplayer_swipe(start, end, duration)
        elif self._input_backend == "adb":
            self._execute_adb_swipe(start, end, duration)
        elif pyautogui is not None:
            self._execute_pyautogui_swipe(start, end, duration)

        return duration

    def _execute_ldplayer_swipe(
        self,
        start: ClickPoint,
        end: ClickPoint,
        duration: float,
    ) -> None:
        """Swipe inside LDPlayer via persistent shell or subprocess fallback.

        Uses the same persistent shell as ``_execute_ldplayer_click``
        for ~10 ms latency on the command dispatch.  Falls back to
        ``subprocess.run`` if the shell is unavailable.
        """
        jitter = self.config.click_jitter_px
        sx1 = int(start.x + gauss(0, jitter))
        sy1 = int(start.y + gauss(0, jitter))
        sx2 = int(end.x + gauss(0, jitter))
        sy2 = int(end.y + gauss(0, jitter))
        dur_ms = int(duration * 1000)

        # Try persistent shell first
        shell = self._ensure_persistent_shell()
        if shell.swipe(sx1, sy1, sx2, sy2, dur_ms):
            self._log.info(
                f"ldplayer swipe ({sx1},{sy1})→({sx2},{sy2}) "
                f"dur={dur_ms}ms via persistent shell"
            )
            return

        # Fallback to subprocess
        try:
            subprocess.run(
                [self._adb_exe, "-s", self._adb_device,
                 "shell", "input", "touchscreen", "swipe",
                 str(sx1), str(sy1), str(sx2), str(sy2), str(dur_ms)],
                timeout=max(10, int(duration) + 5),
                capture_output=True,
            )
            self._log.info(
                f"ldplayer swipe ({sx1},{sy1})→({sx2},{sy2}) "
                f"dur={dur_ms}ms via subprocess fallback"
            )
        except Exception as exc:
            self._log.error(f"LDPlayer swipe failed: {exc}")

    def _execute_adb_swipe(
        self,
        start: ClickPoint,
        end: ClickPoint,
        duration: float,
    ) -> None:
        """Execute a swipe via persistent ADB shell or subprocess fallback."""
        dur_ms = int(duration * 1000)

        # Try persistent shell first
        shell = self._ensure_persistent_shell()
        cmd = (
            f"input swipe {int(start.x)} {int(start.y)} "
            f"{int(end.x)} {int(end.y)} {dur_ms}"
        )
        if shell.send(cmd):
            self._log.info(f"adb swipe via persistent shell")
            return

        # Fallback
        try:
            subprocess.run(
                [
                    self._adb_exe, "-s", self._adb_device,
                    "shell", "input", "swipe",
                    str(int(start.x)), str(int(start.y)),
                    str(int(end.x)), str(int(end.y)),
                    str(dur_ms),
                ],
                timeout=10,
                capture_output=True,
            )
        except Exception as exc:
            self._log.error(f"ADB swipe failed: {exc}")

    def _execute_pyautogui_swipe(
        self,
        start: ClickPoint,
        end: ClickPoint,
        duration: float,
    ) -> None:
        """Execute a swipe via pyautogui drag with Bézier smoothing."""
        if pyautogui is None:
            return

        s = self._to_screen(start)
        e = self._to_screen(end)

        path = _generate_bezier_path(
            CurvePoint(s.x, s.y),
            CurvePoint(e.x, e.y),
            spread=0.05,
            noise_amp=1.0,
            density=12,
        )

        pyautogui.moveTo(s.x, s.y, _pause=False)
        time.sleep(0.05)
        pyautogui.mouseDown(_pause=False)
        time.sleep(0.05)

        n = len(path)
        step_delay = duration / max(n, 1)
        for pt in path:
            pyautogui.moveTo(int(pt.x), int(pt.y), _pause=False)
            time.sleep(step_delay)

        pyautogui.mouseUp(_pause=False)

    def take_screenshot(self) -> bytes | None:
        """Capture a screenshot from the Android device via ADB.

        Returns the raw PNG bytes, or ``None`` on failure.  This works
        even on PPPoker because ``screencap`` uses the framebuffer (not
        input injection), so Unity's anti-automation does not block it.
        """
        adb = self._adb_exe or os.getenv(
            "TITAN_ADB_PATH", r"F:\LDPlayer\LDPlayer9\adb.exe"
        )
        device = self._adb_device or os.getenv(
            "TITAN_ADB_DEVICE", "127.0.0.1:5555"
        )

        try:
            result = subprocess.run(
                [adb, "-s", device, "exec-out", "screencap", "-p"],
                timeout=10,
                capture_output=True,
            )
            if result.returncode == 0 and len(result.stdout) > 100:
                return result.stdout
        except Exception as exc:
            self._log.error(f"Screenshot failed: {exc}")

        return None

    def idle_jitter(self) -> None:
        """Perform a tiny random mouse movement to simulate a resting hand.

        Should be called between actions (during waiting periods) to
        prevent the cursor from being perfectly still for long periods,
        which is a bot-detection signal.
        """
        if not self._enabled:
            return
        if self._input_backend in ("adb", "ldplayer"):
            return  # ADB/LDPlayer don't move a cursor — jitter is not applicable
        if not self.config.idle_jitter_enabled:
            return

        amp = self.config.idle_jitter_amplitude_px
        current_x, current_y = pyautogui.position()
        dx = gauss(0, amp)
        dy = gauss(0, amp)
        new_x = int(current_x + dx)
        new_y = int(current_y + dy)

        # Very slow, gentle drift (not a snap)
        pyautogui.moveTo(new_x, new_y, duration=uniform(0.1, 0.3), _pause=False)
