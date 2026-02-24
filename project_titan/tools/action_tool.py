"""ActionTool – bridge between the decision workflow and the GhostMouse actuator.

When ``TITAN_GHOST_MOUSE=1``, actions are executed via humanised Bézier
mouse movement.  Otherwise the tool only computes timing (safe for CI /
simulation runs).

Raise flow (PPPoker PLO6)
-------------------------
The raise on PPPoker is a **two-step** interaction:

1. Click the **"Raise"** button on the main action bar → opens a modal.
2. Inside the modal, select a **sizing preset** (``2x``, ``2.5x``, ``Pot``)
   and then click the green **"Raise"** confirm button.

The mapping from workflow actions to UI interaction is:

- ``raise_small`` → Raise → 2x → Raise confirm
- ``raise_big``   → Raise → Pot → Raise confirm
- ``raise_slider`` → Raise → drag slider → Raise confirm

Button regions (configurable via env / calibration):

- ``fold``            Main bar Fold button
- ``call``            Main bar Call button
- ``raise``           Main bar Raise button (opens modal)
- ``raise_2x``        Modal preset 2x
- ``raise_2_5x``      Modal preset 2.5x
- ``raise_pot``       Modal preset Pot
- ``raise_confirm``   Modal green Raise confirm button
- ``timebank``        Timebank (extra time) button
- ``sit_out``         Sit-out button
- ``emote``           Emote button (human disguise)
- ``slider_start``    Left edge of raise slider
- ``slider_end``      Right edge of raise slider
"""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.ghost_mouse import GhostMouse

from tools.mouse_protocol import (
    ClickPoint,
    GhostMouseConfig,
    classify_difficulty,
)
from utils.logger import TitanLogger


# Default screen regions (overridable via env / set_action_regions)
_DEFAULT_ACTION_REGIONS: dict[str, ClickPoint] = {
    # Main action bar
    "fold": ClickPoint(x=600, y=700),
    "call": ClickPoint(x=800, y=700),
    "raise": ClickPoint(x=1000, y=700),
    # Raise modal presets
    "raise_2x": ClickPoint(x=200, y=750),
    "raise_2_5x": ClickPoint(x=350, y=750),
    "raise_pot": ClickPoint(x=500, y=750),
    # Raise modal confirm
    "raise_confirm": ClickPoint(x=1000, y=750),
    # Raise slider endpoints
    "slider_start": ClickPoint(x=200, y=700),
    "slider_end": ClickPoint(x=900, y=700),
    # Utility buttons
    "timebank": ClickPoint(x=540, y=1600),
    "sit_out": ClickPoint(x=100, y=100),
    "emote": ClickPoint(x=100, y=1700),
}

# Maps workflow raise actions to their modal preset button key.
_RAISE_PRESET_MAP: dict[str, str] = {
    "raise_small": "raise_2x",
    "raise_big": "raise_pot",
}


