from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceRegistry:
    tools: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    workflows: dict[str, Any] = field(default_factory=dict)
    memory: Any | None = None

    def register_tool(self, name: str, tool: Any) -> None:
        self.tools[name] = tool

    def register_agent(self, name: str, agent: Any) -> None:
        self.agents[name] = agent

    def register_workflow(self, name: str, workflow: Any) -> None:
        self.workflows[name] = workflow
