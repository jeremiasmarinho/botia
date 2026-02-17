from __future__ import annotations

from typing import Any

__all__ = ["Decision", "PokerHandWorkflow"]


def __getattr__(name: str) -> Any:
	if name in {"Decision", "PokerHandWorkflow"}:
		from .poker_hand_workflow import Decision, PokerHandWorkflow

		return {"Decision": Decision, "PokerHandWorkflow": PokerHandWorkflow}[name]
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
