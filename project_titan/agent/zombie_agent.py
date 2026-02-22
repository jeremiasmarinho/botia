"""Zombie agent — stateless wrapper that delegates each tick to a workflow.

The :class:`ZombieAgent` holds a single :class:`SupportsStep` workflow and
calls its ``execute()`` method on every orchestrator tick.  It deliberately
keeps no internal state so the orchestrator can replace or restart agents
without side effects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from typing import Protocol

_log = logging.getLogger("titan.zombie_agent")


class SupportsStep(Protocol):
    """Structural protocol for any object that exposes an ``execute`` method."""

    def execute(self) -> Any: ...


@dataclass(slots=True)
class ZombieAgent:
    """Thin tick-driven agent that forwards every step to *workflow*."""

    workflow: SupportsStep

    def step(self) -> Any:
        """Run the workflow once and return its outcome payload.

        Catches unexpected exceptions from the workflow so a single bad
        tick never takes down the orchestrator loop.  Returns ``None`` on
        failure so the engine can simply skip the result.
        """
        try:
            return self.workflow.execute()
        except Exception as exc:
            _log.error("workflow.execute() raised %s: %s", type(exc).__name__, exc)
            return None
