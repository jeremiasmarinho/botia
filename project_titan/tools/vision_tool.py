from __future__ import annotations

from dataclasses import dataclass
import os
import re
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
        self._unknown_labels: set[str] = set()
        self._model: Any | None = None
        self._load_error: str | None = None

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
        normalized = label.strip()
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

        for pattern in hero_patterns:
            match = re.match(pattern, lowered)
            if match is not None:
                candidate = self._normalize_card_token(match.group(match.lastindex or 1))
                return ("hero", candidate, None)

        for pattern in board_patterns:
            match = re.match(pattern, lowered)
            if match is not None:
                candidate = self._normalize_card_token(match.group(match.lastindex or 1))
                return ("board", candidate, None)

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
        return TableSnapshot(hero_cards=[], board_cards=[], pot=0.0, stack=0.0)

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
            category, card_token, numeric_value = self._parse_label(item.label)

            if category == "hero" and card_token is not None:
                hero_cards.append(card_token)
                continue

            if category == "board" and card_token is not None:
                board_cards.append(card_token)
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

        return TableSnapshot(hero_cards=hero_cards, board_cards=board_cards, pot=detected_pot, stack=detected_stack)

    def read_table(self) -> TableSnapshot:
        if not self.model_path:
            return self._fallback_snapshot()

        if self._model is None:
            self._load_model()
            if self._model is None:
                return self._fallback_snapshot()

        frame = self._capture_frame()
        if frame is None:
            return self._fallback_snapshot()

        try:
            results = self._model.predict(source=frame, verbose=False)
        except Exception:
            return self._fallback_snapshot()

        if not results:
            return self._fallback_snapshot()

        return self._extract_snapshot(results[0])
