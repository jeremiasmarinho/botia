from __future__ import annotations

from random import uniform


class ActionTool:
    def act(self, action: str) -> str:
        delay = uniform(0.8, 1.5)
        return f"action={action} delay={delay:.2f}s"
