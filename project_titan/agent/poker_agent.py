"""Poker agent — main run loop with ZMQ coordination.

This is the top-level agent that ties together vision, equity, actions,
memory and the workflow.  Heavy sub-systems (calibration cache, smoothing,
config parsing) live in dedicated modules:

* :mod:`agent.agent_config` — :class:`AgentConfig` + env-var helpers.
* :mod:`agent.calibration` — action-button cache, smoothing, file persistence.

Architecture
------------
::

    ┌─────────────┐   ZMQ REQ/REP   ┌──────────────┐
    │ PokerAgent  │ ◄─────────────► │  HiveBrain   │
    │  (run loop) │                 │  (orchestr.) │
    └──────┬──────┘                 └──────────────┘
           │
    ┌──────┴──────────────────────────────────┐
    │  vision → calibration → equity          │
    │  → thresholds → action → memory persist │
    └─────────────────────────────────────────┘

Environment variables consumed
-------------------------------
See :mod:`agent.agent_config` for config-level vars and
:mod:`agent.calibration` for calibration-level vars.
"""

from __future__ import annotations

import os
import time
from typing import Any

from memory.redis_memory import RedisMemory
from agent.sanity_guard import SanityGuard
from agent.vision_mock import MockVision
from agent.vision_ocr import TitanOCR
from agent.vision_yolo import VisionYolo
from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.rng_tool import RngTool
from tools.terminator_vision import TerminatorVision
from tools.titan_hud_state import hud_state
from tools.vision_tool import VisionTool
from utils.config import AgentRuntimeConfig, OCRRuntimeConfig, VisionRuntimeConfig
from utils.logger import TitanLogger
from utils.titan_config import cfg
from workflows.poker_hand_workflow import PokerHandWorkflow

from agent.agent_config import (
    AgentConfig,
    clamp_float,
    clamp_int,
    parse_bool_env,
    parse_float_env,
    parse_int_env,
)
from agent.calibration import (
    normalized_action_points,
    persist_calibration_to_file,
    restore_calibration_from_file,
    smooth_action_points,
)

try:
    import zmq
except Exception:
    zmq = None


