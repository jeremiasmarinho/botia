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
"""

from __future__ import annotations

import math
import os
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
        delay = self.thinking_delay(difficulty)
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

    def idle_jitter(self) -> None:
        """Perform a tiny random mouse movement to simulate a resting hand.

        Should be called between actions (during waiting periods) to
        prevent the cursor from being perfectly still for long periods,
        which is a bot-detection signal.
        """
        if not self._enabled or pyautogui is None:
            return
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
