"""Server-side LPN counter.

Format: LPN-C-{YYMMDD}{counter:06d}. The counter resets at midnight in
the server's local timezone. Atomicity:
- MOCK mode: threading.Lock + in-memory counter.
- ORACLE mode: a tiny counter table with a MERGE UPSERT — Oracle gives
  us per-row atomicity, and we hold a Python lock too so concurrent
  Python requests serialize the round-trip rather than racing.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Tuple

from . import db
from .config import settings
from . import queries

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {"date": "", "counter": 0}


def _today_iso() -> str:
    return date.today().isoformat()


def _today_tag() -> str:
    return datetime.now().strftime("%y%m%d")


def _format(counter: int) -> str:
    return f"{settings.LPN_PREFIX}{_today_tag()}{counter:0{settings.LPN_COUNTER_WIDTH}d}"


def _numeric_tail(counter: int) -> int:
    return int(_today_tag() + f"{counter:0{settings.LPN_COUNTER_WIDTH}d}")


def init_lpn_storage() -> None:
    """Create the counter table on first run. Ignored if it already exists."""
    if settings.USE_MOCK_DB:
        return
    try:
        with db.cursor() as cur:
            cur.execute(
                queries.CREATE_LPN_COUNTER_TABLE.sql.format(
                    counter_table=settings.LPN_COUNTER_TABLE
                )
            )
        logger.info("LPN counter table ready: %s", settings.LPN_COUNTER_TABLE)
    except Exception as e:
        logger.warning("LPN counter table init failed: %s", e)


def peek_next() -> Tuple[str, int]:
    today = _today_iso()
    with _lock:
        if settings.USE_MOCK_DB:
            n = _state["counter"] if _state["date"] == today else 0
            n += 1
            return _format(n), _numeric_tail(n)
        with db.cursor() as cur:
            cur.execute(
                queries.READ_LPN_COUNTER.sql.format(
                    counter_table=settings.LPN_COUNTER_TABLE
                ),
                d=today,
            )
            row = cur.fetchone()
            n = (int(row[0]) if row else 0) + 1
            return _format(n), _numeric_tail(n)


def consume_next() -> Tuple[str, int]:
    today = _today_iso()
    with _lock:
        if settings.USE_MOCK_DB:
            if _state["date"] != today:
                _state["date"] = today
                _state["counter"] = 0
            _state["counter"] += 1
            n = _state["counter"]
            return _format(n), _numeric_tail(n)
        with db.cursor() as cur:
            cur.execute(
                queries.UPSERT_INCREMENT_LPN_COUNTER.sql.format(
                    counter_table=settings.LPN_COUNTER_TABLE
                ),
                d=today,
            )
            cur.execute(
                queries.READ_LPN_COUNTER.sql.format(
                    counter_table=settings.LPN_COUNTER_TABLE
                ),
                d=today,
            )
            n = int(cur.fetchone()[0])
            return _format(n), _numeric_tail(n)
