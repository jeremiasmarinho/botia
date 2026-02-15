"""Colored terminal logger for Project Titan.

Provides ANSI-coloured output for demo / presentation quality logging.
Falls back to plain text when the terminal does not support ANSI or when
``TITAN_NO_COLOR=1`` is set.
"""

from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_FG_RED = "\033[31m"
_FG_GREEN = "\033[32m"
_FG_YELLOW = "\033[33m"
_FG_BLUE = "\033[34m"
_FG_MAGENTA = "\033[35m"
_FG_CYAN = "\033[36m"
_FG_WHITE = "\033[37m"
_FG_BRIGHT_GREEN = "\033[92m"
_FG_BRIGHT_YELLOW = "\033[93m"
_FG_BRIGHT_CYAN = "\033[96m"


def _supports_color() -> bool:
    """Heuristic check for ANSI colour support."""
    if os.getenv("TITAN_NO_COLOR", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.getenv("NO_COLOR"):
        return False
    if os.name == "nt":
        # Windows 10+ supports ANSI in conhost / Windows Terminal
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # Enable VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            # Fallback: if running inside Windows Terminal or VS Code
            if os.getenv("WT_SESSION") or os.getenv("TERM_PROGRAM") == "vscode":
                return True
            return False
    # Unix-like: usually fine
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR_ENABLED = _supports_color()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class TitanLogger:
    """Simple coloured logger with module prefix."""

    # Colour palette per module
    _MODULE_COLORS: dict[str, str] = {
        "HiveBrain": _FG_MAGENTA,
        "Orchestrator": _FG_CYAN,
        "Agent": _FG_GREEN,
        "Vision": _FG_BLUE,
        "Equity": _FG_YELLOW,
        "RNG": _FG_RED,
        "GhostMouse": _FG_BRIGHT_GREEN,
        "Action": _FG_BRIGHT_YELLOW,
        "Memory": _FG_BRIGHT_CYAN,
    }

    def __init__(self, module: str) -> None:
        self.module = module
        self._prefix_color = self._MODULE_COLORS.get(module, _FG_WHITE)

    def _format(self, level_color: str, level: str, message: str) -> str:
        if _COLOR_ENABLED:
            return (
                f"{self._prefix_color}{_BOLD}[{self.module}]{_RESET} "
                f"{level_color}{level}{_RESET} {message}"
            )
        return f"[{self.module}] {level} {message}"

    def info(self, message: str) -> None:
        print(self._format(_FG_GREEN, ">", message))

    def success(self, message: str) -> None:
        print(self._format(_FG_BRIGHT_GREEN, "+", message))

    def warn(self, message: str) -> None:
        print(self._format(_FG_YELLOW, "!", message))

    def error(self, message: str) -> None:
        print(self._format(_FG_RED, "X", message))

    def status(self, message: str) -> None:
        """Dimmed status line for non-critical events."""
        if _COLOR_ENABLED:
            print(f"{self._prefix_color}{_BOLD}[{self.module}]{_RESET} {_DIM}{message}{_RESET}")
        else:
            print(f"[{self.module}] {message}")

    def highlight(self, message: str) -> None:
        """Bold bright message (mode activations, demos)."""
        if _COLOR_ENABLED:
            print(f"{self._prefix_color}{_BOLD}[{self.module}] * {message}{_RESET}")
        else:
            print(f"[{self.module}] * {message}")
