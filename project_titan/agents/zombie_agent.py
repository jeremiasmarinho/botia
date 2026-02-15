"""Zombie agent — stateless wrapper that delegates each tick to a workflow.

The :class:`ZombieAgent` holds a single :class:`SupportsStep` workflow and
calls its ``execute()`` method on every orchestrator tick.  It deliberately
keeps no internal state so the orchestrator can replace or restart agents
without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SupportsStep(Protocol):
    """Structural protocol for any object that exposes an ``execute`` method."""

    def execute(self) -> str: ...


@dataclass(slots=True)
class ZombieAgent:
    """Thin tick-driven agent that forwards every step to *workflow*."""

    workflow: SupportsStep

    def step(self) -> str:
        """Run the workflow once and return its outcome string."""
        return self.workflow.execute()
