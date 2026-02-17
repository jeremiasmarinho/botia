"""Visão do Exterminador — janela de debug em tempo real.

Mostra exatamente o que o bot está vendo: bounding boxes coloridas nas
cartas, botões de ação destacados, pot/stack/call lidos pelo OCR e a
decisão tomada — tudo numa janela OpenCV flutuante que atualiza em tempo
real enquanto o agente roda.

Arquitetura
-----------
::

    PokerAgent.run()
        │
        ├── overlay.update_frame(frame)        ← frame capturado
        ├── overlay.update_detections(dets)     ← resultado YOLO
        ├── overlay.update_snapshot(snap)        ← TableSnapshot
        └── overlay.update_decision(action, …)  ← decisão tomada
                │
                ▼
        Thread de renderização (daemon)
            └── cv2.imshow  atualiza a cada ~100ms

Uso::

    from tools.terminator_vision import TerminatorVision

    overlay = TerminatorVision()     # cria mas não bloqueia
    overlay.start()                  # inicia thread de exibição

    # No loop do agente:
    overlay.update_frame(frame)
    overlay.update_snapshot(snapshot)
    overlay.update_decision("raise_small", cycle_ms=147.2, equity=0.72)

    overlay.stop()                   # encerra janela

Variáveis de ambiente
---------------------
``TITAN_OVERLAY_ENABLED``    ``1`` para ativar (default ``0``).
``TITAN_OVERLAY_MAX_FPS``    FPS máximo da janela (default ``10``).
``TITAN_OVERLAY_HUD_WIDTH``  Largura do painel HUD (default ``320``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from utils.logger import TitanLogger

try:
    import cv2  # type: ignore[import-untyped]
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

_log = TitanLogger("Overlay")


# ═══════════════════════════════════════════════════════════════════════════
# Paleta de cores (BGR para OpenCV)
# ═══════════════════════════════════════════════════════════════════════════

_CARD_COLORS: dict[str, tuple[int, int, int]] = {
    "hero":    (0, 255, 0),      # verde
    "board":   (255, 200, 0),    # ciano/dourado
    "dead":    (128, 128, 128),  # cinza
}

_ACTION_COLORS: dict[str, tuple[int, int, int]] = {
    "fold":        (0, 0, 220),      # vermelho
    "call":        (0, 220, 0),      # verde
    "raise_small": (0, 180, 255),    # laranja
    "raise_big":   (0, 100, 255),    # laranja escuro
    "wait":        (180, 180, 180),  # cinza
}

_BUTTON_COLOR: tuple[int, int, int] = (255, 100, 50)   # laranja
_POT_COLOR: tuple[int, int, int] = (0, 200, 255)       # amarelado
_HUD_BG: tuple[int, int, int] = (25, 25, 25)


# ═══════════════════════════════════════════════════════════════════════════
# Dataclass de estado compartilhado entre threads
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _OverlayState:
    """Estado atualizado pelo agente, consumido pela thread de render."""

    frame: Any = None

    hero_cards: list[str] = field(default_factory=list)
    board_cards: list[str] = field(default_factory=list)
    dead_cards: list[str] = field(default_factory=list)
    pot: float = 0.0
    stack: float = 0.0
    call_amount: float = 0.0
    active_players: int = 0
    is_my_turn: bool = False

    action: str = "wait"
    cycle_id: int = 0
    cycle_ms: float = 0.0
    equity: float = 0.0
    spr: float = 0.0
    street: str = "preflop"

    # Bounding boxes brutas do YOLO [{label, confidence, cx, cy, w, h}]
    detections: list[dict[str, Any]] = field(default_factory=list)

    # Pontos de ação calibrados {nome: (x, y)}
    action_points: dict[str, tuple[int, int]] = field(default_factory=dict)

    # OCR regions {nome: (x, y, w, h)}
    ocr_regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Classificação de labels YOLO → categoria visual
# ═══════════════════════════════════════════════════════════════════════════

def _classify_label(label: str) -> str:
    """Classifica um label YOLO em categoria visual."""
    low = label.lower()
    if low.startswith(("hero_", "hole_", "hand_", "h1_", "h2_")):
        return "hero"
    if low.startswith(("board_", "flop_", "turn_", "river_", "b1_", "b2_", "b3_", "b4_", "b5_")):
        return "board"
    if low.startswith(("dead_", "burn_", "muck_", "folded_")):
        return "dead"
    if low.startswith(("btn_", "action_", "button_")):
        return "button"
    if low.startswith("pot"):
        return "pot"
    if low.startswith(("stack", "hero_stack")):
        return "stack"

    # Duas letras = carta avulsa (ex: "Ah", "Kd")
    if len(label) == 2 and label[0] in "23456789TJQKA" and label[1] in "cdhs":
        return "hero"  # default para carta genérica
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# TerminatorVision — classe principal
# ═══════════════════════════════════════════════════════════════════════════

class TerminatorVision:
    """Janela de debug em tempo real — 'Visão do Exterminador'.

    Renderiza o frame capturado com bounding boxes, labels, regiões OCR
    e um HUD lateral com todas as informações de estado e decisão do bot.

    Args:
        max_fps:    FPS máximo de atualização da janela (default 10).
        hud_width:  Largura do painel HUD lateral em pixels (default 320).
        window_name: Título da janela OpenCV.
    """

    def __init__(
        self,
        max_fps: int = 10,
        hud_width: int = 320,
        show_grid: bool = False,
        grid_size: int = 50,
        window_name: str = "TITAN: Visao do Exterminador",
    ) -> None:
        self._max_fps = max(1, max_fps)
        self._hud_width = max(100, hud_width)
        self._show_grid = bool(show_grid)
        self._grid_size = max(10, int(grid_size))
        self._window_name = window_name
        self._state = _OverlayState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ── API pública: updates do agente ────────────────────────────

    def update_frame(self, frame: Any) -> None:
        """Atualiza o frame bruto capturado pelo VisionYolo."""
        if frame is None:
            return
        with self._lock:
            self._state.frame = frame.copy() if hasattr(frame, "copy") else frame

    def update_detections(self, detections: list[dict[str, Any]]) -> None:
        """Atualiza as detecções YOLO brutas.

        Cada detecção é um dict com: label, confidence, cx, cy, w, h.
        """
        with self._lock:
            self._state.detections = list(detections)

    def update_snapshot(self, snapshot: Any) -> None:
        """Atualiza o estado da mesa a partir de um TableSnapshot."""
        with self._lock:
            self._state.hero_cards = list(getattr(snapshot, "hero_cards", []))
            self._state.board_cards = list(getattr(snapshot, "board_cards", []))
            self._state.dead_cards = list(getattr(snapshot, "dead_cards", []))
            self._state.pot = float(getattr(snapshot, "pot", 0.0))
            self._state.stack = float(getattr(snapshot, "stack", 0.0))
            self._state.call_amount = float(getattr(snapshot, "call_amount", 0.0))
            self._state.active_players = int(getattr(snapshot, "active_players", 0))
            self._state.is_my_turn = bool(getattr(snapshot, "is_my_turn", False))
            self._state.action_points = dict(getattr(snapshot, "action_points", {}))

    def update_decision(
        self,
        action: str,
        *,
        cycle_id: int = 0,
        cycle_ms: float = 0.0,
        equity: float = 0.0,
        spr: float = 0.0,
        street: str = "preflop",
    ) -> None:
        """Atualiza a decisão tomada pelo bot neste ciclo."""
        with self._lock:
            self._state.action = action
            self._state.cycle_id = cycle_id
            self._state.cycle_ms = cycle_ms
            self._state.equity = equity
            self._state.spr = spr
            self._state.street = street

    def update_ocr_regions(self, regions: dict[str, tuple[int, int, int, int]]) -> None:
        """Atualiza as regiões de OCR para visualização."""
        with self._lock:
            self._state.ocr_regions = dict(regions)

    # ── Controle de ciclo de vida ─────────────────────────────────

    def start(self) -> None:
        """Inicia a thread de renderização em background."""
        if cv2 is None or np is None:
            _log.warn("OpenCV/numpy nao disponivel — overlay desativado")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._render_loop,
            daemon=True,
            name="TerminatorVision",
        )
        self._thread.start()
        _log.success("Visao do Exterminador ATIVADA")

    def stop(self) -> None:
        """Para a thread e fecha a janela."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Loop de renderização (roda na thread daemon) ──────────────

    def _render_loop(self) -> None:
        """Loop principal de renderização OpenCV."""
        frame_interval = 1.0 / self._max_fps

        while self._running:
            t_start = time.perf_counter()

            with self._lock:
                state = _OverlayState(
                    frame=self._state.frame.copy() if self._state.frame is not None and hasattr(self._state.frame, "copy") else self._state.frame,
                    hero_cards=list(self._state.hero_cards),
                    board_cards=list(self._state.board_cards),
                    dead_cards=list(self._state.dead_cards),
                    pot=self._state.pot,
                    stack=self._state.stack,
                    call_amount=self._state.call_amount,
                    active_players=self._state.active_players,
                    is_my_turn=self._state.is_my_turn,
                    action=self._state.action,
                    cycle_id=self._state.cycle_id,
                    cycle_ms=self._state.cycle_ms,
                    equity=self._state.equity,
                    spr=self._state.spr,
                    street=self._state.street,
                    detections=list(self._state.detections),
                    action_points=dict(self._state.action_points),
                    ocr_regions=dict(self._state.ocr_regions),
                )

            canvas = self._render_frame(state)
            if canvas is not None:
                try:
                    cv2.imshow(self._window_name, canvas)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        _log.info("Overlay encerrado pelo usuario (Q)")
                        self._running = False
                        break
                except Exception:
                    self._running = False
                    break

            elapsed = time.perf_counter() - t_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        try:
            cv2.destroyWindow(self._window_name)
        except Exception:
            pass

    # ── Renderização do frame completo ────────────────────────────

    def _render_frame(self, state: _OverlayState) -> Any:
        """Compõe o frame anotado + HUD lateral."""
        if state.frame is None:
            # Gera placeholder escuro
            placeholder = np.zeros((600, 800 + self._hud_width, 3), dtype=np.uint8)
            self._draw_placeholder(placeholder)
            return placeholder

        frame = state.frame.copy()
        h, w = frame.shape[:2]

        # 1. Desenha detecções YOLO (bounding boxes)
        self._draw_detections(frame, state.detections)

        # 2. Desenha pontos de ação calibrados
        self._draw_action_points(frame, state.action_points, state.action)

        # 3. Desenha regiões de OCR
        self._draw_ocr_regions(frame, state.ocr_regions)

        # 4. Barra de status no topo do frame
        self._draw_status_bar(frame, state)

        # 4.1 Grid opcional para calibração manual de coordenadas
        if self._show_grid:
            self._draw_grid(frame)

        # 5. Compõe HUD lateral
        canvas = np.zeros((h, w + self._hud_width, 3), dtype=np.uint8)
        canvas[:, :w] = frame
        canvas[:, w:] = _HUD_BG
        self._draw_hud(canvas, w, h, state)

        return canvas

    # ── Desenho das detecções YOLO ────────────────────────────────

    def _draw_detections(self, frame: Any, detections: list[dict[str, Any]]) -> None:
        """Desenha bounding boxes coloridas sobre as detecções YOLO."""
        for det in detections:
            label = str(det.get("label", ""))
            conf = float(det.get("confidence", 0.0))
            cx = int(det.get("cx", 0))
            cy = int(det.get("cy", 0))
            w = int(det.get("w", 40))
            h = int(det.get("h", 40))

            x1 = cx - w // 2
            y1 = cy - h // 2
            x2 = cx + w // 2
            y2 = cy + h // 2

            category = _classify_label(label)
            if category in _CARD_COLORS:
                color = _CARD_COLORS[category]
            elif category == "button":
                color = _BUTTON_COLOR
            elif category in ("pot", "stack"):
                color = _POT_COLOR
            else:
                color = (200, 200, 200)

            # Retângulo semi-transparente
            overlay = frame.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)

            # Borda sólida
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Label + confiança
            text = f"{label} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            ly = max(y1 - 4, th + 4)
            cv2.rectangle(frame, (x1, ly - th - 3), (x1 + tw + 4, ly + 2), color, -1)
            cv2.putText(frame, text, (x1 + 2, ly - 1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)

    # ── Desenho dos pontos de ação ────────────────────────────────

    def _draw_action_points(
        self,
        frame: Any,
        action_points: dict[str, tuple[int, int]],
        current_action: str,
    ) -> None:
        """Desenha marcadores nos botões de ação calibrados."""
        for name, point in action_points.items():
            if not isinstance(point, (tuple, list)) or len(point) != 2:
                continue
            x, y = int(point[0]), int(point[1])

            is_active = name.lower() == current_action.lower()
            color = _ACTION_COLORS.get(name.lower(), (200, 200, 200))
            radius = 14 if is_active else 8
            thickness = -1 if is_active else 2

            cv2.circle(frame, (x, y), radius, color, thickness)
            cv2.putText(frame, name.upper(), (x - 20, y - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)

    # ── Desenho das regiões de OCR ────────────────────────────────

    def _draw_ocr_regions(
        self,
        frame: Any,
        ocr_regions: dict[str, tuple[int, int, int, int]],
    ) -> None:
        """Desenha retângulos tracejados nas regiões de OCR."""
        label_colors = {
            "pot": (0, 200, 255),
            "hero_stack": (255, 200, 0),
            "call_amount": (0, 255, 200),
        }
        for name, region in ocr_regions.items():
            if len(region) != 4:
                continue
            x, y, w, h = region
            color = label_colors.get(name, (180, 180, 180))

            # Borda tracejada (simula com linhas curtas)
            for i in range(0, w, 8):
                cv2.line(frame, (x + i, y), (x + min(i + 4, w), y), color, 1)
                cv2.line(frame, (x + i, y + h), (x + min(i + 4, w), y + h), color, 1)
            for i in range(0, h, 8):
                cv2.line(frame, (x, y + i), (x, y + min(i + 4, h)), color, 1)
                cv2.line(frame, (x + w, y + i), (x + w, y + min(i + 4, h)), color, 1)

            cv2.putText(frame, name, (x, y - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1, cv2.LINE_AA)

    # ── Barra de status no topo ───────────────────────────────────

    def _draw_status_bar(self, frame: Any, state: _OverlayState) -> None:
        """Barra semi-transparente no topo com resumo rápido."""
        h, w = frame.shape[:2]
        bar_h = 28

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, bar_h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        action_color = _ACTION_COLORS.get(state.action.lower(), (200, 200, 200))
        text = (
            f"TITAN  |  Cycle {state.cycle_id}  |  "
            f"{state.action.upper()}  |  "
            f"Equity {state.equity:.0%}  |  "
            f"Pot {state.pot:.0f}  Stack {state.stack:.0f}  |  "
            f"{state.cycle_ms:.0f}ms"
        )
        cv2.putText(frame, text, (8, 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, action_color, 1, cv2.LINE_AA)

    def _draw_grid(self, frame: Any) -> None:
        """Desenha grade de pixels para facilitar calibração X/Y/W/H."""
        h, w = frame.shape[:2]
        step = self._grid_size
        minor_color = (70, 70, 70)
        major_color = (120, 120, 120)
        text_color = (170, 170, 170)

        for x in range(0, w, step):
            color = major_color if (x // step) % 2 == 0 else minor_color
            cv2.line(frame, (x, 0), (x, h), color, 1)
            if x % (step * 2) == 0:
                cv2.putText(
                    frame,
                    str(x),
                    (x + 2, min(h - 6, 16)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    text_color,
                    1,
                    cv2.LINE_AA,
                )

        for y in range(0, h, step):
            color = major_color if (y // step) % 2 == 0 else minor_color
            cv2.line(frame, (0, y), (w, y), color, 1)
            if y % (step * 2) == 0:
                cv2.putText(
                    frame,
                    str(y),
                    (4, max(12, y - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    text_color,
                    1,
                    cv2.LINE_AA,
                )

    # ── HUD lateral ───────────────────────────────────────────────

    def _draw_hud(
        self,
        canvas: Any,
        frame_w: int,
        frame_h: int,
        state: _OverlayState,
    ) -> None:
        """Desenha o painel HUD informativo no lado direito."""
        hud_x = frame_w + 10
        y = 30
        dh = 22  # espaçamento entre linhas
        font = cv2.FONT_HERSHEY_SIMPLEX
        fs = 0.42

        white = (255, 255, 255)
        green = (0, 255, 0)
        yellow = (0, 220, 255)
        red = (0, 80, 255)
        gray = (160, 160, 160)
        cyan = (255, 255, 0)

        def put(text: str, color: tuple[int, int, int] = white, bold: bool = False) -> None:
            nonlocal y
            thick = 2 if bold else 1
            cv2.putText(canvas, text, (hud_x, y), font, fs, color, thick, cv2.LINE_AA)
            y += dh

        def separator() -> None:
            nonlocal y
            cv2.line(canvas, (frame_w + 5, y), (frame_w + self._hud_width - 5, y), gray, 1)
            y += 12

        # Título
        cv2.putText(canvas, "PROJECT TITAN", (hud_x, y), font, 0.6, green, 2, cv2.LINE_AA)
        y += dh + 3
        cv2.putText(canvas, "Visao do Exterminador", (hud_x, y), font, 0.35, cyan, 1, cv2.LINE_AA)
        y += dh
        separator()

        # ── Mesa ──
        put("MESA", color=yellow, bold=True)
        y += 3

        hero_str = " ".join(state.hero_cards) if state.hero_cards else "---"
        put(f"Hero: {hero_str}", color=green)

        board_str = " ".join(state.board_cards) if state.board_cards else "---"
        put(f"Board: {board_str}")

        if state.dead_cards:
            dead_str = " ".join(state.dead_cards)
            put(f"Dead: {dead_str}", color=gray)

        put(f"Pot: {state.pot:.0f}   Stack: {state.stack:.0f}")
        put(f"Call: {state.call_amount:.0f}")

        turn_text = "SIM" if state.is_my_turn else "NAO"
        turn_color = green if state.is_my_turn else red
        put(f"Players: {state.active_players}   Meu turno: {turn_text}", color=turn_color)

        y += 5
        separator()

        # ── Decisão ──
        put("DECISAO", color=yellow, bold=True)
        y += 3

        action_color = _ACTION_COLORS.get(state.action.lower(), white)
        put(f"Acao: {state.action.upper()}", color=action_color, bold=True)
        put(f"Street: {state.street}")

        eq_color = green if state.equity >= 0.5 else (yellow if state.equity >= 0.3 else red)
        put(f"Equity: {state.equity:.1%}", color=eq_color)

        spr_text = f"{state.spr:.1f}" if state.spr < 99 else "N/A"
        put(f"SPR: {spr_text}")

        y += 5
        separator()

        # ── Performance ──
        put("PERFORMANCE", color=yellow, bold=True)
        y += 3

        put(f"Ciclo: {state.cycle_id}")
        ms_color = green if state.cycle_ms < 200 else (yellow if state.cycle_ms < 500 else red)
        put(f"Tempo ciclo: {state.cycle_ms:.0f} ms", color=ms_color)

        det_count = len(state.detections)
        put(f"Deteccoes YOLO: {det_count}")
        put(f"Botoes calibrados: {len(state.action_points)}")

        y += 5
        separator()

        # ── Detecções listadas ──
        put("DETECCOES", color=yellow, bold=True)
        y += 3

        shown = 0
        for det in state.detections[:12]:  # limita a 12 para caber
            label = det.get("label", "?")
            conf = det.get("confidence", 0.0)
            cat = _classify_label(label)
            cat_color = _CARD_COLORS.get(cat, gray)
            put(f"  {label} ({conf:.0%}) [{cat}]", color=cat_color)
            shown += 1

        if len(state.detections) > 12:
            remaining = len(state.detections) - 12
            put(f"  ... +{remaining} mais", color=gray)

        if shown == 0:
            put("  (nenhuma)", color=gray)

        # ── Timestamp ──
        y = max(y, frame_h - 30)
        ts = time.strftime("%H:%M:%S")
        put(f"Hora: {ts}", color=gray)

    # ── Placeholder quando não há frame ───────────────────────────

    def _draw_placeholder(self, canvas: Any) -> None:
        """Desenha tela de espera quando ainda não há frame do jogo."""
        h, w = canvas.shape[:2]
        cv2.putText(
            canvas, "TITAN: Aguardando frame...",
            (w // 2 - 180, h // 2),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA,
        )
        cv2.putText(
            canvas, "Certifique-se que o emulador esta aberto",
            (w // 2 - 220, h // 2 + 35),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1, cv2.LINE_AA,
        )
