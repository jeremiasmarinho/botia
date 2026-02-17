from __future__ import annotations

import math
import statistics

from agent.ghost_mouse import ClickPoint, GhostMouse


def _max_perpendicular_deviation(path_points: list, start: ClickPoint, end: ClickPoint) -> float:
    x1, y1 = float(start.x), float(start.y)
    x2, y2 = float(end.x), float(end.y)
    denominator = math.hypot(y2 - y1, x2 - x1)
    if denominator <= 1e-9:
        return 0.0

    max_dev = 0.0
    for point in path_points:
        numerator = abs((y2 - y1) * point.x - (x2 - x1) * point.y + x2 * y1 - y2 * x1)
        max_dev = max(max_dev, numerator / denominator)
    return max_dev


def test_compute_path_is_not_straight_line() -> None:
    mouse = GhostMouse()
    start = ClickPoint(100, 120)
    end = ClickPoint(980, 680)

    path = mouse.compute_path(start, end)

    assert len(path) > 10

    max_dev = _max_perpendicular_deviation(path, start, end)
    assert max_dev > 3.0

    # Extra guard: step sizes should vary (non-linear acceleration profile)
    step_sizes: list[float] = []
    for idx in range(1, len(path)):
        dx = float(path[idx].x - path[idx - 1].x)
        dy = float(path[idx].y - path[idx - 1].y)
        step_sizes.append(math.hypot(dx, dy))

    assert statistics.pstdev(step_sizes) > 0.01
