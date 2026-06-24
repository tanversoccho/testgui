"""Oracle connection pool + in-memory mock store.

Industrial-level features:
- Pool with min/max + wait timeout (returns 503 fast instead of hanging
  the device under load).
- Statement-level timeout on every borrowed connection.
- Idempotent thick-mode init.
- Pool stats surfaced via health() for monitoring.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import oracledb

from .config import settings
from .queries import HEALTH_SELECT

logger = logging.getLogger(__name__)

_pool: Optional[oracledb.ConnectionPool] = None
_pool_lock = threading.Lock()
_thick_initialized = False


def init_pool() -> None:
    """Build the Oracle pool once at startup. No-op in mock mode."""
    global _pool, _thick_initialized
    if settings.USE_MOCK_DB:
        logger.info("DB: MOCK mode (no Oracle, in-memory store).")
        return
    with _pool_lock:
        if _pool is not None:
            return
        if settings.USE_THICK_MODE and not _thick_initialized:
            kwargs: Dict[str, str] = {}
            if settings.ORACLE_INSTANT_CLIENT_PATH:
                kwargs["lib_dir"] = settings.ORACLE_INSTANT_CLIENT_PATH
            if settings.TNS_ADMIN_PATH:
                kwargs["config_dir"] = settings.TNS_ADMIN_PATH
            oracledb.init_oracle_client(**kwargs)
            _thick_initialized = True
            logger.info("Oracle Instant Client initialized (%s).", kwargs)
        dsn = oracledb.makedsn(
            settings.DB_HOST, settings.DB_PORT, service_name=settings.DB_SERVICE
        )
        _pool = oracledb.create_pool(
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            dsn=dsn,
            min=settings.DB_POOL_MIN,
            max=settings.DB_POOL_MAX,
            increment=settings.DB_POOL_INCREMENT,
            getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
            wait_timeout=settings.DB_POOL_WAIT_SECONDS * 1000,
            timeout=600,
        )
        logger.info(
            "Oracle pool ready: min=%d max=%d dsn=%s",
            settings.DB_POOL_MIN, settings.DB_POOL_MAX, dsn,
        )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close(force=True)
        except Exception as e:
            logger.warning("Pool close error: %s", e)
        _pool = None


@contextmanager
def cursor():
    """Acquire a pooled connection + cursor. Auto-commits on success,
    rolls back on exception, always releases the connection."""
    if settings.USE_MOCK_DB:
        yield None
        return
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    conn = _pool.acquire()
    try:
        conn.call_timeout = settings.DB_STMT_TIMEOUT_SECONDS * 1000
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        try: _pool.release(conn)
        except Exception: pass


def health() -> Dict[str, Any]:
    if settings.USE_MOCK_DB:
        return {
            "mode": "mock",
            "db_alive": True,
            "mock_cartons": len(MOCK_CARTONS),
            "mock_batches": len(MOCK_BATCHES),
        }
    if _pool is None:
        return {"mode": "oracle", "db_alive": False, "error": "pool not initialized"}
    try:
        with cursor() as cur:
            cur.execute(HEALTH_SELECT.sql)
            cur.fetchone()
        return {
            "mode": "oracle",
            "db_alive": True,
            "pool": {
                "opened": _pool.opened,
                "busy": _pool.busy,
                "max": _pool.max,
                "min": _pool.min,
            },
        }
    except Exception as e:
        return {"mode": "oracle", "db_alive": False, "error": str(e)}


# ---- In-memory mock store (shared, lock-protected) ----
mock_lock = threading.Lock()
MOCK_CARTONS: List[Dict[str, Any]] = []
MOCK_BATCHES: List[Dict[str, Any]] = []
MOCK_BATCH_SEQ: List[int] = [0]
