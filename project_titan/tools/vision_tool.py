"""YOLO-based table vision — capture, infer and build snapshots.

This is the main entry-point of the vision pipeline.  The heavy lifting of
label parsing lives in :mod:`tools.vision_label_parser`; data models in
:mod:`tools.vision_models`; token constants in :mod:`tools.vision_constants`.

Public API
----------
* :class:`VisionTool`  — instantiate with an optional YOLO model path and
  call :meth:`read_table` to get a :class:`TableSnapshot`.
* :class:`TableSnapshot` — re-exported for backward compatibility.
* :class:`DetectionItem` — re-exported for backward compatibility.

Environment variables consumed by this module
----------------------------------------------
``TITAN_YOLO_MODEL``              Path to the YOLO ``.pt`` weights file.
``TITAN_VISION_DEBUG_LABELS``     Set to ``1`` to print unknown labels.
``TITAN_VISION_LABEL_PROFILE``    Label naming convention (default ``generic``).
``TITAN_VISION_LABEL_MAP_FILE``   JSON file with explicit label aliases.
``TITAN_VISION_LABEL_MAP_JSON``   Inline JSON with label aliases.
``TITAN_SIM_SCENARIO``            Simulation scenario (``off`` / ``cycle`` / ``wait`` / ``fold`` / ``call`` / ``raise``).
``TITAN_VISION_WAIT_STATE_CHANGE`` Set to ``1`` to poll until state changes.
``TITAN_VISION_CHANGE_TIMEOUT``   Polling timeout in seconds (default ``1.0``).
``TITAN_VISION_POLL_FPS``         Polling rate (default ``30``).
``TITAN_VISION_WAIT_MY_TURN``     Only return on hero's turn when polling.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# Re-export data models so downstream ``from tools.vision_tool import …``
# continues to work after the refactoring.
from tools.vision_models import DetectionItem, TableSnapshot  # noqa: F401
from tools.vision_label_parser import (
    apply_alias,
    normalize_card_token,
    parse_action_button_label,
    parse_active_players_label,
    parse_label,
    parse_opponent_label,
    parse_showdown_label,
    parse_turn_label,
)


class VisionTool:
    """Captures the poker table via screenshot → YOLO inference → snapshot.

    The tool can operate in three modes:

    1. **Live** — grabs a screenshot with ``mss``, runs the YOLO model and
       parses the detected labels into a :class:`TableSnapshot`.
    2. **Simulation** — returns hardcoded scenario snapshots for offline
       testing (controlled via ``TITAN_SIM_SCENARIO``).
    3. **Fallback** — returns an empty snapshot when no model is loaded
       or the capture fails.
    """

    # ── Construction & model loading ────────────────────────────────

    def __init__(
        self,
        model_path: str | None = None,
        monitor: dict[str, int] | None = None,
    ) -> None:
        """Initialise the vision tool.

        Args:
            model_path: Filesystem path to the YOLO ``.pt`` weights.
                        Falls back to ``TITAN_YOLO_MODEL`` env-var.
            monitor:    ``mss``-compatible monitor dict ``{"top", "left",
                        "width", "height"}`` for a specific screen region.
        """
        self.model_path = model_path or os.getenv("TITAN_YOLO_MODEL", "")
        self.monitor = monitor
        self.debug_labels = os.getenv("TITAN_VISION_DEBUG_LABELS", "0") == "1"
        self.label_profile = os.getenv("TITAN_VISION_LABEL_PROFILE", "generic").strip().lower()
        self.label_aliases = self._load_label_aliases()
        self.sim_scenario = os.getenv("TITAN_SIM_SCENARIO", "off").strip().lower()
        self._sim_index = 0
        self._unknown_labels: set[str] = set()
        self._model: Any | None = None
        self._load_error: str | None = None
        self._last_state_signature: str = ""

        if self.model_path:
            self._load_model()

    def _load_model(self) -> None:
        """Lazy-load the YOLO model (called once on first use)."""
        if self._model is not None:
            return
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)
        except Exception as error:
            self._model = None
            self._load_error = str(error)

    def _load_label_aliases(self) -> dict[str, str]:
        """Build the label alias map from env-var JSON and/or a JSON file.

        Aliases are merged in order: file-based first, then inline JSON
        (inline takes precedence on key collisions).
        """
        aliases: dict[str, str] = {}

        def add_aliases(raw_aliases: dict[Any, Any]) -> None:
            for raw_key, raw_value in raw_aliases.items():
                if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                    continue
                key = raw_key.strip().lower()
                value = raw_value.strip()
                if not key or not value:
                    continue
                aliases[key] = value

        alias_map_file = os.getenv("TITAN_VISION_LABEL_MAP_FILE", "").strip()
        if alias_map_file:
            try:
                with open(alias_map_file, "r", encoding="utf-8") as file_obj:
                    loaded = json.load(file_obj)
                if isinstance(loaded, dict):
                    add_aliases(loaded)
            except Exception as error:
                if self.debug_labels:
                    print(f"[VisionTool] label map file error: {error}")

        alias_map_json = os.getenv("TITAN_VISION_LABEL_MAP_JSON", "").strip()
        if alias_map_json:
            try:
                loaded = json.loads(alias_map_json)
                if isinstance(loaded, dict):
                    add_aliases(loaded)
            except Exception as error:
                if self.debug_labels:
                    print(f"[VisionTool] label map json error: {error}")

        return aliases

    # ── Delegate label parsing to extracted module ──────────────────
    # These thin wrappers keep the internal interface identical so that
    # tests and subclasses that override them still work.

    def _apply_alias(self, label: str) -> str:
        return apply_alias(label, self.label_aliases, self.label_profile)

    @staticmethod
    def _is_card_label(label: str) -> bool:
        from tools.vision_label_parser import is_card_label
        return is_card_label(label)

    @staticmethod
    def _normalize_card_token(token: str) -> str | None:
        return normalize_card_token(token)

    def _parse_label(self, label: str) -> tuple[str | None, str | None, float | None]:
        return parse_label(
            label,
            self.label_aliases,
            self.label_profile,
            self.debug_labels,
            self._unknown_labels,
        )

    def _parse_opponent_label(self, label: str) -> str | None:
        return parse_opponent_label(label, self.label_aliases, self.label_profile)

    def _parse_showdown_label(self, label: str) -> dict[str, Any] | None:
        return parse_showdown_label(label, self.label_aliases, self.label_profile)

    @staticmethod
    def _parse_turn_label(label: str) -> bool | None:
        return parse_turn_label(label)

    @staticmethod
    def _parse_active_players_label(label: str) -> int | None:
        return parse_active_players_label(label)

    @staticmethod
    def _parse_action_button_label(label: str) -> str | None:
        return parse_action_button_label(label)

    # ── State-change detection ──────────────────────────────────────

    @staticmethod
    def _state_signature(snapshot: TableSnapshot) -> str:
        """Build a deterministic pipe-delimited hash of the snapshot fields.

        Used by :meth:`_mark_state_change` to detect inter-frame transitions.
        """
        hero_key = ",".join(snapshot.hero_cards)
        board_key = ",".join(snapshot.board_cards)
        dead_key = ",".join(snapshot.dead_cards)
        opponent_key = snapshot.current_opponent
        active_players_key = str(int(max(snapshot.active_players, 0)))
        action_points_key = "|".join(
            f"{key}:{value[0]},{value[1]}"
            for key, value in sorted(snapshot.action_points.items(), key=lambda item: item[0])
        )
        turn_key = "1" if snapshot.is_my_turn else "0"
        pot_key = f"{snapshot.pot:.2f}"
        stack_key = f"{snapshot.stack:.2f}"
        call_key = f"{snapshot.call_amount:.2f}"
        return "|".join([
            hero_key, board_key, dead_key, opponent_key,
            active_players_key, action_points_key,
            turn_key, pot_key, stack_key, call_key,
        ])

    def _mark_state_change(self, snapshot: TableSnapshot) -> TableSnapshot:
        """Compare *snapshot* to the previous one and tag ``state_changed``."""
        signature = self._state_signature(snapshot)
        changed = bool(self._last_state_signature) and signature != self._last_state_signature
        self._last_state_signature = signature
        return TableSnapshot(
            hero_cards=list(snapshot.hero_cards),
            board_cards=list(snapshot.board_cards),
            pot=snapshot.pot,
            stack=snapshot.stack,
            call_amount=snapshot.call_amount,
            dead_cards=list(snapshot.dead_cards),
            current_opponent=snapshot.current_opponent,
            active_players=int(max(snapshot.active_players, 0)),
            action_points=dict(snapshot.action_points),
            showdown_events=list(snapshot.showdown_events),
            is_my_turn=snapshot.is_my_turn,
            state_changed=changed,
        )

    # ── Snapshot construction helpers ───────────────────────────────

    @staticmethod
    def _dedupe_cards(cards: list[str], max_size: int) -> list[str]:
        """Remove duplicate cards preserving order, capped at *max_size*."""
        deduped: list[str] = []
        for card in cards:
            if card not in deduped:
                deduped.append(card)
            if len(deduped) >= max_size:
                break
        return deduped

    @staticmethod
    def _fallback_snapshot() -> TableSnapshot:
        """Return an empty snapshot used when capture or inference fails."""
        return TableSnapshot(
            hero_cards=[], board_cards=[], pot=0.0, stack=0.0, call_amount=0.0,
            dead_cards=[], current_opponent="", active_players=0,
            action_points={}, showdown_events=[],
            is_my_turn=False, state_changed=False,
        )

    # ── Simulation scenarios ────────────────────────────────────────

    def _simulated_snapshot(self) -> TableSnapshot:
        """Return a hardcoded scenario snapshot for offline testing.

        Scenarios:
        * ``wait``  — empty table, no cards.
        * ``fold``  — weak hand, high pot, many opponents.
        * ``call``  — medium hand, moderate pot.
        * ``raise`` — monster hand, deep stack.
        * ``cycle`` — rotates through all four scenarios.
        """
        scenarios: dict[str, TableSnapshot] = {
            "wait": TableSnapshot(
                hero_cards=[], board_cards=[], pot=0.0, stack=0.0, call_amount=0.0,
                dead_cards=[], active_players=0, action_points={}, is_my_turn=False,
            ),
            "fold": TableSnapshot(
                hero_cards=["7c", "2d", "4h", "3s"],
                board_cards=["Kc", "Qd", "9s"],
                pot=45.0, stack=180.0, call_amount=12.0,
                dead_cards=["Ah"], active_players=4,
                action_points={
                    "fold": (600, 700), "call": (800, 700),
                    "raise": (1000, 700),
                    "raise_2x": (200, 750), "raise_pot": (500, 750),
                    "raise_confirm": (1000, 750),
                },
                is_my_turn=True,
            ),
            "call": TableSnapshot(
                hero_cards=["As", "Kd", "Qh", "Js"],
                board_cards=["9c", "7d", "2s"],
                pot=40.0, stack=220.0, call_amount=8.0,
                dead_cards=["Tc", "8h"], active_players=3,
                action_points={
                    "fold": (600, 700), "call": (800, 700),
                    "raise": (1000, 700),
                    "raise_2x": (200, 750), "raise_pot": (500, 750),
                    "raise_confirm": (1000, 750),
                },
                is_my_turn=True,
            ),
            "raise": TableSnapshot(
                hero_cards=["As", "Ah", "Ks", "Kh", "Qs", "Qh"],
                board_cards=["Ad", "Kd", "Qc", "Jh"],
                pot=20.0, stack=600.0, call_amount=4.0,
                dead_cards=["2c", "2d", "2h"], active_players=2,
                action_points={
                    "fold": (600, 700), "call": (800, 700),
                    "raise": (1000, 700),
                    "raise_2x": (200, 750), "raise_pot": (500, 750),
                    "raise_confirm": (1000, 750),
                },
                is_my_turn=True,
            ),
        }

        if self.sim_scenario == "cycle":
            order = ["wait", "fold", "call", "raise"]
            scenario_name = order[self._sim_index % len(order)]
            self._sim_index += 1
            return scenarios[scenario_name]

        return scenarios.get(self.sim_scenario, self._fallback_snapshot())

    # ── Screen capture ──────────────────────────────────────────────

    def _capture_frame(self) -> Any | None:
        """Grab the current screen (or monitor region) via ``mss``.

        Returns:
            A BGR numpy array or ``None`` on failure.
        """
        try:
            import mss
            import numpy as np
        except Exception:
            return None

        with mss.mss() as sct:
            target = self.monitor if self.monitor is not None else sct.monitors[1]
            frame = np.array(sct.grab(target))
        # mss captures BGRA; strip alpha channel for YOLO (expects BGR).
        return frame[:, :, :3]

    # ── YOLO result → TableSnapshot ─────────────────────────────────

    def _extract_snapshot(self, result: Any) -> TableSnapshot:
        """Convert a single YOLO inference result into a :class:`TableSnapshot`.

        The method iterates over every detected bounding box, classifies
        each label via the parsing pipeline, and accumulates hero cards,
        board cards, dead cards, action buttons, opponents, showdown events,
        pot/stack values and turn indicators.

        Generic (un-prefixed) cards are heuristically assigned to hero or
        board based on their vertical position on screen.
        """
        names: dict[int, str] = getattr(result, "names", {})
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return self._fallback_snapshot()

        # Accumulators
        hero_cards: list[str] = []
        board_cards: list[str] = []
        dead_cards: list[str] = []
        showdown_events: list[dict[str, Any]] = []
        current_opponent = ""
        opponent_ids: set[str] = set()
        explicit_active_players: int | None = None
        action_points: dict[str, tuple[int, int]] = {}
        action_confidence: dict[str, float] = {}
        is_my_turn = False
        detected_pot = 0.0
        detected_stack = 0.0

        # Unpack YOLO tensors
        cls_values = boxes.cls.tolist() if boxes.cls is not None else []
        xyxy_values = boxes.xyxy.tolist() if boxes.xyxy is not None else []
        conf_values = boxes.conf.tolist() if boxes.conf is not None else []

        items: list[DetectionItem] = []
        for idx, (cls_idx, xyxy) in enumerate(zip(cls_values, xyxy_values)):
            label = names.get(int(cls_idx), "")
            confidence = float(conf_values[idx]) if idx < len(conf_values) else 0.0
            center_x = (float(xyxy[0]) + float(xyxy[2])) / 2.0
            center_y = (float(xyxy[1]) + float(xyxy[3])) / 2.0
            items.append(DetectionItem(label=label, confidence=confidence, center_x=center_x, center_y=center_y))

        generic_cards: list[DetectionItem] = []

        # ── Classify each detection ─────────────────────────────────
        for item in sorted(items, key=lambda item: item.center_x):
            # Action button (highest-confidence duplicate wins)
            action_name = self._parse_action_button_label(item.label)
            if action_name is not None:
                previous_conf = action_confidence.get(action_name, -1.0)
                if item.confidence >= previous_conf:
                    action_points[action_name] = (int(item.center_x), int(item.center_y))
                    action_confidence[action_name] = item.confidence
                continue

            active_players_value = self._parse_active_players_label(item.label)
            if active_players_value is not None:
                explicit_active_players = active_players_value
                continue

            turn_flag = self._parse_turn_label(item.label)
            if turn_flag is not None:
                is_my_turn = turn_flag
                continue

            showdown_event = self._parse_showdown_label(item.label)
            if showdown_event is not None:
                showdown_events.append(showdown_event)
                continue

            opponent_id = self._parse_opponent_label(item.label)
            if opponent_id:
                current_opponent = opponent_id
                opponent_ids.add(opponent_id)
                continue

            category, card_token, numeric_value = self._parse_label(item.label)

            if category == "hero" and card_token is not None:
                hero_cards.append(card_token)
                continue
            if category == "board" and card_token is not None:
                board_cards.append(card_token)
                continue
            if category == "dead" and card_token is not None:
                dead_cards.append(card_token)
                continue
            if category == "pot" and numeric_value is not None:
                detected_pot = numeric_value
                continue
            if category == "stack" and numeric_value is not None:
                detected_stack = numeric_value
                continue
            if category == "generic_card" and card_token is not None:
                generic_cards.append(item)

        # ── Heuristic assignment of generic (un-prefixed) cards ─────
        if generic_cards:
            if not hero_cards and not board_cards:
                # Split by average Y position: lower half → hero, upper → board.
                y_values = [card.center_y for card in generic_cards]
                split_y = sum(y_values) / len(y_values)
                for card in sorted(generic_cards, key=lambda item: item.center_x):
                    _, card_token, _ = self._parse_label(card.label)
                    if card_token is None:
                        continue
                    if card.center_y >= split_y and len(hero_cards) < 6:
                        hero_cards.append(card_token)
                    else:
                        board_cards.append(card_token)
            else:
                for card in sorted(generic_cards, key=lambda item: item.center_x):
                    _, card_token, _ = self._parse_label(card.label)
                    if card_token is None:
                        continue
                    if len(hero_cards) < 6:
                        hero_cards.append(card_token)
                    else:
                        board_cards.append(card_token)

        # ── Deduplicate and cap card lists ──────────────────────────
        hero_cards = self._dedupe_cards(hero_cards, max_size=6)
        board_cards = self._dedupe_cards(board_cards, max_size=5)
        dead_cards = self._dedupe_cards(dead_cards, max_size=20)

        # ── Infer active player count ───────────────────────────────
        inferred_active_players = 0
        if explicit_active_players is not None:
            inferred_active_players = explicit_active_players
        elif opponent_ids:
            inferred_active_players = 1 + len(opponent_ids)
        elif hero_cards or board_cards or dead_cards or detected_pot > 0:
            inferred_active_players = 1

        return TableSnapshot(
            hero_cards=hero_cards,
            board_cards=board_cards,
            pot=detected_pot,
            stack=detected_stack,
            call_amount=0.0,
            dead_cards=dead_cards,
            current_opponent=current_opponent,
            active_players=inferred_active_players,
            action_points=action_points,
            showdown_events=showdown_events,
            is_my_turn=is_my_turn,
            state_changed=False,
        )

    # ── Environment-variable helpers ────────────────────────────────

    @staticmethod
    def _bool_env(name: str, default: bool = False) -> bool:
        """Read a boolean from env-var (truthy values: ``1/true/yes/on``)."""
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _float_env(name: str, default: float) -> float:
        """Read a float from env-var, returning *default* on failure."""
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    # ── Public API ──────────────────────────────────────────────────

    def read_table_until_state_change(
        self,
        timeout_seconds: float = 1.0,
        fps: float = 30.0,
        require_my_turn: bool = False,
    ) -> TableSnapshot:
        """Poll the table at *fps* until the state changes or *timeout* expires.

        Args:
            timeout_seconds: Maximum wall-clock time to poll.
            fps:             Target polling rate (capped at ≥ 1).
            require_my_turn: When ``True``, only return early if it is the
                             hero's turn.

        Returns:
            The latest :class:`TableSnapshot` (with ``state_changed`` set
            when a transition was detected).
        """
        interval = 1.0 / max(fps, 1.0)
        deadline = time.perf_counter() + max(timeout_seconds, 0.0)
        latest = self._read_table_once()

        while time.perf_counter() < deadline:
            if latest.state_changed and (not require_my_turn or latest.is_my_turn):
                return latest
            time.sleep(interval)
            latest = self._read_table_once()

        return latest

    def _read_table_once(self) -> TableSnapshot:
        """Single-shot table read: simulation → model → fallback."""
        if self.sim_scenario != "off":
            return self._mark_state_change(self._simulated_snapshot())

        if not self.model_path:
            return self._mark_state_change(self._fallback_snapshot())

        if self._model is None:
            self._load_model()
            if self._model is None:
                return self._mark_state_change(self._fallback_snapshot())

        frame = self._capture_frame()
        if frame is None:
            return self._mark_state_change(self._fallback_snapshot())

        try:
            results = self._model.predict(source=frame, verbose=False)
        except Exception:
            return self._mark_state_change(self._fallback_snapshot())

        if not results:
            return self._mark_state_change(self._fallback_snapshot())

        return self._mark_state_change(self._extract_snapshot(results[0]))

    def read_table(self) -> TableSnapshot:
        """Read the current table state (main entry-point).

        Respects ``TITAN_VISION_WAIT_STATE_CHANGE`` to decide between
        a single snapshot and a polling loop.
        """
        wait_state_change = self._bool_env("TITAN_VISION_WAIT_STATE_CHANGE", default=False)
        if wait_state_change:
            timeout_seconds = self._float_env("TITAN_VISION_CHANGE_TIMEOUT", default=1.0)
            fps = self._float_env("TITAN_VISION_POLL_FPS", default=30.0)
            require_turn = self._bool_env("TITAN_VISION_WAIT_MY_TURN", default=False)
            return self.read_table_until_state_change(
                timeout_seconds=timeout_seconds,
                fps=fps,
                require_my_turn=require_turn,
            )
        return self._read_table_once()
