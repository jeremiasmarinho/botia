from __future__ import annotations

from orchestrator.engine import Orchestrator


def main() -> int:
    orchestrator = Orchestrator()
    orchestrator.bootstrap()

    agent = orchestrator.registry.agents.get("zombie_01")
    if agent is None:
        print("[Healthcheck] FAIL: zombie_01 not registered")
        return 1

    outcome = agent.step()
    print(f"[Healthcheck] OK memory_backend={orchestrator.registry.memory.backend}")
    print(f"[Healthcheck] OK first_outcome={outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
