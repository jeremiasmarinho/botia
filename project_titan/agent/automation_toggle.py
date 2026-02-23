"""automation_toggle.py — Global on/off toggle for Titan automation.

Provides a thread-safe toggle that the engine loop checks before
executing any game actions.  The user can press a configurable hotkey
(default **F7**) to enable/disable automation at any time.

Usage::

    from agent.automation_toggle import AutomationToggle

    toggle = AutomationToggle(hotkey="F7")
    toggle.start()          # begins listening for the hotkey

    if toggle.is_active:    # check in the main loop
        agent.step()

    toggle.stop()           # clean-up

Environment variables
---------------------
``TITAN_TOGGLE_HOTKEY``
    Override the default hotkey (e.g. ``"F8"``).
``TITAN_TOGGLE_INITIAL``
    Set to ``"1"`` to start with automation *enabled*.
    Default is ``"0"`` (off — user must press F7 to activate).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

_log = logging.getLogger("AutomationToggle")

# Optional: audio beep (Windows only)
try:
    import winsound as _winsound  # type: ignore[import-untyped]
except ImportError:
    _winsound = None

# Optional: keyboard module for hotkey
try:
    import keyboard as _keyboard  # type: ignore[import-untyped]
except ImportError:
    _keyboard = None


class AutomationToggle:
    """Thread-safe automation on/off toggle with hotkey support.

    Parameters
    ----------
    hotkey:
        Keyboard shortcut to toggle automation (default ``"F7"``).
    initial_state:
        Whether automation starts enabled or disabled.
    on_change:
        Optional callback ``(active: bool) -> None`` called on every toggle.
    beep:
        Play a short beep on toggle (Windows only).
    """

    def __init__(
        self,
        hotkey: str = "F7",
        initial_state: bool = False,
        on_change: Callable[[bool], None] | None = None,
        beep: bool = True,
    ) -> None:
        self._hotkey = os.getenv("TITAN_TOGGLE_HOTKEY", hotkey).strip()
        initial_env = os.getenv("TITAN_TOGGLE_INITIAL", "").strip()
        if initial_env in ("1", "true", "yes", "on"):
            initial_state = True
        elif initial_env in ("0", "false", "no", "off"):
            initial_state = False

        self._active = initial_state
        self._lock = threading.Lock()
        self._on_change = on_change
        self._beep = beep
        self._running = False
        self._hook_registered = False

    # ── Public API ────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """Return ``True`` if automation is currently enabled."""
        return self._active

    def activate(self) -> None:
        """Programmatically enable automation."""
        self._set(True)

    def deactivate(self) -> None:
        """Programmatically disable automation."""
        self._set(False)

    def toggle(self) -> bool:
        """Flip the toggle and return the new state."""
        with self._lock:
            self._active = not self._active
            new_state = self._active
        self._notify(new_state)
        return new_state

    def start(self) -> None:
        """Register the hotkey listener (non-blocking)."""
        if _keyboard is None:
            _log.warning(
                "keyboard module not available — hotkey disabled. "
                "Install with: pip install keyboard"
            )
            return
        if self._hook_registered:
            return

        try:
            _keyboard.add_hotkey(self._hotkey, self._on_hotkey, suppress=False)
            self._hook_registered = True
            self._running = True
            state_label = "ON" if self._active else "OFF"
            _log.info(
                f"Hotkey [{self._hotkey}] registered — "
                f"automation starts {state_label}"
            )
        except Exception as exc:
            _log.error(f"Failed to register hotkey [{self._hotkey}]: {exc}")

    def stop(self) -> None:
        """Unregister the hotkey listener."""
        self._running = False
        if _keyboard is not None and self._hook_registered:
            try:
                _keyboard.remove_hotkey(self._hotkey)
            except Exception:
                pass
            self._hook_registered = False

    # ── Internal ──────────────────────────────────────────────────────

    def _set(self, state: bool) -> None:
        with self._lock:
            if self._active == state:
                return
            self._active = state
        self._notify(state)

    def _on_hotkey(self) -> None:
        new_state = self.toggle()
        label = "ATIVADA" if new_state else "DESATIVADA"
        _log.info(f"[{self._hotkey}] Automação {label}")
        print(f"\n  >>> Automação {label} (pressione {self._hotkey} para alternar) <<<\n")

    def _notify(self, state: bool) -> None:
        if self._beep and _winsound is not None:
            try:
                freq = 800 if state else 400
                _winsound.Beep(freq, 150)
            except Exception:
                pass
        if self._on_change is not None:
            try:
                self._on_change(state)
            except Exception as exc:
                _log.error(f"on_change callback failed: {exc}")


# ── Module-level singleton for easy import ─────────────────────────
_global_toggle: AutomationToggle | None = None


def get_toggle() -> AutomationToggle:
    """Return (or create) the global AutomationToggle singleton."""
    global _global_toggle
    if _global_toggle is None:
        _global_toggle = AutomationToggle()
    return _global_toggle
