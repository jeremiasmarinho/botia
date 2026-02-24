"""titan_hud.py — Painel de Controle visual do Project Titan.

Janela tkinter *always-on-top* com tema escuro que mostra em tempo real:
  • Botão ON/OFF para ligar e desligar o bot
  • Cartas do hero e board (com ícones de naipe coloridos)
  • Equity da mão (barra de progresso + porcentagem)
  • Força da mão / street / SPR
  • Distribuição GTO (fold / call / raise)
  • Pot, stack, call amount
  • Pot odds
  • Classe do oponente
  • Log de ações rolante

Roda numa thread daemon separada — o agente continua funcionando
independentemente se a janela for fechada.

Uso::

    from tools.titan_hud import TitanHUD
    hud = TitanHUD()
    hud.start()           # não bloqueia
    # ... bot roda ...
    hud.stop()
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Any

from tools.titan_hud_state import hud_state, _HudSnapshot


# ═══════════════════════════════════════════════════════════════════════════
# Color palette
# ═══════════════════════════════════════════════════════════════════════════

_BG         = "#1a1a2e"
_BG_CARD    = "#16213e"
_BG_SECTION = "#0f3460"
_FG         = "#e0e0e0"
_FG_DIM     = "#888888"
_GREEN      = "#00e676"
_RED        = "#ff1744"
_YELLOW     = "#ffd740"
_ORANGE     = "#ff9100"
_CYAN       = "#00e5ff"
_BLUE       = "#448aff"
_PURPLE     = "#d500f9"

_SUIT_COLORS = {
    "h": "#e53935",    # hearts  — red
    "d": "#42a5f5",    # diamonds — blue
    "c": "#66bb6a",    # clubs   — green
    "s": "#bdbdbd",    # spades  — white/gray
}

_SUIT_SYMBOLS = {
    "h": "♥",
    "d": "♦",
    "c": "♣",
    "s": "♠",
}

_ACTION_COLORS = {
    "fold":        _RED,
    "call":        _GREEN,
    "raise_small": _ORANGE,
    "raise_big":   _ORANGE,
    "all_in":      _PURPLE,
    "wait":        _FG_DIM,
    "check":       _CYAN,
}

_STREET_LABELS = {
    "preflop": "PRE-FLOP",
    "flop":    "FLOP",
    "turn":    "TURN",
    "river":   "RIVER",
}


# ═══════════════════════════════════════════════════════════════════════════
# Card rendering helpers
# ═══════════════════════════════════════════════════════════════════════════

def _render_card(parent: tk.Frame, card_str: str, size: str = "large") -> tk.Frame:
    """Create a mini card widget from a 2-char string like 'Ah'."""
    if len(card_str) < 2:
        return _render_card_back(parent, size)

    rank = card_str[0]
    suit = card_str[1].lower()
    suit_color = _SUIT_COLORS.get(suit, _FG)
    suit_sym = _SUIT_SYMBOLS.get(suit, "?")

    pad_x = 6 if size == "large" else 4
    pad_y = 8 if size == "large" else 4
    rank_size = 14 if size == "large" else 11
    suit_size = 12 if size == "large" else 9

    card_frame = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid",
                          padx=pad_x, pady=pad_y)

    rank_lbl = tk.Label(card_frame, text=rank, bg="#ffffff", fg="#1a1a1a",
                        font=("Consolas", rank_size, "bold"))
    rank_lbl.pack()

    suit_lbl = tk.Label(card_frame, text=suit_sym, bg="#ffffff", fg=suit_color,
                        font=("Consolas", suit_size))
    suit_lbl.pack()

    return card_frame


def _render_card_back(parent: tk.Frame, size: str = "large") -> tk.Frame:
    """Render an unknown/face-down card."""
    pad_x = 6 if size == "large" else 4
    pad_y = 8 if size == "large" else 4
    card_frame = tk.Frame(parent, bg="#3949ab", bd=1, relief="solid",
                          padx=pad_x, pady=pad_y)
    tk.Label(card_frame, text="?", bg="#3949ab", fg="#7986cb",
             font=("Consolas", 14 if size == "large" else 11, "bold")).pack()
    tk.Label(card_frame, text="?", bg="#3949ab", fg="#7986cb",
             font=("Consolas", 12 if size == "large" else 9)).pack()
    return card_frame


# ═══════════════════════════════════════════════════════════════════════════
# Progress bar helper
# ═══════════════════════════════════════════════════════════════════════════

class _EquityBar(tk.Canvas):
    """Horizontal bar showing equity percentage with gradient coloring."""

    def __init__(self, parent: tk.Widget, width: int = 280, height: int = 22, **kw: Any):
        super().__init__(parent, width=width, height=height,
                         bg=_BG, highlightthickness=0, **kw)
        self._bar_w = width
        self._bar_h = height
        self._value = 0.0

    def set_value(self, v: float) -> None:
        v = max(0.0, min(1.0, v))
        self._value = v
        self.delete("all")

        # Background track
        self.create_rectangle(0, 0, self._bar_w, self._bar_h, fill="#2a2a3e", outline="")

        # Filled portion
        fill_w = int(self._bar_w * v)
        if fill_w > 0:
            if v >= 0.6:
                color = _GREEN
            elif v >= 0.4:
                color = _YELLOW
            elif v >= 0.25:
                color = _ORANGE
            else:
                color = _RED
            self.create_rectangle(0, 0, fill_w, self._bar_h, fill=color, outline="")

        # Text overlay
        pct_text = f"{v:.1%}"
        self.create_text(self._bar_w // 2, self._bar_h // 2, text=pct_text,
                         fill="white", font=("Consolas", 10, "bold"))


# ═══════════════════════════════════════════════════════════════════════════
# GTO Distribution mini-bars
# ═══════════════════════════════════════════════════════════════════════════

class _GTOBars(tk.Frame):
    """Mini horizontal bars for fold/call/raise distribution."""

    def __init__(self, parent: tk.Widget, **kw: Any):
        super().__init__(parent, bg=_BG, **kw)
        self._bars: dict[str, tuple[tk.Canvas, tk.Label]] = {}
        self._actions = [
            ("fold",        _RED,    "FOLD"),
            ("call",        _GREEN,  "CALL"),
            ("raise_small", _ORANGE, "RAISE S"),
            ("raise_big",   _PURPLE, "RAISE B"),
        ]
        for action, color, label in self._actions:
            row = tk.Frame(self, bg=_BG)
            row.pack(fill="x", pady=1)
            lbl = tk.Label(row, text=label, width=8, anchor="w",
                           bg=_BG, fg=_FG_DIM, font=("Consolas", 8))
            lbl.pack(side="left")
            canvas = tk.Canvas(row, width=160, height=12,
                               bg="#2a2a3e", highlightthickness=0)
            canvas.pack(side="left", padx=(4, 0))
            pct_lbl = tk.Label(row, text="0%", width=5, anchor="e",
                               bg=_BG, fg=_FG_DIM, font=("Consolas", 8))
            pct_lbl.pack(side="left", padx=(4, 0))
            self._bars[action] = (canvas, pct_lbl)

    def update_values(self, dist: dict[str, float]) -> None:
        for action, (canvas, pct_lbl) in self._bars.items():
            v = dist.get(action, 0.0)
            canvas.delete("all")
            fill_w = int(160 * min(1.0, v))
            color = [c for a, c, _ in self._actions if a == action]
            fill_color = color[0] if color else _FG_DIM
            if fill_w > 0:
                canvas.create_rectangle(0, 0, fill_w, 12,
                                        fill=fill_color, outline="")
            pct_lbl.config(text=f"{v:.0%}")


# ═══════════════════════════════════════════════════════════════════════════
# Main HUD Window
# ═══════════════════════════════════════════════════════════════════════════

class TitanHUD:
    """Painel de Controle do Project Titan.

    Runs in a daemon thread; call ``start()`` to show the window
    and ``stop()`` to close it.
    """

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._root: tk.Tk | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_gui, daemon=True,
                                        name="TitanHUD")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    # ── Main GUI thread ───────────────────────────────────────────

    def _run_gui(self) -> None:
        root = tk.Tk()
        self._root = root
        root.title("Project Titan — Painel de Controle")
        root.configure(bg=_BG)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.geometry("360x820")

        # Custom icon — skip if unavailable
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        # ── Header section ─────────────────────────────────────
        header = tk.Frame(root, bg=_BG_SECTION, pady=8)
        header.pack(fill="x", padx=6, pady=(6, 3))

        title_lbl = tk.Label(header, text="⚡ PROJECT TITAN",
                             bg=_BG_SECTION, fg=_CYAN,
                             font=("Segoe UI", 14, "bold"))
        title_lbl.pack()

        subtitle_lbl = tk.Label(header, text="Poker Bot Control Panel",
                                bg=_BG_SECTION, fg=_FG_DIM,
                                font=("Segoe UI", 9))
        subtitle_lbl.pack()

        # ── ON / OFF toggle ────────────────────────────────────
        toggle_frame = tk.Frame(root, bg=_BG, pady=6)
        toggle_frame.pack(fill="x", padx=6)

        self._bot_active = False
        self._toggle_btn = tk.Button(
            toggle_frame,
            text="▶  LIGAR BOT",
            bg="#2e7d32", fg="white",
            activebackground="#388e3c", activeforeground="white",
            font=("Segoe UI", 12, "bold"),
            relief="flat", cursor="hand2",
            padx=20, pady=6,
            command=self._on_toggle_click,
        )
        self._toggle_btn.pack(fill="x")

        self._status_lbl = tk.Label(toggle_frame, text="Status: DESLIGADO",
                                    bg=_BG, fg=_RED,
                                    font=("Consolas", 9))
        self._status_lbl.pack(pady=(4, 0))

        # ── Cards section ──────────────────────────────────────
        cards_section = tk.LabelFrame(root, text=" Cartas ",
                                      bg=_BG, fg=_CYAN,
                                      font=("Segoe UI", 10, "bold"),
                                      bd=1, relief="groove")
        cards_section.pack(fill="x", padx=6, pady=3)

        # Hero cards
        hero_row = tk.Frame(cards_section, bg=_BG)
        hero_row.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(hero_row, text="HERO:", bg=_BG, fg=_GREEN,
                 font=("Consolas", 9, "bold"), width=7, anchor="w").pack(side="left")
        self._hero_cards_frame = tk.Frame(hero_row, bg=_BG)
        self._hero_cards_frame.pack(side="left")

        # Board cards
        board_row = tk.Frame(cards_section, bg=_BG)
        board_row.pack(fill="x", padx=6, pady=(2, 6))
        tk.Label(board_row, text="BOARD:", bg=_BG, fg=_YELLOW,
                 font=("Consolas", 9, "bold"), width=7, anchor="w").pack(side="left")
        self._board_cards_frame = tk.Frame(board_row, bg=_BG)
        self._board_cards_frame.pack(side="left")

        # ── Equity section ─────────────────────────────────────
        equity_section = tk.LabelFrame(root, text=" Equity & Força ",
                                       bg=_BG, fg=_CYAN,
                                       font=("Segoe UI", 10, "bold"),
                                       bd=1, relief="groove")
        equity_section.pack(fill="x", padx=6, pady=3)

        eq_inner = tk.Frame(equity_section, bg=_BG, padx=6, pady=6)
        eq_inner.pack(fill="x")

        self._equity_bar = _EquityBar(eq_inner, width=320, height=24)
        self._equity_bar.pack()

        # Street + SPR row
        metrics_row = tk.Frame(eq_inner, bg=_BG)
        metrics_row.pack(fill="x", pady=(6, 0))

        self._street_lbl = tk.Label(metrics_row, text="PRE-FLOP", bg=_BG, fg=_BLUE,
                                    font=("Consolas", 10, "bold"))
        self._street_lbl.pack(side="left")

        self._spr_lbl = tk.Label(metrics_row, text="SPR: --", bg=_BG, fg=_FG,
                                 font=("Consolas", 9))
        self._spr_lbl.pack(side="right")

        # Pot odds
        odds_row = tk.Frame(eq_inner, bg=_BG)
        odds_row.pack(fill="x", pady=(2, 0))

        self._potodds_lbl = tk.Label(odds_row, text="Pot Odds: --", bg=_BG, fg=_FG_DIM,
                                     font=("Consolas", 9))
        self._potodds_lbl.pack(side="left")

        self._committed_lbl = tk.Label(odds_row, text="", bg=_BG, fg=_PURPLE,
                                       font=("Consolas", 9, "bold"))
        self._committed_lbl.pack(side="right")

        # ── Decision section ───────────────────────────────────
        decision_section = tk.LabelFrame(root, text=" Decisão ",
                                         bg=_BG, fg=_CYAN,
                                         font=("Segoe UI", 10, "bold"),
                                         bd=1, relief="groove")
        decision_section.pack(fill="x", padx=6, pady=3)

        dec_inner = tk.Frame(decision_section, bg=_BG, padx=6, pady=6)
        dec_inner.pack(fill="x")

        self._action_lbl = tk.Label(dec_inner, text="WAIT", bg=_BG, fg=_FG_DIM,
                                    font=("Segoe UI", 16, "bold"))
        self._action_lbl.pack()

        self._desc_lbl = tk.Label(dec_inner, text="", bg=_BG, fg=_FG_DIM,
                                  font=("Consolas", 8), wraplength=330, justify="center")
        self._desc_lbl.pack(pady=(2, 0))

        # GTO distribution
        gto_frame = tk.LabelFrame(dec_inner, text=" GTO Mix ",
                                  bg=_BG, fg=_FG_DIM,
                                  font=("Consolas", 8), bd=0)
        gto_frame.pack(fill="x", pady=(6, 0))

        self._gto_bars = _GTOBars(gto_frame)
        self._gto_bars.pack(fill="x")

        # ── Table info section ─────────────────────────────────
        table_section = tk.LabelFrame(root, text=" Mesa ",
                                      bg=_BG, fg=_CYAN,
                                      font=("Segoe UI", 10, "bold"),
                                      bd=1, relief="groove")
        table_section.pack(fill="x", padx=6, pady=3)

        tbl_inner = tk.Frame(table_section, bg=_BG, padx=6, pady=6)
        tbl_inner.pack(fill="x")

        # Grid of pot / stack / call / players
        for i, (label, attr) in enumerate([
            ("Pot", "_pot_val"),
            ("Stack", "_stack_val"),
            ("Call", "_call_val"),
            ("Players", "_players_val"),
        ]):
            row = tk.Frame(tbl_inner, bg=_BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{label}:", bg=_BG, fg=_FG_DIM,
                     font=("Consolas", 9), width=10, anchor="w").pack(side="left")
            val_lbl = tk.Label(row, text="--", bg=_BG, fg=_FG,
                               font=("Consolas", 10, "bold"), anchor="w")
            val_lbl.pack(side="left")
            setattr(self, attr, val_lbl)

        # My turn + opponent
        extra_row = tk.Frame(tbl_inner, bg=_BG)
        extra_row.pack(fill="x", pady=(4, 0))
        self._turn_lbl = tk.Label(extra_row, text="Meu turno: --", bg=_BG, fg=_FG_DIM,
                                  font=("Consolas", 9))
        self._turn_lbl.pack(side="left")
        self._opponent_lbl = tk.Label(extra_row, text="", bg=_BG, fg=_FG_DIM,
                                      font=("Consolas", 9))
        self._opponent_lbl.pack(side="right")

        # ── Performance section ────────────────────────────────
        perf_section = tk.LabelFrame(root, text=" Performance ",
                                     bg=_BG, fg=_CYAN,
                                     font=("Segoe UI", 10, "bold"),
                                     bd=1, relief="groove")
        perf_section.pack(fill="x", padx=6, pady=3)

        perf_inner = tk.Frame(perf_section, bg=_BG, padx=6, pady=4)
        perf_inner.pack(fill="x")

        perf_row = tk.Frame(perf_inner, bg=_BG)
        perf_row.pack(fill="x")

        self._cycle_lbl = tk.Label(perf_row, text="Ciclo: 0", bg=_BG, fg=_FG_DIM,
                                   font=("Consolas", 9))
        self._cycle_lbl.pack(side="left")

        self._latency_lbl = tk.Label(perf_row, text="0 ms", bg=_BG, fg=_FG_DIM,
                                     font=("Consolas", 9))
        self._latency_lbl.pack(side="right")

        mode_row = tk.Frame(perf_inner, bg=_BG)
        mode_row.pack(fill="x")

        self._mode_lbl = tk.Label(mode_row, text="SOLO", bg=_BG, fg=_BLUE,
                                  font=("Consolas", 9, "bold"))
        self._mode_lbl.pack(side="left")

        self._sanity_lbl = tk.Label(mode_row, text="Sanity: OK", bg=_BG, fg=_GREEN,
                                    font=("Consolas", 9))
        self._sanity_lbl.pack(side="right")

        # ── Action log section ─────────────────────────────────
        log_section = tk.LabelFrame(root, text=" Log ",
                                    bg=_BG, fg=_CYAN,
                                    font=("Segoe UI", 10, "bold"),
                                    bd=1, relief="groove")
        log_section.pack(fill="both", expand=True, padx=6, pady=(3, 6))

        self._log_text = tk.Text(log_section, bg="#0d0d1a", fg=_FG_DIM,
                                 font=("Consolas", 8), height=5,
                                 wrap="word", state="disabled",
                                 bd=0, highlightthickness=0,
                                 insertbackground=_FG_DIM)
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # Tag colors for log
        self._log_text.tag_configure("fold", foreground=_RED)
        self._log_text.tag_configure("call", foreground=_GREEN)
        self._log_text.tag_configure("raise", foreground=_ORANGE)
        self._log_text.tag_configure("wait", foreground=_FG_DIM)
        self._log_text.tag_configure("time", foreground=_FG_DIM)

        # ── Track previous card state for efficient updates ────
        self._prev_hero: list[str] = []
        self._prev_board: list[str] = []
        self._prev_log_len = 0

        # ── Start periodic refresh ────────────────────────────
        self._refresh(root)

        # Run the mainloop (blocks in this thread)
        try:
            root.mainloop()
        except Exception:
            pass
        finally:
            self._running = False

    # ── Toggle callback ───────────────────────────────────────────

    def _on_toggle_click(self) -> None:
        self._bot_active = not self._bot_active
        hud_state.request_toggle(self._bot_active)
        self._update_toggle_ui()

    def _update_toggle_ui(self) -> None:
        if self._bot_active:
            self._toggle_btn.config(
                text="⏹  DESLIGAR BOT",
                bg="#c62828",
                activebackground="#d32f2f",
            )
            self._status_lbl.config(text="Status: LIGADO", fg=_GREEN)
        else:
            self._toggle_btn.config(
                text="▶  LIGAR BOT",
                bg="#2e7d32",
                activebackground="#388e3c",
            )
            self._status_lbl.config(text="Status: DESLIGADO", fg=_RED)

    # ── Periodic refresh (runs every 250ms) ───────────────────────

    def _refresh(self, root: tk.Tk) -> None:
        if not self._running:
            return

        try:
            snap = hud_state.snapshot()
            self._apply_snapshot(snap)
        except Exception:
            pass

        root.after(250, lambda: self._refresh(root))

    def _apply_snapshot(self, s: _HudSnapshot) -> None:
        """Apply a snapshot to all widgets."""

        # ── Toggle sync (agent may change it via F7) ──
        if s.bot_active != self._bot_active:
            self._bot_active = s.bot_active
            self._update_toggle_ui()

        # ── Cards (only rebuild if changed) ──
        if s.hero_cards != self._prev_hero:
            self._prev_hero = list(s.hero_cards)
            for w in self._hero_cards_frame.winfo_children():
                w.destroy()
            if s.hero_cards:
                for c in s.hero_cards:
                    card_w = _render_card(self._hero_cards_frame, c, "large")
                    card_w.pack(side="left", padx=2)
            else:
                tk.Label(self._hero_cards_frame, text="---", bg=_BG, fg=_FG_DIM,
                         font=("Consolas", 12)).pack(side="left")

        if s.board_cards != self._prev_board:
            self._prev_board = list(s.board_cards)
            for w in self._board_cards_frame.winfo_children():
                w.destroy()
            if s.board_cards:
                for c in s.board_cards:
                    card_w = _render_card(self._board_cards_frame, c, "small")
                    card_w.pack(side="left", padx=2)
            else:
                tk.Label(self._board_cards_frame, text="---", bg=_BG, fg=_FG_DIM,
                         font=("Consolas", 11)).pack(side="left")

        # ── Equity ──
        self._equity_bar.set_value(s.equity)

        # ── Street / SPR ──
        street_text = _STREET_LABELS.get(s.street, s.street.upper())
        self._street_lbl.config(text=street_text)
        spr_text = f"SPR: {s.spr:.1f}" if s.spr < 90 else "SPR: --"
        self._spr_lbl.config(text=spr_text)

        # ── Pot odds ──
        if s.pot_odds > 0:
            self._potodds_lbl.config(text=f"Pot Odds: {s.pot_odds:.1%}")
        else:
            self._potodds_lbl.config(text="Pot Odds: --")

        # ── Committed ──
        self._committed_lbl.config(text="COMMITTED" if s.committed else "")

        # ── Decision ──
        action_text = s.action.upper().replace("_", " ")
        action_color = _ACTION_COLORS.get(s.action.lower(), _FG)
        self._action_lbl.config(text=action_text, fg=action_color)
        self._desc_lbl.config(text=s.description if s.description else "")

        # ── GTO distribution ──
        if s.gto_distribution:
            self._gto_bars.update_values(s.gto_distribution)

        # ── Table info ──
        self._pot_val.config(text=f"{s.pot:,.0f}")
        self._stack_val.config(text=f"{s.stack:,.0f}")
        self._call_val.config(text=f"{s.call_amount:,.0f}")
        self._players_val.config(text=str(s.active_players) if s.active_players else "--")

        turn_text = "SIM ●" if s.is_my_turn else "NÃO"
        turn_color = _GREEN if s.is_my_turn else _FG_DIM
        self._turn_lbl.config(text=f"Meu turno: {turn_text}", fg=turn_color)

        if s.opponent_class and s.opponent_class != "Unknown":
            self._opponent_lbl.config(text=f"Oponente: {s.opponent_class}")
        else:
            self._opponent_lbl.config(text="")

        # ── Performance ──
        self._cycle_lbl.config(text=f"Ciclo: {s.cycle_id}")

        ms_color = _GREEN if s.cycle_ms < 200 else (_YELLOW if s.cycle_ms < 500 else _RED)
        self._latency_lbl.config(text=f"{s.cycle_ms:.0f} ms", fg=ms_color)

        mode_color = _PURPLE if "GOD" in s.mode.upper() else _BLUE
        self._mode_lbl.config(text=s.mode, fg=mode_color)

        if s.sanity_ok:
            self._sanity_lbl.config(text="Sanity: OK", fg=_GREEN)
        else:
            self._sanity_lbl.config(text=f"Sanity: {s.sanity_reason}", fg=_RED)

        # ── Action log ──
        if len(s.action_log) != self._prev_log_len:
            new_entries = s.action_log[self._prev_log_len:]
            self._prev_log_len = len(s.action_log)
            self._log_text.config(state="normal")
            for entry in new_entries:
                # Determine tag from entry content
                tag = "wait"
                entry_low = entry.lower()
                if "fold" in entry_low:
                    tag = "fold"
                elif "call" in entry_low:
                    tag = "call"
                elif "raise" in entry_low or "all_in" in entry_low:
                    tag = "raise"
                self._log_text.insert("end", entry + "\n", tag)
            self._log_text.see("end")
            self._log_text.config(state="disabled")


# ═══════════════════════════════════════════════════════════════════════════
# Quick standalone test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import random

    hud = TitanHUD()
    hud.start()

    # Simulate agent pushing data
    time.sleep(1)

    ranks = list("23456789TJQKA")
    suits = list("hdcs")

    def random_card() -> str:
        return random.choice(ranks) + random.choice(suits)

    cycle = 0
    while True:
        cycle += 1
        hero = [random_card(), random_card()]
        board = [random_card() for _ in range(random.choice([0, 3, 4, 5]))]
        equity = random.uniform(0.15, 0.85)
        action = random.choice(["fold", "call", "raise_small", "raise_big", "wait"])
        street = ["preflop", "flop", "turn", "river"][min(len(board), 3) if board else 0]

        hud_state.push(
            bot_active=True,
            hero_cards=hero,
            board_cards=board,
            pot=random.uniform(50, 5000),
            stack=random.uniform(500, 10000),
            call_amount=random.uniform(0, 500),
            active_players=random.randint(2, 6),
            is_my_turn=random.random() > 0.5,
            action=action,
            street=street,
            equity=equity,
            spr=random.uniform(0.5, 15),
            pot_odds=random.uniform(0.1, 0.5),
            committed=random.random() > 0.85,
            mode=random.choice(["SOLO", "SQUAD_GOD_MODE"]),
            opponent_class=random.choice(["TAG", "LAG", "Calling Station", "Unknown"]),
            gto_distribution={
                "fold": random.uniform(0, 0.5),
                "call": random.uniform(0, 0.5),
                "raise_small": random.uniform(0, 0.3),
                "raise_big": random.uniform(0, 0.2),
            },
            description=f"{action.upper()} - Solo TAG | Equity {equity:.0%}",
            cycle_id=cycle,
            cycle_ms=random.uniform(80, 600),
            sanity_ok=random.random() > 0.1,
            sanity_reason="ok" if random.random() > 0.1 else "unstable_tail",
        )
        hud_state.log_action(
            f"[{time.strftime('%H:%M:%S')}] Ciclo {cycle}: {action.upper()} "
            f"| Equity {equity:.0%} | Pot {random.uniform(50, 5000):.0f}"
        )
        time.sleep(2)