class PokerAgent:
    """Autonomous poker agent with ZMQ check-in and calibrated actions.

    Lifecycle:
        1. ``__init__`` — build tools, restore calibration cache.
        2. ``run()``    — enter the main loop (read → calibrate → decide → act).
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._context: Any | None = None
        self._socket: Any | None = None

        # ── Memory backend ──────────────────────────────────────────
        self.memory = RedisMemory(
            redis_url=config.redis_url,
            ttl_seconds=3600,
        )

        # ── Calibration settings (from env-vars) ───────────────────
        self._action_calibration_cache: dict[str, dict[str, tuple[int, int]]] = {}
        self._action_calibration_cache_enabled = os.getenv(
            "TITAN_ACTION_CALIBRATION_CACHE", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}

        self._action_calibration_session_id = (
            os.getenv("TITAN_ACTION_CALIBRATION_SESSION", "default").strip() or "default"
        )
        self._action_calibration_file = os.getenv(
            "TITAN_ACTION_CALIBRATION_FILE",
            os.path.join("reports", "action_calibration_cache.json"),
        ).strip()
        self._action_calibration_max_scopes = clamp_int(
            parse_int_env("TITAN_ACTION_CALIBRATION_MAX_SCOPES", 50),
            min_value=1, max_value=500,
        )

        # ── Smoothing settings ──────────────────────────────────────
        self._action_smoothing_enabled = os.getenv(
            "TITAN_ACTION_SMOOTHING", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}

        self._action_smoothing_alpha = clamp_float(
            parse_float_env("TITAN_ACTION_SMOOTHING_ALPHA", 0.35),
            min_value=0.05, max_value=1.0,
        )
        self._action_smoothing_deadzone_px = clamp_int(
            parse_int_env("TITAN_ACTION_SMOOTHING_DEADZONE_PX", 3),
            min_value=0, max_value=50,
        )

        # ── Tool construction ──────────────────────────────────────
        vision_config = VisionRuntimeConfig()
        self._use_mock_vision = bool(config.use_mock_vision)
        if self._use_mock_vision:
            self.vision = MockVision(scenario=config.mock_vision_scenario)
        else:
            self.vision = VisionTool(
                model_path=vision_config.model_path,
                monitor=vision_config.monitor_region(),
            )

        # OCR pipeline (pot / stack / call)
        self.ocr_config = OCRRuntimeConfig()

        # Auto-detect Tesseract if not configured
        _tess_cmd = self.ocr_config.tesseract_cmd or None
        if not _tess_cmd:
            import shutil
            _tess_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.isfile(_tess_default):
                _tess_cmd = _tess_default
            elif shutil.which("tesseract"):
                _tess_cmd = shutil.which("tesseract")

        self.ocr = TitanOCR(
            use_easyocr=self.ocr_config.use_easyocr,
            tesseract_cmd=_tess_cmd,
        )
        self.ocr_vision = VisionYolo(model_path=vision_config.model_path)
        # Reference Android resolution for OCR region scaling
        self._ocr_ref_w = int(os.getenv("TITAN_OCR_REF_W", "720"))
        self._ocr_ref_h = int(os.getenv("TITAN_OCR_REF_H", "1280"))
        self._ocr_last_values: dict[str, float] = {
            "pot": 0.0,
            "hero_stack": 0.0,
            "call_amount": 0.0,
        }
        self._ocr_pending_values: dict[str, tuple[float, int]] = {}
        self._ocr_confirm_frames = clamp_int(
            parse_int_env("TITAN_OCR_CONFIRM_FRAMES", 2),
            min_value=1,
            max_value=5,
        )
        self._screen_stability_threshold = clamp_float(
            parse_float_env("TITAN_SCREEN_STABILITY_THRESHOLD", 0.01),
            min_value=0.0001,
            max_value=0.50,
        )
        self._prev_ocr_frame: Any | None = None
        self.sanity_guard = SanityGuard(
            history_size=clamp_int(
                parse_int_env("TITAN_SANITY_HISTORY_SIZE", 5),
                min_value=3,
                max_value=10,
            ),
            stable_frames=clamp_int(
                parse_int_env("TITAN_SANITY_STABLE_FRAMES", 3),
                min_value=2,
                max_value=5,
            ),
        )

        self.equity = EquityTool()
        self.action = ActionTool()
        self.rng = RngTool(storage=self.memory)
        self.workflow = PokerHandWorkflow(
            self.vision, self.equity, self.action, self.memory, self.rng,
        )

        # ── Visual Overlay ("Visão do Exterminador") ───────────────
        overlay_enabled = parse_bool_env(
            "TITAN_OVERLAY_ENABLED",
            cfg.get_bool("overlay.enabled", False),
        )
        self._overlay: TerminatorVision | None = None
        if overlay_enabled:
            self._overlay = TerminatorVision(
                max_fps=cfg.get_int("overlay.max_fps", 10),
                hud_width=cfg.get_int("overlay.hud_width", 320),
                show_grid=cfg.get_bool("overlay.show_grid", False),
                grid_size=cfg.get_int("overlay.grid_size", 50),
            )

        # Restore calibration from file on startup
        self._restore_action_calibration_file_cache()

    # Card display delegated to utils.card_utils
    from utils.card_utils import card_to_pt as _card_to_pt_fn
    _card_to_pt = staticmethod(_card_to_pt_fn)

    def _log_seen_cards(self, logger: TitanLogger, cards: list[str]) -> None:
        if not cards:
            return
        spoken = [text for text in (self._card_to_pt(card) for card in cards) if text]
        if not spoken:
            return
        logger.info(f"Eu vejo um {spoken[0]}")

    # ── OCR helpers ────────────────────────────────────────────────

    @staticmethod
    def _crop_region(frame: Any, region: tuple[int, int, int, int]) -> Any | None:
        if frame is None:
            return None
        x, y, w, h = region
        try:
            frame_h, frame_w = frame.shape[:2]
        except Exception:
            return None

        x1 = max(0, min(frame_w, int(x)))
        y1 = max(0, min(frame_h, int(y)))
        x2 = max(0, min(frame_w, x1 + int(w)))
        y2 = max(0, min(frame_h, y1 + int(h)))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _read_ocr_metrics(self) -> dict[str, float]:
        """Read pot/stack/call via OCR with safe fallback semantics."""
        if not self.ocr_config.enabled:
            return dict(self._ocr_last_values)

        frame = self.ocr_vision.capture_frame()
        if frame is None:
            return dict(self._ocr_last_values)
        return self._read_ocr_metrics_from_frame(frame)

    def _read_ocr_metrics_from_frame(self, frame: Any) -> dict[str, float]:
        """Read OCR metrics from a pre-captured frame.

        If the frame dimensions differ from the reference OCR region
        coordinate system (``_ocr_ref_w`` x ``_ocr_ref_h``), regions
        are auto-scaled proportionally.
        """
        if frame is None:
            return dict(self._ocr_last_values)

        regions = self.ocr_config.regions()
        updated = dict(self._ocr_last_values)

        # Auto-scale regions if frame size differs from reference
        try:
            frame_h, frame_w = frame.shape[:2]
        except Exception:
            frame_h, frame_w = self._ocr_ref_h, self._ocr_ref_w

        sx = frame_w / self._ocr_ref_w if self._ocr_ref_w > 0 else 1.0
        sy = frame_h / self._ocr_ref_h if self._ocr_ref_h > 0 else 1.0

        for key, region in regions.items():
            x, y, w, h = region
            if abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02:
                x = int(x * sx)
                y = int(y * sy)
                w = int(w * sx)
                h = int(h * sy)
            crop = self._crop_region(frame, (x, y, w, h))
            value = self.ocr.read_numeric_region(
                crop,
                key=key,
                fallback=updated.get(key, 0.0),
            )
            updated[key] = self._sanitize_ocr_value(
                key=key,
                candidate=max(0.0, float(value)),
                previous=updated.get(key, 0.0),
            )

        self._ocr_last_values = updated
        return updated

    def _sanitize_ocr_value(self, key: str, candidate: float, previous: float) -> float:
        """Filter noisy OCR values by bounds and max-delta guards."""
        limits = self.ocr_config.value_limits()
        deltas = self.ocr_config.max_deltas()

        min_value, max_value = limits.get(key, (0.0, 1_000_000.0))
        safe_candidate = max(0.0, float(candidate))
        safe_previous = max(0.0, float(previous))

        if key == "pot" and safe_candidate <= 1.0:
            return safe_previous if safe_previous > 0 else 0.0

        if key == "hero_stack" and safe_previous >= 50.0 and safe_candidate <= 5.0:
            return safe_previous

        if safe_candidate < min_value or safe_candidate > max_value:
            return safe_previous

        max_delta = float(deltas.get(key, 0.0))
        if max_delta > 0 and safe_previous > 0:
            if abs(safe_candidate - safe_previous) > max_delta:
                pending_value, pending_count = self._ocr_pending_values.get(
                    key,
                    (safe_candidate, 0),
                )
                pending_epsilon = max(1.0, max_delta * 0.10)
                if abs(pending_value - safe_candidate) <= pending_epsilon:
                    pending_count += 1
                else:
                    pending_value = safe_candidate
                    pending_count = 1

                self._ocr_pending_values[key] = (pending_value, pending_count)
                if pending_count >= self._ocr_confirm_frames:
                    self._ocr_pending_values.pop(key, None)
                    return pending_value
                return safe_previous

        self._ocr_pending_values.pop(key, None)

        return safe_candidate

    # ── Calibration cache helpers ───────────────────────────────────

    def _cache_scope_key(self, table_id: str | None = None) -> str:
        """Build the composite scope key: ``<table_id>::<session_id>``."""
        effective_table = (
            (table_id or self.config.table_id or "table_default").strip()
            or "table_default"
        )
        return f"{effective_table}::{self._action_calibration_session_id}"

    def _restore_action_calibration_file_cache(self) -> None:
        """Load persisted action points from the JSON calibration file."""
        if not self._action_calibration_cache_enabled:
            return
        scope_key = self._cache_scope_key()
        scoped_points = restore_calibration_from_file(
            self._action_calibration_file, scope_key,
        )
        if not scoped_points:
            return
        self._action_calibration_cache[self.config.table_id] = dict(scoped_points)
        self.memory.set(
            f"action_points_cache:{self.config.table_id}", dict(scoped_points),
        )

    def _persist_action_calibration_file_cache(
        self, points: dict[str, tuple[int, int]],
    ) -> None:
        """Write calibration points to the JSON cache file."""
        if not self._action_calibration_cache_enabled:
            return
        persist_calibration_to_file(
            filepath=self._action_calibration_file,
            scope_key=self._cache_scope_key(),
            points=points,
            max_scopes=self._action_calibration_max_scopes,
        )

    def _apply_action_calibration(
        self, snapshot: Any,
    ) -> tuple[dict[str, tuple[int, int]], str]:
        """Resolve action-button coordinates from vision, cache or nothing.

        Returns:
            Tuple of ``(effective_points, source)`` where *source* is
            ``"vision"``, ``"cache"`` or ``"none"``.
        """
        table_id = self.config.table_id
        direct_points = normalized_action_points(
            getattr(snapshot, "action_points", {}),
        )

        if direct_points:
            # Fresh points from YOLO — apply smoothing and update cache.
            previous_points = self._action_calibration_cache.get(table_id, {})
            if not previous_points and self._action_calibration_cache_enabled:
                previous_points = normalized_action_points(
                    self.memory.get(f"action_points_cache:{table_id}", {}),
                )

            if self._action_smoothing_enabled:
                effective_points = smooth_action_points(
                    direct_points, previous_points,
                    alpha=self._action_smoothing_alpha,
                    deadzone=self._action_smoothing_deadzone_px,
                )
            else:
                effective_points = dict(direct_points)

            self.action.set_action_regions_from_xy(effective_points)

            if self._action_calibration_cache_enabled:
                self._action_calibration_cache[table_id] = dict(effective_points)
                self.memory.set(
                    f"action_points_cache:{table_id}", dict(effective_points),
                )
                self._persist_action_calibration_file_cache(effective_points)

            return effective_points, "vision"

        # No fresh points — try cache.
        if not self._action_calibration_cache_enabled:
            return {}, "none"

        cached_points = self._action_calibration_cache.get(table_id, {})
        if not cached_points:
            memory_cached = normalized_action_points(
                self.memory.get(f"action_points_cache:{table_id}", {}),
            )
            if memory_cached:
                cached_points = memory_cached
                self._action_calibration_cache[table_id] = dict(memory_cached)

        if cached_points:
            self.action.set_action_regions_from_xy(cached_points)
            return cached_points, "cache"

        return {}, "none"

    # ── ZMQ coordination ────────────────────────────────────────────

    def _connect(self) -> None:
        """Establish (or re-establish) the ZMQ ``REQ`` socket to HiveBrain."""
        if zmq is None:
            raise RuntimeError(
                "pyzmq nao disponivel. Instale dependencias com requirements.txt"
            )

        if self._context is None:
            self._context = zmq.Context.instance()

        if self._socket is not None:
            try:
                self._socket.close(0)
            except Exception:
                pass

        socket = self._context.socket(zmq.REQ)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, max(100, int(self.config.timeout_ms)))
        socket.setsockopt(zmq.SNDTIMEO, max(100, int(self.config.timeout_ms)))
        socket.connect(self.config.server_address)
        self._socket = socket

    # Card normalisation delegated to utils.card_utils
    from utils.card_utils import normalize_cards as _normalize_cards_fn
    _normalize_cards = staticmethod(_normalize_cards_fn)

    def _effective_active_players(self, snapshot: Any | None = None) -> int:
        """Determine active player count from snapshot → config → env-var."""
        if snapshot is not None:
            snapshot_active = getattr(snapshot, "active_players", 0)
            if isinstance(snapshot_active, int) and snapshot_active > 0:
                return snapshot_active

        if isinstance(self.config.active_players, int) and self.config.active_players > 0:
            return self.config.active_players

        opponents_raw = os.getenv("TITAN_OPPONENTS", "").strip()
        if opponents_raw.isdigit():
            opponents = max(1, min(9, int(opponents_raw)))
            return opponents + 1

        return 0

    def _checkin(self, cards: list[str], active_players: int, cycle_id: int) -> dict[str, Any]:
        """Send a check-in message to HiveBrain and return the response.

        The ZMQ ``REQ/REP`` pattern requires strict send→recv alternation.
        On timeout, the socket is recreated.
        """
        if self._socket is None:
            self._connect()

        last_decision = self.memory.get("last_decision", {})
        last_action = ""
        if isinstance(last_decision, dict):
            raw_action = last_decision.get("decision", "")
            if isinstance(raw_action, str):
                last_action = raw_action.strip().upper()

        payload = {
            "type": "checkin",
            "agent_id": self.config.agent_id,
            "table_id": self.config.table_id,
            "cycle_id": max(0, int(cycle_id)),
            "cards": self._normalize_cards(cards),
            "active_players": max(0, int(active_players)),
            "last_action": last_action,
        }

        try:
            self._socket.send_json(payload)
            response = self._socket.recv_json()
            if isinstance(response, dict):
                return response
            return {"ok": False, "error": "invalid_response"}
        except Exception:
            self._connect()
            return {"ok": False, "error": "connection_timeout"}

    def _report_decision(
        self,
        *,
        cycle_id: int,
        action: str,
        amount: float,
        target: tuple[int, int] | None,
    ) -> None:
        if self._socket is None:
            return
        payload = {
            "type": "decision",
            "agent_id": self.config.agent_id,
            "table_id": self.config.table_id,
            "cycle_id": max(0, int(cycle_id)),
            "action": str(action).strip().lower(),
            "amount": float(amount),
            "target": [int(target[0]), int(target[1])] if target is not None else None,
        }
        try:
            self._socket.send_json(payload)
            _ = self._socket.recv_json()
        except Exception:
            try:
                self._connect()
            except Exception:
                pass

    # ── Main loop ───────────────────────────────────────────────────

    def run(self) -> None:
        """Enter the main decision loop.

        Each cycle:
            1. Read the table via YOLO vision.
            2. Apply / restore action-button calibration.
            3. Check in with HiveBrain (ZMQ).
            4. Execute the poker-hand workflow.
            5. Log the outcome and sleep.
        """
        _log = TitanLogger("Agent")
        self._connect()
        _log.highlight(
            f"Agente {self.config.agent_id} conectado a {self.config.server_address} "
            f"table={self.config.table_id}  memory={self.memory.backend}"
        )

        # ── Automation toggle (F7) ─────────────────────────────────
        from agent.automation_toggle import AutomationToggle
        toggle = AutomationToggle()
        toggle.start()
        _log.info(
            f"Toggle automação: {'ON' if toggle.is_active else 'OFF'} "
            f"— pressione {toggle._hotkey} para alternar"
        )

        # ── HUD toggle bridge ──────────────────────────────────
        def _hud_toggle_cb(active: bool) -> None:
            if active:
                toggle.activate()
            else:
                toggle.deactivate()
        hud_state.set_toggle_callback(_hud_toggle_cb)
        hud_state.push(bot_active=toggle.is_active)

        # ── HUD: Painel de Controle visual ─────────────────────────
        _hud = None
        _hud_disabled = os.getenv("TITAN_HUD_DISABLED", "0").strip() in ("1", "true", "yes")
        if not _hud_disabled:
            try:
                from tools.titan_hud import TitanHUD
                _hud = TitanHUD()
                _hud.start()
                _log.success("Painel de Controle (HUD) iniciado")
            except Exception as _hud_err:
                _log.warn(f"HUD não pôde ser iniciado: {_hud_err}")

        # Inicia overlay se habilitado
        if self._overlay is not None:
            self._overlay.start()

        cycle = 0
        _toggle_log_counter = 0
        while True:
            # Skip cycle when automation is paused
            if not toggle.is_active:
                _toggle_log_counter += 1
                if _toggle_log_counter == 1 or _toggle_log_counter % 10 == 0:
                    _log.info(
                        f"Toggle automação: OFF — pressione {toggle._hotkey} para ativar "
                        f"(aguardando há {_toggle_log_counter} ciclos)"
                    )
                hud_state.push(bot_active=False)
                time.sleep(max(0.5, float(self.config.interval_seconds)))
                continue
            # Reset counter when active
            if _toggle_log_counter > 0:
                _log.highlight(f"Toggle automação: ON — iniciando ciclos de jogo")
                _toggle_log_counter = 0
            hud_state.push(bot_active=True)
            cycle_started_at = time.perf_counter()
            cycle_id = cycle + 1
            snapshot = self.vision.read_table()
            self._log_seen_cards(_log, list(getattr(snapshot, "hero_cards", [])))

            # Use mss capture first (no ADB — safe for network)
            # ADB screencap as last resort only
            current_ocr_frame = self.ocr_vision.capture_frame()
            if current_ocr_frame is None:
                # fallback to ADB if mss fails (window occluded, etc.)
                current_ocr_frame = self.ocr_vision.capture_frame_adb()

            # Overlay: atualiza frame e snapshot
            if self._overlay is not None:
                if current_ocr_frame is not None:
                    self._overlay.update_frame(current_ocr_frame)
                self._overlay.update_snapshot(snapshot)
                self._overlay.update_ocr_regions(self.ocr_config.regions())

            # HUD: push table snapshot
            hud_state.push(
                hero_cards=list(getattr(snapshot, 'hero_cards', [])),
                board_cards=list(getattr(snapshot, 'board_cards', [])),
                dead_cards=list(getattr(snapshot, 'dead_cards', [])),
                pot=float(getattr(snapshot, 'pot', 0.0)),
                stack=float(getattr(snapshot, 'stack', 0.0)),
                call_amount=float(getattr(snapshot, 'call_amount', 0.0)),
                active_players=int(getattr(snapshot, 'active_players', 0)),
                is_my_turn=bool(getattr(snapshot, 'is_my_turn', False)),
            )

            if not self._use_mock_vision:
                if current_ocr_frame is None:
                    time.sleep(max(0.1, float(self.config.interval_seconds)))
                    continue

                if self._prev_ocr_frame is not None:
                    is_stable = self.ocr_vision.check_screen_stability(
                        self._prev_ocr_frame,
                        current_ocr_frame,
                        threshold=self._screen_stability_threshold,
                    )
                    if not is_stable:
                        self._prev_ocr_frame = current_ocr_frame
                        _log.info("screen_stable=0 ocr_skipped=1")
                        time.sleep(max(0.1, float(self.config.interval_seconds)))
                        continue

                self._prev_ocr_frame = current_ocr_frame

                ocr_metrics = self._read_ocr_metrics_from_frame(current_ocr_frame)
                ocr_pot = float(ocr_metrics.get("pot", 0.0))
                ocr_stack = float(ocr_metrics.get("hero_stack", 0.0))
                ocr_call = float(ocr_metrics.get("call_amount", 0.0))

                # Diagnostic logging on first 5 cycles
                if cycle < 5:
                    fh, fw = current_ocr_frame.shape[:2]
                    _tess_ok = self.ocr._pytesseract is not None
                    _log.info(
                        f"[OCR_DIAG] frame={fw}x{fh} "
                        f"pot={ocr_pot:.2f} stack={ocr_stack:.2f} call={ocr_call:.2f} "
                        f"regions={self.ocr_config.regions()} "
                        f"tesseract_loaded={_tess_ok}"
                    )

                if not self.sanity_guard.validate(ocr_pot, ocr_stack, ocr_call):
                    _log.info(
                        "sanity_ok=0 "
                        f"reason={self.sanity_guard.last_reason} "
                        f"pot={ocr_pot:.2f} stack={ocr_stack:.2f} call={ocr_call:.2f}"
                    )
                    hud_state.push(
                        sanity_ok=False,
                        sanity_reason=self.sanity_guard.last_reason,
                    )
                    time.sleep(max(0.1, float(self.config.interval_seconds)))
                    continue

                if ocr_pot > 0:
                    snapshot.pot = ocr_pot
                if ocr_stack > 0:
                    snapshot.stack = ocr_stack
                snapshot.call_amount = ocr_call
                self.memory.set("call_amount", ocr_call)

            effective_action_points, action_calibration_source = (
                self._apply_action_calibration(snapshot)
            )

            active_players = self._effective_active_players(snapshot)
            response = self._checkin(
                cards=snapshot.hero_cards, active_players=active_players, cycle_id=cycle_id,
            )

            mode = response.get("mode", "unknown")
            partners = response.get("partners", [])
            dead_cards = response.get("dead_cards", [])
            heads_up_obfuscation = bool(response.get("heads_up_obfuscation", False))
            latency_ms = response.get("latency_ms", "-")

            self.memory.set(
                "dead_cards", dead_cards if isinstance(dead_cards, list) else [],
            )
            self.memory.set("heads_up_obfuscation", heads_up_obfuscation)

            if isinstance(snapshot.current_opponent, str) and snapshot.current_opponent.strip():
                self.memory.set("current_opponent", snapshot.current_opponent.strip())

            outcome = self.workflow.execute(
                snapshot=snapshot,
                hive_data=response,
            )

            action_target = effective_action_points.get(outcome.action)
            self._report_decision(
                cycle_id=cycle_id,
                action=outcome.action,
                amount=outcome.amount,
                target=action_target,
            )

            cycle_ms = (time.perf_counter() - cycle_started_at) * 1000.0

            # Overlay: atualiza decisão
            if self._overlay is not None:
                self._overlay.update_decision(
                    outcome.action,
                    cycle_id=cycle_id,
                    cycle_ms=cycle_ms,
                    equity=outcome.equity,
                    spr=outcome.spr,
                    street=outcome.street if hasattr(outcome, 'street') else 'preflop',
                )

            # HUD: push decision
            hud_state.push(
                action=outcome.action,
                equity=outcome.equity,
                spr=outcome.spr,
                street=getattr(outcome, 'street', 'preflop'),
                pot_odds=getattr(outcome, 'pot_odds', 0.0),
                committed=getattr(outcome, 'committed', False),
                mode=getattr(outcome, 'mode', 'SOLO'),
                opponent_class=getattr(outcome, 'opponent_class', 'Unknown'),
                gto_distribution=getattr(outcome, 'gto_distribution', {}),
                description=getattr(outcome, 'description', ''),
                cycle_id=cycle_id,
                cycle_ms=cycle_ms,
                sanity_ok=True,
                sanity_reason='ok',
            )
            hud_state.log_action(
                f"[{time.strftime('%H:%M:%S')}] #{cycle_id} {outcome.action.upper()} "
                f"| Eq {outcome.equity:.0%} | Pot {snapshot.pot:.0f} "
                f"| SPR {outcome.spr:.1f} | {outcome.mode}"
            )

            log_method = _log.highlight if mode == "squad" else _log.info
            log_method(
                f"mode={mode} partners={partners} "
                f"dead_cards={dead_cards} active_players={active_players} "
                f"hu_obf={heads_up_obfuscation} latency_ms={latency_ms} "
                f"my_turn={snapshot.is_my_turn} state_changed={snapshot.state_changed} "
                f"pot={snapshot.pot:.2f} stack={snapshot.stack:.2f} call={snapshot.call_amount:.2f} "
                f"action_points={list(effective_action_points.keys())} "
                f"action_calibration={action_calibration_source} "
                f"cycle={cycle_id} cycle_ms={cycle_ms:.2f} "
                f"decision={outcome.action} amount={outcome.amount} "
                f"equity={outcome.equity} spr={outcome.spr} "
                f"mode={outcome.mode} committed={outcome.committed} "
                f"desc={outcome.description}"
            )

            if heads_up_obfuscation:
                _log.warn("obfuscacao heads-up ativa -- forcando agressividade")

            cycle += 1
            if self.config.max_cycles is not None and cycle >= self.config.max_cycles:
                _log.success(f"max_cycles={self.config.max_cycles} atingido. parando.")
                break

            time.sleep(max(0.1, float(self.config.interval_seconds)))

        # Encerra overlay ao sair do loop
        if self._overlay is not None:
            self._overlay.stop()
        # Encerra HUD
        if _hud is not None:
            _hud.stop()


if __name__ == "__main__":
    runtime = AgentRuntimeConfig()
    max_cycles_raw = os.getenv("TITAN_AGENT_MAX_CYCLES", "").strip()
    max_cycles = int(max_cycles_raw) if max_cycles_raw.isdigit() else None
    active_players_raw = os.getenv("TITAN_ACTIVE_PLAYERS", "").strip()
    active_players = int(active_players_raw) if active_players_raw.isdigit() else None
    redis_url = os.getenv("TITAN_REDIS_URL", "redis://:titan_secret@127.0.0.1:6379/0").strip()
    use_mock_vision = parse_bool_env("TITAN_USE_MOCK_VISION", False)
    mock_vision_scenario = os.getenv("TITAN_MOCK_SCENARIO", "ALT").strip() or "ALT"

    PokerAgent(
        AgentConfig(
            agent_id=runtime.agent_id,
            server_address=runtime.zmq_server,
            table_id=runtime.table_id,
            interval_seconds=runtime.heartbeat_seconds,
            timeout_ms=runtime.timeout_ms,
            active_players=active_players,
            max_cycles=max_cycles,
            redis_url=redis_url,
            use_mock_vision=use_mock_vision,
            mock_vision_scenario=mock_vision_scenario,
        )
    ).run()