class ActionTool:
    """Execute a poker action, optionally driving real cursor movement.

    Simple actions (fold, call) are single clicks.
    Raise actions follow a two-step sequence:
    Raise button → preset selection → confirm.
    """

    def __init__(self) -> None:
        self._log = TitanLogger("Action")
        from agent.ghost_mouse import GhostMouse  # lazy import to avoid circular
        self._ghost = GhostMouse(GhostMouseConfig())
        self._regions = dict(_DEFAULT_ACTION_REGIONS)
        self._adb_scale = 1.0
        self._load_regions_from_env()
        self._load_regions_from_config()
        self._load_input_backend_config()

    # -- configuration -------------------------------------------------------

    def set_action_regions(self, regions: dict[str, ClickPoint]) -> None:
        """Override button regions at runtime (e.g. from vision calibration)."""
        self._regions.update(regions)

    def set_action_regions_from_xy(self, regions: dict[str, tuple[int, int]]) -> None:
        """Override button regions from plain (x, y) tuples."""
        normalized: dict[str, ClickPoint] = {}
        for action_name, point in regions.items():
            if not isinstance(action_name, str):
                continue
            if not isinstance(point, tuple) or len(point) != 2:
                continue
            x_raw, y_raw = point
            if not isinstance(x_raw, int) or not isinstance(y_raw, int):
                continue
            normalized[action_name.strip().lower()] = ClickPoint(x=x_raw, y=y_raw)
        if normalized:
            self._regions.update(normalized)

    # -- public API ----------------------------------------------------------

    def act(self, action: str, street: str = "preflop", **kwargs: Any) -> str:
        """Execute *action* and return a summary string.

        *street* is used to compute thinking-delay difficulty.

        For raise actions (``raise_small``, ``raise_big``), executes
        a multi-step click sequence:
        1. Click "Raise" to open modal
        2. Click sizing preset (2x / Pot)
        3. Click "Raise" confirm

        For ``raise_slider``, executes:
        1. Click "Raise" to open modal
        2. Drag the slider to the desired fraction
        3. Click "Raise" confirm

        Additional actions:
        - ``timebank``: click timebank button
        - ``sit_out``:  click sit-out button
        - ``emote``:    click emote button (human disguise)
        """
        action_lower = action.strip().lower()
        difficulty = classify_difficulty(action_lower, street)

        # ── Raise slider (custom sizing) ──
        if action_lower == "raise_slider":
            fraction = kwargs.get("fraction", 0.5)
            return self._act_raise_slider(
                action_lower, street, difficulty, float(fraction),
            )

        # ── Raise = multi-step preset sequence ──
        preset_key = _RAISE_PRESET_MAP.get(action_lower)
        if preset_key is not None:
            return self._act_raise_sequence(
                action_lower, preset_key, street, difficulty,
            )

        # ── Simple single-click action (fold / call / timebank / emote / sit_out) ──
        target = self._regions.get(action_lower)
        if target is not None:
            self._log.info(
                f"dispatch action={action_lower} street={street} "
                f"target=({target.x},{target.y}) difficulty={difficulty}"
            )
            delay = self._ghost.move_and_click(
                target,
                difficulty=difficulty,
                action_name=action_lower,
            )
        else:
            self._log.warn(
                f"dispatch action={action_lower} street={street} target=<none> difficulty={difficulty}"
            )
            delay = self._ghost.thinking_delay(difficulty)

        return f"action={action} delay={delay:.2f}s difficulty={difficulty}"

    # -- raise sequence ------------------------------------------------------

    def _act_raise_sequence(
        self,
        action: str,
        preset_key: str,
        street: str,
        difficulty: str,
    ) -> str:
        """Execute the two-step raise flow: open modal → select preset → confirm."""
        raise_btn = self._regions.get("raise")
        preset_btn = self._regions.get(preset_key)
        confirm_btn = self._regions.get("raise_confirm")

        steps: list[ClickPoint] = []
        step_labels: list[str] = []

        if raise_btn is not None:
            steps.append(raise_btn)
            step_labels.append(f"raise({raise_btn.x},{raise_btn.y})")
        if preset_btn is not None:
            steps.append(preset_btn)
            step_labels.append(f"{preset_key}({preset_btn.x},{preset_btn.y})")
        if confirm_btn is not None:
            steps.append(confirm_btn)
            step_labels.append(f"confirm({confirm_btn.x},{confirm_btn.y})")

        if steps:
            self._log.info(
                f"dispatch action={action} street={street} "
                f"sequence=[{' → '.join(step_labels)}] difficulty={difficulty}"
            )
            delay = self._ghost.move_and_click_sequence(
                steps,
                difficulty=difficulty,
                action_name=action,
            )
        else:
            self._log.warn(
                f"dispatch action={action} street={street} sequence=<empty> difficulty={difficulty}"
            )
            delay = self._ghost.thinking_delay(difficulty)

        return f"action={action} delay={delay:.2f}s difficulty={difficulty}"

    def _act_raise_slider(
        self,
        action: str,
        street: str,
        difficulty: str,
        fraction: float,
    ) -> str:
        """Execute raise via slider: open modal → drag slider → confirm.

        Args:
            fraction: 0.0 = minimum raise, 1.0 = all-in.  The slider is
                      dragged to exactly this fraction of the slider range.
        """
        raise_btn = self._regions.get("raise")
        slider_start = self._regions.get("slider_start")
        slider_end = self._regions.get("slider_end")
        confirm_btn = self._regions.get("raise_confirm")

        delay = self._ghost.thinking_delay(difficulty)

        # Step 1: click Raise to open modal
        if raise_btn is not None:
            self._ghost.move_and_click(
                raise_btn, difficulty=difficulty, action_name="raise_open",
            )
            import time
            time.sleep(0.5)  # wait for modal animation

        # Step 2: drag slider to desired fraction
        if slider_start is not None and slider_end is not None:
            frac = max(0.0, min(1.0, fraction))
            target_x = int(slider_start.x + (slider_end.x - slider_start.x) * frac)
            target_y = slider_start.y  # slider is horizontal

            swipe_start = ClickPoint(x=slider_start.x, y=slider_start.y)
            swipe_end = ClickPoint(x=target_x, y=target_y)

            self._ghost.swipe(
                swipe_start, swipe_end,
                duration=0.3 + 0.3 * frac,  # longer swipe = more time
                action_name="raise_slider_drag",
            )
            import time
            time.sleep(0.3)  # wait for value to update

            self._log.info(
                f"dispatch action={action} street={street} "
                f"slider_fraction={frac:.2f} difficulty={difficulty}"
            )

        # Step 3: confirm
        if confirm_btn is not None:
            self._ghost.move_and_click(
                confirm_btn, difficulty="easy", action_name="raise_confirm",
            )

        return f"action={action} delay={delay:.2f}s fraction={fraction:.2f} difficulty={difficulty}"

    def take_screenshot(self) -> bytes | None:
        """Capture a screenshot from the device via GhostMouse's ADB helper.

        Returns raw PNG bytes or *None* on failure.  Works even inside
        PPPoker because ``screencap`` uses the framebuffer (not input
        injection), so Unity's anti-automation does not block it.
        """
        return self._ghost.take_screenshot()

    # -- helpers -------------------------------------------------------------

    def _load_regions_from_env(self) -> None:
        """Load button coordinates from environment variables.

        Supported variables::

            TITAN_BTN_FOLD          Fold button
            TITAN_BTN_CALL          Call button
            TITAN_BTN_RAISE         Raise button (opens modal)
            TITAN_BTN_RAISE_2X      Modal 2x preset
            TITAN_BTN_RAISE_2_5X    Modal 2.5x preset
            TITAN_BTN_RAISE_POT     Modal Pot preset
            TITAN_BTN_RAISE_CONFIRM Modal confirm button
            TITAN_BTN_SLIDER_START  Left edge of raise slider
            TITAN_BTN_SLIDER_END    Right edge of raise slider
            TITAN_BTN_TIMEBANK      Timebank button
            TITAN_BTN_SIT_OUT       Sit out button
            TITAN_BTN_EMOTE         Emote button
        """
        mapping = {
            "fold": "TITAN_BTN_FOLD",
            "call": "TITAN_BTN_CALL",
            "raise": "TITAN_BTN_RAISE",
            "raise_2x": "TITAN_BTN_RAISE_2X",
            "raise_2_5x": "TITAN_BTN_RAISE_2_5X",
            "raise_pot": "TITAN_BTN_RAISE_POT",
            "raise_confirm": "TITAN_BTN_RAISE_CONFIRM",
            "slider_start": "TITAN_BTN_SLIDER_START",
            "slider_end": "TITAN_BTN_SLIDER_END",
            "timebank": "TITAN_BTN_TIMEBANK",
            "sit_out": "TITAN_BTN_SIT_OUT",
            "emote": "TITAN_BTN_EMOTE",
        }
        for action_name, env_key in mapping.items():
            raw = os.getenv(env_key, "").strip()
            if "," in raw:
                parts = raw.split(",", 1)
                try:
                    self._regions[action_name] = ClickPoint(x=int(parts[0]), y=int(parts[1]))
                except ValueError:
                    pass

    def _load_regions_from_config(self) -> None:
        """Load button coordinates from config_club.yaml ``action_coordinates``.

        This provides PPPoker-specific button positions that override the
        generic defaults, ensuring clicks land on the correct screen areas.
        Config values have lower priority than env-var overrides.
        """
        try:
            from utils.titan_config import cfg
        except Exception:
            return

        # action_coordinates section: {fold: {x, y}, call: {x, y}, raise: {x, y}, ...}
        all_coord_keys = (
            "fold", "call", "raise",
            "raise_2x", "raise_2_5x", "raise_pot", "raise_confirm",
            "slider_start", "slider_end",
            "timebank", "sit_out", "emote",
        )
        for action_name in all_coord_keys:
            key = f"action_coordinates.{action_name}"
            point = cfg.get_raw(key, None)
            if isinstance(point, dict) and "x" in point and "y" in point:
                # Only override if not already set by env var
                env_key = f"TITAN_BTN_{action_name.upper()}"
                if not os.getenv(env_key, "").strip():
                    self._regions[action_name] = ClickPoint(
                        x=int(point["x"]), y=int(point["y"]),
                    )

        # action_buttons section: {fold: [x,y], call: [x,y], raise_small: [x,y], raise_big: [x,y]}
        for config_name, region_name in [
            ("fold", "fold"), ("call", "call"),
            ("raise_small", "raise"), ("raise_big", "raise"),
        ]:
            key = f"action_buttons.{config_name}"
            point = cfg.get_raw(key, None)
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                env_key = f"TITAN_BTN_{region_name.upper()}"
                if not os.getenv(env_key, "").strip():
                    self._regions[region_name] = ClickPoint(
                        x=int(point[0]), y=int(point[1]),
                    )

    def _load_input_backend_config(self) -> None:
        """Load input backend settings from config YAML.

        Supports ``emulator`` / ``mumu`` (real mouse on Win32 render surface),
        ``adb`` (shell input tap), or ``pyautogui`` (legacy).

        For the ``emulator`` backend, coordinates in ``action_coordinates``
        should already be in Android override resolution (1080×1920).
        No scaling is applied.

        For the ``adb`` backend, applies ``input.adb_coordinate_scale``
        to map 720×1280 config coords to 1080×1920 Android space.
        """
        try:
            from utils.titan_config import cfg
        except Exception:
            return

        backend = cfg.get_raw("input.backend", "")
        if backend:
            os.environ.setdefault("TITAN_INPUT_BACKEND", str(backend))

        # ADB settings (used by adb backend only)
        adb_path = cfg.get_raw("input.adb_path", "")
        if adb_path:
            os.environ.setdefault("TITAN_ADB_PATH", str(adb_path))
        adb_device = cfg.get_raw("input.adb_device", "")
        if adb_device:
            os.environ.setdefault("TITAN_ADB_DEVICE", str(adb_device))

        # Emulator render-surface settings
        android_w = cfg.get_raw("input.android_w", "")
        if android_w:
            os.environ.setdefault("TITAN_ANDROID_W", str(android_w))
        android_h = cfg.get_raw("input.android_h", "")
        if android_h:
            os.environ.setdefault("TITAN_ANDROID_H", str(android_h))

        # Scale coordinates for ADB (720→1080 = 1.5x)
        scale = cfg.get_raw("input.adb_coordinate_scale", None)
        if scale is not None:
            self._adb_scale = float(scale)
            if self._adb_scale != 1.0:
                for name, pt in self._regions.items():
                    self._regions[name] = ClickPoint(
                        x=int(pt.x * self._adb_scale),
                        y=int(pt.y * self._adb_scale),
                    )
                self._log.info(
                    f"ADB coordinate scale={self._adb_scale} applied to all regions"
                )

        # Re-create GhostMouse so it picks up the new env vars
        if str(backend).lower() in ("adb", "emulator", "mumu", "ldplayer"):
            from agent.ghost_mouse import GhostMouse  # lazy import
            self._ghost = GhostMouse(GhostMouseConfig())
