from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from memory.redis_memory import RedisMemory
from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.rng_tool import RngTool
from tools.vision_tool import VisionTool
from utils.config import AgentRuntimeConfig, VisionRuntimeConfig
from utils.logger import TitanLogger
from workflows.poker_hand_workflow import PokerHandWorkflow

try:
    import zmq
except Exception:
    zmq = None


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    server_address: str
    table_id: str = "table_default"
    interval_seconds: float = 1.0
    timeout_ms: int = 1500
    active_players: int | None = None
    max_cycles: int | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"


class PokerAgent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._context: Any | None = None
        self._socket: Any | None = None
        self.memory = RedisMemory(
            redis_url=config.redis_url,
            ttl_seconds=3600,
        )
        self._action_calibration_cache: dict[str, dict[str, tuple[int, int]]] = {}
        self._action_calibration_cache_enabled = os.getenv("TITAN_ACTION_CALIBRATION_CACHE", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._action_calibration_session_id = os.getenv("TITAN_ACTION_CALIBRATION_SESSION", "default").strip() or "default"
        self._action_calibration_file = os.getenv(
            "TITAN_ACTION_CALIBRATION_FILE",
            os.path.join("reports", "action_calibration_cache.json"),
        ).strip()
        self._action_calibration_max_scopes = self._clamp_int(
            self._parse_int_env("TITAN_ACTION_CALIBRATION_MAX_SCOPES", 50),
            min_value=1,
            max_value=500,
        )
        self._action_smoothing_enabled = os.getenv("TITAN_ACTION_SMOOTHING", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._action_smoothing_alpha = self._clamp_float(
            self._parse_float_env("TITAN_ACTION_SMOOTHING_ALPHA", 0.35),
            min_value=0.05,
            max_value=1.0,
        )
        self._action_smoothing_deadzone_px = self._clamp_int(
            self._parse_int_env("TITAN_ACTION_SMOOTHING_DEADZONE_PX", 3),
            min_value=0,
            max_value=50,
        )

        vision_config = VisionRuntimeConfig()
        self.vision = VisionTool(
            model_path=vision_config.model_path,
            monitor=vision_config.monitor_region(),
        )
        self.equity = EquityTool()
        self.action = ActionTool()
        self.rng = RngTool(storage=self.memory)
        self.workflow = PokerHandWorkflow(self.vision, self.equity, self.action, self.memory, self.rng)
        self._restore_action_calibration_file_cache()

    def _cache_scope_key(self, table_id: str | None = None) -> str:
        effective_table = (table_id or self.config.table_id or "table_default").strip() or "table_default"
        return f"{effective_table}::{self._action_calibration_session_id}"

    def _restore_action_calibration_file_cache(self) -> None:
        if not self._action_calibration_cache_enabled:
            return
        if not self._action_calibration_file:
            return
        if not os.path.exists(self._action_calibration_file):
            return

        try:
            with open(self._action_calibration_file, "r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except Exception:
            return

        if not isinstance(payload, dict):
            return

        scopes = payload.get("scopes", {})
        if not isinstance(scopes, dict):
            return

        scope_key = self._cache_scope_key()
        scope_payload = scopes.get(scope_key, {})
        scope_points = scope_payload.get("points", {}) if isinstance(scope_payload, dict) else scope_payload
        scoped_points = self._normalized_action_points(scope_points)
        if not scoped_points:
            return

        self._action_calibration_cache[self.config.table_id] = dict(scoped_points)
        self.memory.set(f"action_points_cache:{self.config.table_id}", dict(scoped_points))

    def _persist_action_calibration_file_cache(self, points: dict[str, tuple[int, int]]) -> None:
        if not self._action_calibration_cache_enabled:
            return
        if not self._action_calibration_file:
            return
        normalized_points = self._normalized_action_points(points)
        if not normalized_points:
            return

        existing_payload: dict[str, Any] = {}
        if os.path.exists(self._action_calibration_file):
            try:
                with open(self._action_calibration_file, "r", encoding="utf-8") as cache_file:
                    loaded = json.load(cache_file)
                if isinstance(loaded, dict):
                    existing_payload = loaded
            except Exception:
                existing_payload = {}

        scopes = existing_payload.get("scopes", {})
        if not isinstance(scopes, dict):
            scopes = {}

        scope_key = self._cache_scope_key()
        scopes[scope_key] = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "points": {
                action_name: [int(xy[0]), int(xy[1])] for action_name, xy in normalized_points.items()
            },
        }

        scopes = self._prune_scope_entries(scopes)

        payload: dict[str, Any] = {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scopes": scopes,
        }

        target_dir = os.path.dirname(self._action_calibration_file)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        temp_file = f"{self._action_calibration_file}.tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as cache_file:
                json.dump(payload, cache_file, ensure_ascii=False, indent=2)
            os.replace(temp_file, self._action_calibration_file)
        except Exception:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass

    def _prune_scope_entries(self, scopes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(scopes, dict):
            return {}

        if len(scopes) <= self._action_calibration_max_scopes:
            return scopes

        sortable: list[tuple[str, str]] = []
        for scope_key, scope_payload in scopes.items():
            if not isinstance(scope_key, str):
                continue
            updated_at = ""
            if isinstance(scope_payload, dict):
                raw_updated = scope_payload.get("updated_at", "")
                if isinstance(raw_updated, str):
                    updated_at = raw_updated
            sortable.append((scope_key, updated_at))

        sortable.sort(key=lambda item: item[1], reverse=True)
        keep_keys = {scope_key for scope_key, _ in sortable[: self._action_calibration_max_scopes]}

        return {
            scope_key: scope_payload
            for scope_key, scope_payload in scopes.items()
            if isinstance(scope_key, str) and scope_key in keep_keys
        }

    @staticmethod
    def _normalized_action_points(raw_points: Any) -> dict[str, tuple[int, int]]:
        if not isinstance(raw_points, dict):
            return {}

        normalized: dict[str, tuple[int, int]] = {}
        for raw_action, raw_point in raw_points.items():
            if not isinstance(raw_action, str):
                continue
            action = raw_action.strip().lower()
            if action not in {"fold", "call", "raise_small", "raise_big"}:
                continue

            if not isinstance(raw_point, (tuple, list)) or len(raw_point) != 2:
                continue
            x_raw, y_raw = raw_point
            if not isinstance(x_raw, int) or not isinstance(y_raw, int):
                continue

            normalized[action] = (x_raw, y_raw)

        return normalized

    @staticmethod
    def _parse_float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @staticmethod
    def _parse_int_env(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        if raw.lstrip("-").isdigit():
            try:
                return int(raw)
            except ValueError:
                return default
        return default

    @staticmethod
    def _clamp_float(value: float, min_value: float, max_value: float) -> float:
        return max(min_value, min(max_value, value))

    @staticmethod
    def _clamp_int(value: int, min_value: int, max_value: int) -> int:
        return max(min_value, min(max_value, value))

    def _smooth_action_points(
        self,
        current_points: dict[str, tuple[int, int]],
        previous_points: dict[str, tuple[int, int]],
    ) -> dict[str, tuple[int, int]]:
        if not self._action_smoothing_enabled:
            return dict(current_points)

        if not previous_points:
            return dict(current_points)

        alpha = self._action_smoothing_alpha
        deadzone = self._action_smoothing_deadzone_px
        smoothed: dict[str, tuple[int, int]] = {}

        for action_name, (current_x, current_y) in current_points.items():
            previous_point = previous_points.get(action_name)
            if previous_point is None:
                smoothed[action_name] = (current_x, current_y)
                continue

            previous_x, previous_y = previous_point
            delta_x = current_x - previous_x
            delta_y = current_y - previous_y
            if abs(delta_x) <= deadzone and abs(delta_y) <= deadzone:
                smoothed[action_name] = (previous_x, previous_y)
                continue

            blended_x = int(round(previous_x + (delta_x * alpha)))
            blended_y = int(round(previous_y + (delta_y * alpha)))
            smoothed[action_name] = (blended_x, blended_y)

        return smoothed

    def _apply_action_calibration(self, snapshot: Any) -> tuple[dict[str, tuple[int, int]], str]:
        table_id = self.config.table_id
        direct_points = self._normalized_action_points(getattr(snapshot, "action_points", {}))

        if direct_points:
            previous_points = self._action_calibration_cache.get(table_id, {})
            if not previous_points and self._action_calibration_cache_enabled:
                previous_points = self._normalized_action_points(self.memory.get(f"action_points_cache:{table_id}", {}))

            effective_points = self._smooth_action_points(direct_points, previous_points)
            self.action.set_action_regions_from_xy(effective_points)
            if self._action_calibration_cache_enabled:
                self._action_calibration_cache[table_id] = dict(effective_points)
                self.memory.set(f"action_points_cache:{table_id}", dict(effective_points))
                self._persist_action_calibration_file_cache(effective_points)
            return effective_points, "vision"

        if not self._action_calibration_cache_enabled:
            return {}, "none"

        cached_points = self._action_calibration_cache.get(table_id, {})
        if not cached_points:
            memory_cached = self._normalized_action_points(self.memory.get(f"action_points_cache:{table_id}", {}))
            if memory_cached:
                cached_points = memory_cached
                self._action_calibration_cache[table_id] = dict(memory_cached)

        if cached_points:
            self.action.set_action_regions_from_xy(cached_points)
            return cached_points, "cache"

        return {}, "none"

    def _connect(self) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq não disponível. Instale dependências com requirements.txt")

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

    @staticmethod
    def _normalize_cards(cards: list[str]) -> list[str]:
        normalized: list[str] = []
        for card in cards:
            if not isinstance(card, str):
                continue
            token = card.strip().upper().replace("10", "T")
            if len(token) != 2:
                continue
            rank = token[0]
            suit = token[1].lower()
            if rank not in "23456789TJQKA" or suit not in "cdhs":
                continue
            card_token = f"{rank}{suit}"
            if card_token not in normalized:
                normalized.append(card_token)
        return normalized

    def _effective_active_players(self, snapshot: Any | None = None) -> int:
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

    def _checkin(self, cards: list[str], active_players: int) -> dict[str, Any]:
        if self._socket is None:
            self._connect()

        payload = {
            "type": "checkin",
            "agent_id": self.config.agent_id,
            "table_id": self.config.table_id,
            "cards": self._normalize_cards(cards),
            "active_players": max(0, int(active_players)),
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

    def run(self) -> None:
        _log = TitanLogger("Agent")
        self._connect()
        _log.highlight(
            f"Agente {self.config.agent_id} conectado a {self.config.server_address} "
            f"table={self.config.table_id}  memory={self.memory.backend}"
        )

        cycle = 0
        while True:
            snapshot = self.vision.read_table()
            effective_action_points, action_calibration_source = self._apply_action_calibration(snapshot)

            active_players = self._effective_active_players(snapshot)
            response = self._checkin(cards=snapshot.hero_cards, active_players=active_players)

            mode = response.get("mode", "unknown")
            partners = response.get("partners", [])
            dead_cards = response.get("dead_cards", [])
            heads_up_obfuscation = bool(response.get("heads_up_obfuscation", False))
            latency_ms = response.get("latency_ms", "-")

            self.memory.set("dead_cards", dead_cards if isinstance(dead_cards, list) else [])
            self.memory.set("heads_up_obfuscation", heads_up_obfuscation)

            if isinstance(snapshot.current_opponent, str) and snapshot.current_opponent.strip():
                self.memory.set("current_opponent", snapshot.current_opponent.strip())

            outcome = self.workflow.execute(snapshot=snapshot)

            log_method = _log.highlight if mode == "squad" else _log.info
            log_method(
                f"mode={mode} partners={partners} "
                f"dead_cards={dead_cards} active_players={active_players} hu_obf={heads_up_obfuscation} latency_ms={latency_ms} "
                f"my_turn={snapshot.is_my_turn} state_changed={snapshot.state_changed} "
                f"action_points={list(effective_action_points.keys())} action_calibration={action_calibration_source} outcome={outcome}"
            )

            if heads_up_obfuscation:
                _log.warn(f"obfuscacao heads-up ativa -- forcando agressividade")

            cycle += 1
            if self.config.max_cycles is not None and cycle >= self.config.max_cycles:
                _log.success(f"max_cycles={self.config.max_cycles} atingido. parando.")
                break

            time.sleep(max(0.1, float(self.config.interval_seconds)))


if __name__ == "__main__":
    runtime = AgentRuntimeConfig()
    max_cycles_raw = os.getenv("TITAN_AGENT_MAX_CYCLES", "").strip()
    max_cycles = int(max_cycles_raw) if max_cycles_raw.isdigit() else None
    active_players_raw = os.getenv("TITAN_ACTIVE_PLAYERS", "").strip()
    active_players = int(active_players_raw) if active_players_raw.isdigit() else None
    redis_url = os.getenv("TITAN_REDIS_URL", "redis://127.0.0.1:6379/0").strip()

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
        )
    ).run()
