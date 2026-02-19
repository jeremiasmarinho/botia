"""Ghost Mouse — movimento humanizado de cursor com curvas de Bézier.

Implementa o Ghost Protocol do Project Titan:
  • Curvas de Bézier cúbicas (nunca move em linha reta).
  • Injeção de ruído gaussiano (micro-arcos aleatórios).
  • Velocidade variável por dificuldade da decisão (tweening).
  • Coordenadas relativas à janela do emulador → absolutas na tela.
  • Backend via PyAutoGUI para controle real do cursor.

Segurança anti-detecção
------------------------
O movimento do mouse segue uma curva cúbica com 2 pontos de controle
randômicos, ruído gaussiano por waypoint e hold-time variável no clique.
A velocidade (ms/px) varia conforme a distância, impedindo padrões lineares.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from random import gauss, uniform
from typing import Any

from utils.logger import TitanLogger

try:
    import pyautogui  # type: ignore[import-untyped]

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.0
    _HAS_PYAUTOGUI = True
except Exception:
    pyautogui = None  # type: ignore[assignment]
    _HAS_PYAUTOGUI = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClickPoint:
    x: int
    y: int


@dataclass(slots=True)
class CurvePoint:
    x: float
    y: float


@dataclass(slots=True)
class GhostMouseConfig:
    """Tunables for humanised movement."""

    # Bézier generation
    control_point_spread: float = 0.35  # max % of distance for control-point offset
    noise_amplitude: float = 3.0  # px noise added to each interpolated point
    steps_per_100px: int = 18  # interpolation density

    # Timing (seconds) by decision difficulty
    timing_easy: tuple[float, float] = (0.8, 1.5)
    timing_medium: tuple[float, float] = (2.0, 4.0)
    timing_hard: tuple[float, float] = (4.0, 12.0)

    # Click parameters
    click_hold_min: float = 0.04
    click_hold_max: float = 0.12
    click_jitter_px: float = 2.0  # max random offset in px applied to final click position

    # Movement duration (seconds per 100px distance)
    move_duration_per_100px: float = 0.06


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
# Decision-difficulty classifier
# ---------------------------------------------------------------------------

_DIFFICULTY_EASY = "easy"
_DIFFICULTY_MEDIUM = "medium"
_DIFFICULTY_HARD = "hard"


def classify_difficulty(action: str, street: str = "preflop") -> str:
    """Infer decision difficulty from the chosen action and street.

    Spec references:
        Easy  (preflop fold): 0.8 – 1.5 s
        Hard  (river bluff):  4.0 – 12.0 s
    """
    action_lower = action.strip().lower()

    if action_lower == "fold" and street == "preflop":
        return _DIFFICULTY_EASY

    if action_lower in {"raise_big", "raise_small", "raise_pot"} and street in {"turn", "river"}:
        return _DIFFICULTY_HARD

    if action_lower == "fold" and street in {"turn", "river"}:
        return _DIFFICULTY_MEDIUM

    if action_lower in {"raise_big", "raise_small", "raise_pot", "raise_2x"}:
        return _DIFFICULTY_MEDIUM

    # call anywhere, fold on flop, etc.
    return _DIFFICULTY_EASY


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
        self._enabled = _HAS_PYAUTOGUI and os.getenv(
            "TITAN_GHOST_MOUSE", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}

        # Offset da janela do emulador (definido pelo agente via set_window_offset)
        self._window_left: int = 0
        self._window_top: int = 0

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
        delay = self._thinking_delay(difficulty)
        target = self._to_screen(point) if relative else point
        label = (action_name or "unknown").strip().lower() or "unknown"
        self._log.info(
            f"moving_to action={label} button target=({target.x},{target.y}) "
            f"relative={1 if relative else 0} enabled={1 if self._enabled else 0}"
        )

        if self._enabled and pyautogui is not None:
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
            return self._thinking_delay(difficulty)

        label = (action_name or "unknown").strip().lower() or "unknown"
        total_delay = self._thinking_delay(difficulty)

        for idx, pt in enumerate(points):
            target = self._to_screen(pt) if relative else pt
            step_label = f"{label}[{idx + 1}/{len(points)}]"
            self._log.info(
                f"sequence step={step_label} target=({target.x},{target.y}) "
                f"relative={1 if relative else 0} enabled={1 if self._enabled else 0}"
            )

            if self._enabled and pyautogui is not None:
                self._execute_move_and_click(target)

            # Inter-click pause (skip after last click)
            if idx < len(points) - 1:
                pause = uniform(*inter_click_delay)
                total_delay += pause
                if self._enabled and pyautogui is not None:
                    pyautogui.sleep(pause)

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

    def _thinking_delay(self, difficulty: str) -> float:
        """Retorna um delay aleatório proporcional à dificuldade da decisão."""
        if difficulty == _DIFFICULTY_HARD:
            lo, hi = self.config.timing_hard
        elif difficulty == _DIFFICULTY_MEDIUM:
            lo, hi = self.config.timing_medium
        else:
            lo, hi = self.config.timing_easy
        return uniform(lo, hi)

    def _execute_move_and_click(self, target: ClickPoint) -> None:
        """Executa movimento Bézier real + clique via PyAutoGUI.

        O cursor percorre a curva interpolada com pausa proporcional
        à distância, depois segura o clique por um tempo aleatório
        para simular comportamento humano.
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
        step_pause = total_duration / max(len(path), 1)

        for pt in path:
            pyautogui.moveTo(int(pt.x), int(pt.y), _pause=False)
            pyautogui.sleep(step_pause)

        # Hold click for a human-like duration with small positional jitter
        jitter = self.config.click_jitter_px
        final_x = int(target.x + gauss(0, jitter))
        final_y = int(target.y + gauss(0, jitter))
        pyautogui.moveTo(final_x, final_y, _pause=False)
        hold = uniform(self.config.click_hold_min, self.config.click_hold_max)
        pyautogui.click(_pause=False)
        pyautogui.sleep(hold)
