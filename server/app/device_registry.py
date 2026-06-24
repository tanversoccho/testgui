"""Server-owned device config and heartbeat registry.

These APIs are intentionally separate from Oracle. They support fleet
operations such as pushing printer defaults to a device and tracking
which devices are currently alive for a load-balancer/monitoring view.
The registry is in-memory for now; it can be backed by Redis or Oracle
without changing the HTTP contract.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict

from .config import settings


DEFAULT_DEVICE_CONFIG: Dict[str, Any] = {
    "printer": {
        "ip": "192.168.1.110",
        "port": 4916,
        "proto": "TCP",
        "timeout_seconds": 2.0,
        "poll_interval_seconds": 1.0,
    },
    "qr": {
        "max_dots": 30,
        "border": 2,
        "error_correction": "L",
    },
    "message": {
        "reverse": False,
        "invert": False,
        "width": 999,
        "delay": 100,
        "height": 1,
        "printed_dots": 30,
        "trigger_times": 1,
        "gap": 0,
        "column_repeats": 1,
        "char_space": 1,
    },
    "brands": settings.brands,
}

_configs: Dict[str, Dict[str, Any]] = {}
_heartbeats: Dict[str, Dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get_config(device_id: str) -> Dict[str, Any]:
    cfg = _configs.get(device_id)
    if cfg is None:
        cfg = {
            "device_id": device_id,
            "config": deepcopy(DEFAULT_DEVICE_CONFIG),
            "updated_at": _utc_now(),
        }
        _configs[device_id] = cfg
    return deepcopy(cfg)


def patch_config(device_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = get_config(device_id)
    merged = deepcopy(current["config"])
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    cfg = {"device_id": device_id, "config": merged, "updated_at": _utc_now()}
    _configs[device_id] = cfg
    return deepcopy(cfg)


def record_heartbeat(device_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "device_id": device_id,
        "state": payload.get("state", ""),
        "fw_version": payload.get("fw_version", ""),
        "ip": payload.get("ip", ""),
        "seen_at": _utc_now(),
    }
    _heartbeats[device_id] = row
    return deepcopy(row)
