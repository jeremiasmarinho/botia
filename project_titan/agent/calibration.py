"""Action-button calibration cache and coordinate smoothing.

Persists and restores the screen positions of action buttons (fold, call,
raise, raise_2x, raise_2_5x, raise_pot, raise_confirm) so the agent does
not need YOLO to re-detect them every cycle.  Smoothing (EMA with deadzone) reduces jitter from noisy
bounding-box centres.

Storage layers (in priority order):
1. **In-memory dict** — fastest, per-process.
2. **Redis** — shared across agents on the same machine.
3. **JSON file** — survives restarts (``reports/action_calibration_cache.json``).

Environment variables
---------------------
``TITAN_ACTION_CALIBRATION_CACHE``     ``1`` to enable (default on).
``TITAN_ACTION_CALIBRATION_SESSION``   Session key inside the JSON file.
``TITAN_ACTION_CALIBRATION_FILE``      Path to the JSON cache file.
``TITAN_ACTION_CALIBRATION_MAX_SCOPES`` Max scope entries before pruning.
``TITAN_ACTION_SMOOTHING``             ``1`` to enable EMA smoothing.
``TITAN_ACTION_SMOOTHING_ALPHA``       EMA blending factor (0.05–1.0).
``TITAN_ACTION_SMOOTHING_DEADZONE_PX`` Pixel deadzone below which no update.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


# ── Point validation ────────────────────────────────────────────────────────

def normalized_action_points(raw_points: Any) -> dict[str, tuple[int, int]]:
    """Validate and normalise raw action-point coordinates.

    Only keeps entries whose action name is one of ``fold``, ``call``,
    ``raise``, ``raise_2x``, ``raise_2_5x``, ``raise_pot``,
    ``raise_confirm`` and whose value is a 2-int tuple/list.

    Args:
        raw_points: Candidate mapping (usually from JSON or Redis).

    Returns:
        Clean ``{action_name: (x, y)}`` dict.
    """
    if not isinstance(raw_points, dict):
        return {}

    normalized: dict[str, tuple[int, int]] = {}
    for raw_action, raw_point in raw_points.items():
        if not isinstance(raw_action, str):
            continue
        action = raw_action.strip().lower()
        if action not in {"fold", "call", "raise", "raise_2x", "raise_2_5x", "raise_pot", "raise_confirm"}:
            continue
        if not isinstance(raw_point, (tuple, list)) or len(raw_point) != 2:
            continue
        x_raw, y_raw = raw_point
        if not isinstance(x_raw, int) or not isinstance(y_raw, int):
            continue
        normalized[action] = (x_raw, y_raw)

    return normalized


# ── Smoothing ───────────────────────────────────────────────────────────────

def smooth_action_points(
    current_points: dict[str, tuple[int, int]],
    previous_points: dict[str, tuple[int, int]],
    alpha: float,
    deadzone: int,
) -> dict[str, tuple[int, int]]:
    """Apply exponential moving average (EMA) smoothing to button positions.

    For each action, the new position is blended with the previous one::

        blended = previous + (current - previous) * alpha

    If the delta is within ``deadzone`` pixels on both axes, the previous
    position is kept unchanged to avoid sub-pixel jitter.

    Args:
        current_points:  Fresh coordinates from YOLO this frame.
        previous_points: Coordinates from the last frame/cache.
        alpha:           EMA blending factor (1.0 = no smoothing).
        deadzone:        Pixel threshold below which movement is ignored.

    Returns:
        Smoothed ``{action: (x, y)}`` dict.
    """
    if not previous_points:
        return dict(current_points)

    smoothed: dict[str, tuple[int, int]] = {}

    for action_name, (current_x, current_y) in current_points.items():
        previous_point = previous_points.get(action_name)
        if previous_point is None:
            smoothed[action_name] = (current_x, current_y)
            continue

        previous_x, previous_y = previous_point
        delta_x = current_x - previous_x
        delta_y = current_y - previous_y

        # Inside deadzone → no update (suppress jitter).
        if abs(delta_x) <= deadzone and abs(delta_y) <= deadzone:
            smoothed[action_name] = (previous_x, previous_y)
            continue

        # EMA blend
        blended_x = int(round(previous_x + (delta_x * alpha)))
        blended_y = int(round(previous_y + (delta_y * alpha)))
        smoothed[action_name] = (blended_x, blended_y)

    return smoothed


# ── File-based cache ────────────────────────────────────────────────────────

def prune_scope_entries(
    scopes: dict[str, Any],
    max_scopes: int,
) -> dict[str, Any]:
    """Keep only the *max_scopes* most-recently-updated scope entries.

    Each scope entry is expected to carry an ``updated_at`` ISO timestamp
    used for sorting.
    """
    if not isinstance(scopes, dict):
        return {}
    if len(scopes) <= max_scopes:
        return scopes

    sortable: list[tuple[str, str]] = []
    for scope_key, scope_payload in scopes.items():
        if not isinstance(scope_key, str):
            continue
        updated_at = ""
        if isinstance(scope_payload, dict):
            raw_updated = scope_payload.get("updated_at", "")
            if isinstance(raw_updated, str):
                updated_at = raw_updated
        sortable.append((scope_key, updated_at))

    sortable.sort(key=lambda item: item[1], reverse=True)
    keep_keys = {k for k, _ in sortable[:max_scopes]}

    return {
        k: v for k, v in scopes.items()
        if isinstance(k, str) and k in keep_keys
    }


def restore_calibration_from_file(
    filepath: str,
    scope_key: str,
) -> dict[str, tuple[int, int]]:
    """Load cached action points from the JSON calibration file.

    Args:
        filepath:  Absolute or relative path to the JSON cache.
        scope_key: ``<table_id>::<session_id>`` key inside ``scopes``.

    Returns:
        Normalised action points, or empty dict on any failure.
    """
    if not filepath or not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    scopes = payload.get("scopes", {})
    if not isinstance(scopes, dict):
        return {}

    scope_payload = scopes.get(scope_key, {})
    scope_points = (
        scope_payload.get("points", {})
        if isinstance(scope_payload, dict)
        else scope_payload
    )
    return normalized_action_points(scope_points)


def persist_calibration_to_file(
    filepath: str,
    scope_key: str,
    points: dict[str, tuple[int, int]],
    max_scopes: int,
) -> None:
    """Atomically write calibration points to the JSON cache file.

    Uses a write-to-temp + ``os.replace`` strategy for crash safety.

    Args:
        filepath:   Target JSON file path.
        scope_key:  ``<table_id>::<session_id>`` key.
        points:     Action points to persist.
        max_scopes: Maximum scope entries before old ones are pruned.
    """
    norm_points = normalized_action_points(points)
    if not norm_points:
        return

    existing_payload: dict[str, Any] = {}
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing_payload = loaded
        except Exception:
            existing_payload = {}

    scopes = existing_payload.get("scopes", {})
    if not isinstance(scopes, dict):
        scopes = {}

    scopes[scope_key] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "points": {
            action: [int(xy[0]), int(xy[1])]
            for action, xy in norm_points.items()
        },
    }

    scopes = prune_scope_entries(scopes, max_scopes)

    payload: dict[str, Any] = {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scopes": scopes,
    }

    target_dir = os.path.dirname(filepath)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    temp_file = f"{filepath}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, filepath)
    except Exception:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass
