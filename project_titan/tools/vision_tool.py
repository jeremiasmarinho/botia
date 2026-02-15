from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Any


CARD_TOKEN_RANKS = set("23456789TJQKA")
CARD_TOKEN_SUITS = set("cdhs")
RANK_WORD_MAP = {
    "ace": "A",
    "king": "K",
    "queen": "Q",
    "jack": "J",
    "ten": "T",
    "t": "T",
    "nine": "9",
    "eight": "8",
    "seven": "7",
    "six": "6",
    "five": "5",
    "four": "4",
    "three": "3",
    "two": "2",
}
SUIT_WORD_MAP = {
    "hearts": "h",
    "heart": "h",
    "diamonds": "d",
    "diamond": "d",
    "clubs": "c",
    "club": "c",
    "spades": "s",
    "spade": "s",
}


@dataclass(slots=True)
class TableSnapshot:
    hero_cards: list[str]
    board_cards: list[str]
    pot: float
    stack: float
    dead_cards: list[str] = field(default_factory=list)
    current_opponent: str = ""
    active_players: int = 0
    action_points: dict[str, tuple[int, int]] = field(default_factory=dict)
    showdown_events: list[dict[str, Any]] = field(default_factory=list)
    is_my_turn: bool = False
    state_changed: bool = False


@dataclass(slots=True)
class DetectionItem:
    label: str
    confidence: float
    center_x: float
    center_y: float


