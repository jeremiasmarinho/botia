"""Service registry — dependency container for tools, agents and workflows.

The :class:`ServiceRegistry` acts as a simple dependency-injection
container so the orchestrator can look up services by name at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceRegistry:
    """Named-service container shared across the orchestrator.

    Attributes:
        tools:     Registered tool instances (keyed by name).
        agents:    Registered agent instances (keyed by name).
        workflows: Registered workflow instances (keyed by name).
        memory:    Shared memory backend (set during bootstrap).
    """

    tools: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    workflows: dict[str, Any] = field(default_factory=dict)
    memory: Any | None = None

    def register_tool(self, name: str, tool: Any) -> None:
        """Store a tool instance under *name*."""
        self.tools[name] = tool

    def register_agent(self, name: str, agent: Any) -> None:
        """Store an agent instance under *name*."""
        self.agents[name] = agent

    def register_workflow(self, name: str, workflow: Any) -> None:
        """Store a workflow instance under *name*."""
        self.workflows[name] = workflow
