from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.vision_tool import VisionTool


class SupportsMemory(Protocol):
    def set(self, key: str, value: Any) -> None: ...

    def get(self, key: str, default: Any = None) -> Any: ...


@dataclass(slots=True)
class PokerHandWorkflow:
    vision: VisionTool
    equity: EquityTool
    action: ActionTool
    memory: SupportsMemory

    def execute(self) -> str:
        snapshot = self.vision.read_table()
        dead_cards = self.memory.get("dead_cards", [])
        if not isinstance(dead_cards, list):
            dead_cards = []

        estimate = self.equity.estimate(snapshot.hero_cards, snapshot.board_cards, dead_cards=dead_cards)
        decision = "fold" if estimate.win_rate < 0.35 else "call"
        result = self.action.act(decision)
        self.memory.set(
            "last_decision",
            {
                "hero_cards": snapshot.hero_cards,
                "board_cards": snapshot.board_cards,
                "win_rate": estimate.win_rate,
                "tie_rate": estimate.tie_rate,
                "decision": decision,
            },
        )
        return f"win_rate={estimate.win_rate:.2f} {result}"