class VisionTool:
    def __init__(self, model_path: str | None = None, monitor: dict[str, int] | None = None) -> None:
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
        if self._model is not None:
            return

        try:
            from ultralytics import YOLO

            self._model = YOLO(self.model_path)
        except Exception as error:
            self._model = None
            self._load_error = str(error)

    def _load_label_aliases(self) -> dict[str, str]:
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

    def _apply_alias(self, label: str) -> str:
        alias = self.label_aliases.get(label.strip().lower())
        if alias is not None:
            return alias
        return self._apply_profile_alias(label)

    def _apply_profile_alias(self, label: str) -> str:
        if self.label_profile not in {"dataset_v1", "dataset-1", "yolo_dataset_v1"}:
            return label

        normalized = label.strip().lower().replace(" ", "_")
        normalized = re.sub(r"[^a-z0-9_\-.]", "", normalized)
        normalized = normalized.replace("-", "_")
        normalized = re.sub(r"_+", "_", normalized)

        hero_match = re.match(r"^(hero|player|my)_card(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$", normalized)
        if hero_match is not None:
            card_token = self._normalize_card_token(hero_match.group(2))
            if card_token is not None:
                return f"hero_{card_token}"

        board_match = re.match(
            r"^(?:table_)?(?:board|flop|turn|river)(?:_card)?(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$",
            normalized,
        )
        if board_match is not None:
            card_token = self._normalize_card_token(board_match.group(1))
            if card_token is not None:
                return f"board_{card_token}"

        dead_match = re.match(r"^(?:burn|dead|muck|folded)(?:_card)?(?:_\d+)?_([0-9tjqka]{1,2}[cdhs])$", normalized)
        if dead_match is not None:
            card_token = self._normalize_card_token(dead_match.group(1))
            if card_token is not None:
                return f"dead_{card_token}"

        dead_tokens = {
            "burn",
            "burned",
            "dead",
            "muck",
            "mucked",
            "folded",
            "discard",
            "discarded",
            "gone",
        }
        dead_parts = [part for part in normalized.split("_") if part]
        if dead_parts and any(part in dead_tokens for part in dead_parts):
            for part in dead_parts:
                card_token = self._normalize_card_token(part)
                if card_token is not None:
                    return f"dead_{card_token}"

            full_card_token = self._normalize_card_token(normalized)
            if full_card_token is not None:
                return f"dead_{full_card_token}"

        pot_match = re.match(r"^(?:pot|pote)(?:_value)?_([0-9]+(?:\.[0-9]+)?)$", normalized)
        if pot_match is not None:
            return f"pot_{pot_match.group(1)}"

        stack_match = re.match(r"^(?:hero_stack|stack|my_stack)(?:_value)?_([0-9]+(?:\.[0-9]+)?)$", normalized)
        if stack_match is not None:
            return f"hero_stack_{stack_match.group(1)}"

        opponent_match = re.match(r"^(?:opponent|opp|villain)(?:_id)?_([a-z0-9_]+)$", normalized)
        if opponent_match is not None:
            return f"opponent_{opponent_match.group(1)}"

        showdown_match = re.match(
            r"^(?:showdown|sd|allin)_([a-z0-9_]+)_(?:eq|equity)_([0-9]+(?:\.[0-9]+)?)(p)?_(?:won|win|lost|lose)$",
            normalized,
        )
        if showdown_match is not None:
            return normalized

        return label

    @staticmethod
    def _is_card_label(label: str) -> bool:
        if len(label) != 2:
            return False
        return label[0] in CARD_TOKEN_RANKS and label[1] in CARD_TOKEN_SUITS

    @staticmethod
    def _normalize_card_token(token: str) -> str | None:
        cleaned = token.strip().replace("10", "T").replace("_", "").replace("-", "")
        if len(cleaned) < 2:
            return None

        match = re.search(r"([2-9TJQKA])([CDHScdhs])", cleaned)
        if match is None:
            return None

        rank = match.group(1).upper()
        suit = match.group(2).lower()
        normalized = f"{rank}{suit}"
        if VisionTool._is_card_label(normalized):
            return normalized

        lowered = token.strip().lower().replace("-", "_")
        chunks = [chunk for chunk in lowered.split("_") if chunk]
        if not chunks:
            return None

        rank_candidate = ""
        suit_candidate = ""
        for chunk in chunks:
            if not rank_candidate and chunk in RANK_WORD_MAP:
                rank_candidate = RANK_WORD_MAP[chunk]
            if not suit_candidate and chunk in SUIT_WORD_MAP:
                suit_candidate = SUIT_WORD_MAP[chunk]

        if rank_candidate and suit_candidate:
            word_based = f"{rank_candidate}{suit_candidate}"
            if VisionTool._is_card_label(word_based):
                return word_based

        return None

    def _parse_label(self, label: str) -> tuple[str | None, str | None, float | None]:
        normalized = self._apply_alias(label).strip()
        lowered = normalized.lower()

        direct_card = self._normalize_card_token(normalized)

        hero_patterns = [
            r"^(hero|hole|hand|player|my|pocket)[_\-]?(card)?[_\-]?(.+)$",
            r"^(h|hc)[_\-]?(\d+)?[_\-]?(.+)$",
        ]
        board_patterns = [
            r"^(board|flop|turn|river|community|table)[_\-]?(card)?[_\-]?(.+)$",
            r"^(b|bc)[_\-]?(\d+)?[_\-]?(.+)$",
        ]
        dead_patterns = [
            r"^(dead|burn|muck|folded|gone)[_\-]?(card)?[_\-]?(.+)$",
            r"^(d|dc)[_\-]?(\d+)?[_\-]?(.+)$",
        ]

        for pattern in hero_patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match is not None:
                candidate = self._normalize_card_token(match.group(match.lastindex or 1))
                return ("hero", candidate, None)

        for pattern in board_patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match is not None:
                candidate = self._normalize_card_token(match.group(match.lastindex or 1))
                return ("board", candidate, None)

        for pattern in dead_patterns:
            match = re.match(pattern, normalized, flags=re.IGNORECASE)
            if match is not None:
                candidate = self._normalize_card_token(match.group(match.lastindex or 1))
                return ("dead", candidate, None)

        if direct_card is not None:
            return ("generic_card", direct_card, None)

        pot_match = re.match(r"^(pot|pote|total_pot)[_\-]?([0-9]+(?:\.[0-9]+)?)$", lowered)
        if pot_match is not None:
            return ("pot", None, float(pot_match.group(2)))

        stack_match = re.match(r"^(stack|hero_stack|my_stack)[_\-]?([0-9]+(?:\.[0-9]+)?)$", lowered)
        if stack_match is not None:
            return ("stack", None, float(stack_match.group(2)))

        if self.debug_labels and normalized not in self._unknown_labels:
            self._unknown_labels.add(normalized)
            print(f"[VisionTool] unknown label: {normalized}")

        return (None, None, None)

    @staticmethod
    def _normalize_opponent_id(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]", "", value.strip().replace("-", "_")).lower()
        return normalized

    def _parse_opponent_label(self, label: str) -> str | None:
        normalized = self._apply_alias(label).strip().lower()

        patterns = [
            r"^(opponent|opp|villain|vilao)[_\-]?([a-zA-Z0-9_]+)$",
            r"^(opponent|opp|villain|vilao)[_\-]?(id)[_\-]?([a-zA-Z0-9_]+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, normalized)
            if match is None:
                continue
            candidate = match.group(match.lastindex or 1)
            opponent_id = self._normalize_opponent_id(candidate)
            if opponent_id:
                return opponent_id
        return None

    @staticmethod
    def _normalize_equity(value: float, has_percent_suffix: bool) -> float:
        equity = float(value)
        if has_percent_suffix or equity > 1.0:
            equity = equity / 100.0
        return min(max(equity, 0.0), 1.0)

    def _parse_showdown_label(self, label: str) -> dict[str, Any] | None:
        normalized = self._apply_alias(label).strip().lower()
        patterns = [
            r"^(showdown|sd|allin)[_\-]([a-zA-Z0-9_]+)[_\-](?:eq|equity)[_\-]?([0-9]+(?:\.[0-9]+)?)(p)?[_\-](won|win|lost|lose)$",
            r"^(showdown|sd|allin)[_\-]([a-zA-Z0-9_]+)[_\-]([0-9]+(?:\.[0-9]+)?)(p)?[_\-](won|win|lost|lose)$",
        ]

        for pattern in patterns:
            match = re.match(pattern, normalized)
            if match is None:
                continue

            opponent_id = self._normalize_opponent_id(match.group(2))
            if not opponent_id:
                continue

            raw_equity = float(match.group(3))
            has_percent_suffix = match.group(4) == "p"
            equity = self._normalize_equity(raw_equity, has_percent_suffix)
            outcome_token = match.group(5)
            won = outcome_token in {"won", "win"}

            return {
                "opponent_id": opponent_id,
                "equity": round(equity, 4),
                "won": won,
            }

        return None

    @staticmethod
    def _parse_turn_label(label: str) -> bool | None:
        normalized = label.strip().lower().replace("-", "_")
        normalized = re.sub(r"[^a-z0-9_]", "", normalized)

        positive_tokens = {
            "my_turn",
            "your_turn",
            "action_required",
            "act_now",
            "turn_on",
            "hero_turn",
            "btn_call",
            "btn_raise",
            "btn_fold",
        }
        negative_tokens = {
            "not_my_turn",
            "waiting_turn",
            "turn_off",
            "no_action",
        }

        if normalized in positive_tokens:
            return True
        if normalized in negative_tokens:
            return False
        return None

    @staticmethod
    def _parse_active_players_label(label: str) -> int | None:
        normalized = label.strip().lower().replace("-", "_")
        normalized = re.sub(r"[^a-z0-9_\.]", "", normalized)

        patterns = [
            r"^(?:active_players|players_active|player_count|players_count|table_players|alive_players)_([0-9]{1,2})$",
            r"^(?:seats|players)_([0-9]{1,2})$",
            r"^(?:active|alive)_([0-9]{1,2})$",
        ]
        for pattern in patterns:
            match = re.match(pattern, normalized)
            if match is None:
                continue
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 0 <= value <= 10:
                return value
        return None

    @staticmethod
    def _parse_action_button_label(label: str) -> str | None:
        normalized = label.strip().lower().replace("-", "_")
        normalized = re.sub(r"[^a-z0-9_]", "", normalized)

        fold_patterns = [
            r"^(?:btn_)?fold(?:_button)?$",
            r"^(?:button_)?fold$",
            r"^action_fold$",
        ]
        call_patterns = [
            r"^(?:btn_)?call(?:_button)?$",
            r"^(?:button_)?call$",
            r"^action_call$",
            r"^check_call$",
            r"^check$",
        ]
        raise_patterns = [
            r"^(?:btn_)?raise(?:_button)?$",
            r"^(?:button_)?raise$",
            r"^action_raise$",
            r"^(?:btn_)?bet(?:_button)?$",
            r"^action_bet$",
        ]
        raise_big_patterns = [
            r"^(?:btn_)?allin(?:_button)?$",
            r"^(?:button_)?allin$",
            r"^action_allin$",
            r"^raise_big$",
        ]

        for pattern in fold_patterns:
            if re.match(pattern, normalized):
                return "fold"
        for pattern in call_patterns:
            if re.match(pattern, normalized):
                return "call"
        for pattern in raise_big_patterns:
            if re.match(pattern, normalized):
                return "raise_big"
        for pattern in raise_patterns:
            if re.match(pattern, normalized):
                return "raise_small"
        return None

    @staticmethod
    def _state_signature(snapshot: TableSnapshot) -> str:
        hero_key = ",".join(snapshot.hero_cards)
        board_key = ",".join(snapshot.board_cards)
        dead_key = ",".join(snapshot.dead_cards)
        opponent_key = snapshot.current_opponent
        active_players_key = str(int(max(snapshot.active_players, 0)))
        action_points_key = "|".join(
            [
                f"{key}:{value[0]},{value[1]}"
                for key, value in sorted(snapshot.action_points.items(), key=lambda item: item[0])
            ]
        )
        turn_key = "1" if snapshot.is_my_turn else "0"
        pot_key = f"{snapshot.pot:.2f}"
        stack_key = f"{snapshot.stack:.2f}"
        return "|".join([
            hero_key,
            board_key,
            dead_key,
            opponent_key,
            active_players_key,
            action_points_key,
            turn_key,
            pot_key,
            stack_key,
        ])

    def _mark_state_change(self, snapshot: TableSnapshot) -> TableSnapshot:
        signature = self._state_signature(snapshot)
        changed = bool(self._last_state_signature) and signature != self._last_state_signature
        self._last_state_signature = signature
        return TableSnapshot(
            hero_cards=list(snapshot.hero_cards),
            board_cards=list(snapshot.board_cards),
            pot=snapshot.pot,
            stack=snapshot.stack,
            dead_cards=list(snapshot.dead_cards),
            current_opponent=snapshot.current_opponent,
            active_players=int(max(snapshot.active_players, 0)),
            action_points=dict(snapshot.action_points),
            showdown_events=list(snapshot.showdown_events),
            is_my_turn=snapshot.is_my_turn,
            state_changed=changed,
        )

    @staticmethod
    def _dedupe_cards(cards: list[str], max_size: int) -> list[str]:
        deduped: list[str] = []
        for card in cards:
            if card not in deduped:
                deduped.append(card)
            if len(deduped) >= max_size:
                break
        return deduped

    @staticmethod
    def _fallback_snapshot() -> TableSnapshot:
        return TableSnapshot(
            hero_cards=[],
            board_cards=[],
            pot=0.0,
            stack=0.0,
            dead_cards=[],
            current_opponent="",
            active_players=0,
            action_points={},
            showdown_events=[],
            is_my_turn=False,
            state_changed=False,
        )

    def _simulated_snapshot(self) -> TableSnapshot:
        scenarios: dict[str, TableSnapshot] = {
            "wait": TableSnapshot(hero_cards=[], board_cards=[], pot=0.0, stack=0.0, dead_cards=[], active_players=0, action_points={}, is_my_turn=False),
            "fold": TableSnapshot(
                hero_cards=["7c", "2d", "4h", "3s"],
                board_cards=["Kc", "Qd", "9s"],
                pot=45.0,
                stack=180.0,
                dead_cards=["Ah"],
                active_players=4,
                action_points={"fold": (600, 700), "call": (800, 700), "raise_small": (1000, 700), "raise_big": (1000, 700)},
                is_my_turn=True,
            ),
            "call": TableSnapshot(
                hero_cards=["As", "Kd", "Qh", "Js"],
                board_cards=["9c", "7d", "2s"],
                pot=40.0,
                stack=220.0,
                dead_cards=["Tc", "8h"],
                active_players=3,
                action_points={"fold": (600, 700), "call": (800, 700), "raise_small": (1000, 700), "raise_big": (1000, 700)},
                is_my_turn=True,
            ),
            "raise": TableSnapshot(
                hero_cards=["As", "Ah", "Ks", "Kh", "Qs", "Qh"],
                board_cards=["Ad", "Kd", "Qc", "Jh"],
                pot=20.0,
                stack=600.0,
                dead_cards=["2c", "2d", "2h"],
                active_players=2,
                action_points={"fold": (600, 700), "call": (800, 700), "raise_small": (1000, 700), "raise_big": (1000, 700)},
                is_my_turn=True,
            ),
        }

        if self.sim_scenario == "cycle":
            order = ["wait", "fold", "call", "raise"]
            scenario_name = order[self._sim_index % len(order)]
            self._sim_index += 1
            return scenarios[scenario_name]

        return scenarios.get(self.sim_scenario, self._fallback_snapshot())

    def _capture_frame(self) -> Any | None:
        try:
            import mss
            import numpy as np
        except Exception:
            return None

        with mss.mss() as sct:
            target = self.monitor if self.monitor is not None else sct.monitors[1]
            frame = np.array(sct.grab(target))
        return frame[:, :, :3]

    def _extract_snapshot(self, result: Any) -> TableSnapshot:
        names: dict[int, str] = getattr(result, "names", {})
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return self._fallback_snapshot()

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

        for item in sorted(items, key=lambda item: item.center_x):
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

        if generic_cards:
            if not hero_cards and not board_cards:
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

        hero_cards = self._dedupe_cards(hero_cards, max_size=6)
        board_cards = self._dedupe_cards(board_cards, max_size=5)
        dead_cards = self._dedupe_cards(dead_cards, max_size=20)

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
            dead_cards=dead_cards,
            current_opponent=current_opponent,
            active_players=inferred_active_players,
            action_points=action_points,
            showdown_events=showdown_events,
            is_my_turn=is_my_turn,
            state_changed=False,
        )

    @staticmethod
    def _bool_env(name: str, default: bool = False) -> bool:
        raw = os.getenv(name, "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def read_table_until_state_change(
        self,
        timeout_seconds: float = 1.0,
        fps: float = 30.0,
        require_my_turn: bool = False,
    ) -> TableSnapshot:
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
