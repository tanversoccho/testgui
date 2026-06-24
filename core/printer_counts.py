"""Daily dashboard counts derived from the printer's main counter.

The printer owns the cumulative ALL count. TODAY is the difference
between the current printer counter and the first counter value seen for
the current system day. The baseline is persisted so reconnects or GUI
restarts during the same day continue counting from the same point.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Tuple

from config.user_paths import user_data_dir

_STATE_FILENAME = "printer_count_state.json"


def _state_path() -> str:
    return os.path.join(user_data_dir(), _STATE_FILENAME)


def _today_iso() -> str:
    return date.today().isoformat()


def _read_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def counts_from_printer(counter: int | None, connected: bool) -> Tuple[int, int]:
    """Return `(today, all)` for the dashboard.

    Expects the live printer main counter and connection state.
    Outputs zero/zero while disconnected. On first connection each day,
    stores the current printer counter as the day's baseline. If the
    printer counter is reset below the baseline, re-baselines to the new
    value to avoid showing a negative TODAY value.

    Example: first connect at ALL=428 -> `(0, 428)`, later ALL=431 ->
    `(3, 431)`, next system day at ALL=450 -> `(0, 450)`.
    """
    if not connected or counter is None:
        return 0, 0
    current = max(0, int(counter))
    today = _today_iso()
    state = _read_state()
    baseline = state.get("baseline")
    if state.get("date") != today or baseline is None:
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    try:
        baseline = int(baseline)
    except (TypeError, ValueError):
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    if current < baseline:
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    return max(0, current - baseline), current
