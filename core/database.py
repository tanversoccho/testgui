"""Database layer for APPS.XXFG_CARTON_MASTER.

In production, points at Oracle via python-oracledb. When credentials
are not yet configured, the layer keeps records in an in-memory list
so the GUI is fully usable on the shop floor (records persist for the
session; nothing is ever fabricated).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
import os
import threading
from urllib import parse

from config import config_app
from core import api_client

try:
    import oracledb  # type: ignore
except Exception:  # pragma: no cover
    oracledb = None


# ---------- Instant Client (thick mode) ----------
_THICK_INIT_LOCK = threading.Lock()
_THICK_INITIALIZED = False
_THICK_INIT_ERROR: Optional[str] = None


def _project_root() -> str:
    """Project directory (parent of core/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _auto_detect_instant_client() -> str:
    """Look for a bundled instantclient_* folder next to main.py."""
    root = _project_root()
    try:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if (
                os.path.isdir(full)
                and name.lower().startswith("instantclient")
                and os.path.exists(os.path.join(full, "oci.dll"))
            ):
                return full
    except OSError:
        pass
    return ""


def _resolve_instant_client_path() -> str:
    """Effective Instant Client path: user setting → bundled folder."""
    if config_app.ORACLE_INSTANT_CLIENT_PATH:
        return config_app.ORACLE_INSTANT_CLIENT_PATH
    return _auto_detect_instant_client()


def _resolve_tns_admin() -> str:
    """Effective TNS_ADMIN: user setting → <instant client>/network/admin."""
    if config_app.TNS_ADMIN_PATH:
        return config_app.TNS_ADMIN_PATH
    ic = _resolve_instant_client_path()
    if ic:
        candidate = os.path.join(ic, "network", "admin")
        if os.path.isdir(candidate):
            return candidate
    return ""


def _ensure_thick_initialized() -> tuple[bool, str]:
    """Idempotent init_oracle_client(). Returns (ok, message)."""
    if api_client.enabled():
        return True, "Server API mode; Oracle Instant Client is owned by the server."
    global _THICK_INITIALIZED, _THICK_INIT_ERROR
    if not config_app.USE_THICK_MODE:
        return False, "Thick mode disabled."
    if oracledb is None:
        return False, "oracledb package not installed."
    with _THICK_INIT_LOCK:
        if _THICK_INITIALIZED:
            return True, "Instant Client already initialised."
        ic_path = _resolve_instant_client_path()
        tns_dir = _resolve_tns_admin()
        if not ic_path:
            _THICK_INIT_ERROR = "No Instant Client folder found (looked for instantclient_* next to main.py)."
            return False, _THICK_INIT_ERROR
        try:
            kwargs = {"lib_dir": ic_path}
            if tns_dir:
                kwargs["config_dir"] = tns_dir
            oracledb.init_oracle_client(**kwargs)
            _THICK_INITIALIZED = True
            _THICK_INIT_ERROR = None
            return True, f"Instant Client initialised from {ic_path}."
        except Exception as e:
            _THICK_INIT_ERROR = f"{type(e).__name__}: {e}"
            return False, _THICK_INIT_ERROR


def current_mode() -> str:
    """Return 'Thick (<version>)' / 'Thin (<version>)' / 'unavailable'."""
    if api_client.enabled():
        return f"Server API ({getattr(config_app, 'SERVER_API_BASE_URL', '')})"
    if oracledb is None:
        return "unavailable"
    version = getattr(oracledb, "__version__", "?")
    if _THICK_INITIALIZED:
        try:
            client_v = oracledb.clientversion()
            return f"Thick (oracledb {version}; Instant Client {'.'.join(str(n) for n in client_v[:3])})"
        except Exception:
            return f"Thick (oracledb {version})"
    return f"Thin (oracledb {version})"


# Columns of XXFG_CARTON_MASTER that the GUI populates. Other columns
# (DELIVERY_ID, TRANSACTION_ID, etc.) are reserved for downstream WMS
# integration and stay NULL. CARTON_ID is NOT NULL PRIMARY KEY but has
# no Oracle SEQUENCE backing it (same pattern as XXFG_PALLET_MASTER) —
# `insert_carton` computes `MAX(CARTON_ID)+1` and injects it before INSERT.
INSERTED_COLUMNS = (
    "CARTON_ID",
    "CARTON_CODE", "QR_CODE", "ORGANIZATION_ID", "PLANT_CODE",
    "INVENTORY_ITEM_ID",
    "ITEM_CODE", "ITEM_DESC", "LOT_NUMBER", "CARTON_QTY", "UOM_CODE",
    "LPN_ID", "LPN_CONTEXT",
    "BATCH_NO", "BATCH_DATE", "LOT_NO",
    "BRAND", "GRADE", "SHIFT", "BATCH_TIME", "STATUS",
    "SIZE_CODE", "GRADE_CODE",
    "NO_PCS", "CTN_TYPE",
    "TOTAL_PLANNED_QTY",
    "CREATED_BY", "CREATION_DATE",
)


def _next_carton_id() -> int:
    """Return MAX(CARTON_ID)+1 from XXFG_CARTON_MASTER.

    Mirrors the pallet app's pattern. Race-safe enough for our single-
    operator-per-machine workflow — concurrent inserts from different
    apps could still collide, but that's a workflow design choice."""
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT NVL(MAX(CARTON_ID), 0) + 1 FROM {config_app.CARTON_TABLE}"
        )
        return int(cur.fetchone()[0])


@dataclass
class CartonRow:
    """One row read back from XXFG_CARTON_MASTER (full column set for UI)."""
    lpn_id: str            # display string — populated from CARTON_CODE
    carton_code: str
    brand: str
    item_code: str
    lot_number: str
    shift: str
    grade: str
    size_code: str
    batch_no: str
    batch_date: Optional[datetime]
    batch_time: str
    status: str
    qr_code: str = ""
    # Extended schema (all remaining XXFG_CARTON_MASTER columns)
    carton_id: Optional[int] = None
    organization_id: str = ""
    inventory_item_id: str = ""
    item_desc: str = ""
    carton_qty: Optional[float] = None
    uom_code: str = ""
    lpn_id_num: Optional[int] = None     # numeric LPN_ID column
    lpn_context: str = ""
    lot_no: str = ""
    grade_code: str = ""
    no_pcs: Optional[int] = None
    ctn_type: str = ""
    created_by: str = ""
    creation_date: Optional[datetime] = None


# ---------- In-memory store (offline / no DB configured) ----------
_MEM_LOCK = threading.Lock()
_MEM_ROWS: List[Dict[str, Any]] = []


def _parse_api_datetime(value):
    if isinstance(value, datetime) or value is None:
        return value
    if isinstance(value, str):
        text = value.rstrip("Z")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _api_param_datetime(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _api_carton_row(d: Dict[str, Any]) -> CartonRow:
    def _as_int(v):
        try: return int(v) if v is not None and v != "" else None
        except (TypeError, ValueError): return None
    def _as_float(v):
        try: return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError): return None
    return CartonRow(
        lpn_id=str(d.get("lpn_display") or d.get("carton_code") or d.get("lpn_id") or ""),
        carton_code=d.get("carton_code") or "",
        brand=d.get("brand") or "",
        item_code=d.get("item_code") or "",
        lot_number=d.get("lot_number") or "",
        shift=d.get("shift") or "",
        grade=d.get("grade") or "",
        size_code=d.get("size_code") or "",
        batch_no=d.get("batch_no") or "",
        batch_date=_parse_api_datetime(d.get("batch_date")),
        batch_time=d.get("batch_time") or "",
        status=d.get("status") or "",
        qr_code=d.get("qr_code") or "",
        carton_id=_as_int(d.get("carton_id")),
        organization_id=str(d.get("organization_id") or ""),
        inventory_item_id=str(d.get("inventory_item_id") or ""),
        item_desc=d.get("item_desc") or "",
        carton_qty=_as_float(d.get("carton_qty")),
        uom_code=d.get("uom_code") or "",
        lpn_id_num=_as_int(d.get("lpn_id")),
        lpn_context=d.get("lpn_context") or "",
        lot_no=str(d.get("lot_no") or ""),
        grade_code=d.get("grade_code") or "",
        no_pcs=_as_int(d.get("no_pcs")),
        ctn_type=d.get("ctn_type") or "",
        created_by=d.get("created_by") or "",
        creation_date=_parse_api_datetime(d.get("creation_date")),
    )


# ---------- Real Oracle connection ----------
# Oracle DB connections are NOT thread-safe — a connection created on one
# thread cannot be safely used (or pinged) on another. We give each
# thread its own connection via threading.local. The "generation" lets
# us invalidate every thread's cached connection in one go when the
# operator changes connection settings.
_THREAD_LOCAL = threading.local()
_GENERATION = 0
_GEN_LOCK = threading.Lock()


def reset_connections() -> None:
    """Mark every thread's cached connection as stale.

    Each thread will notice on its next `_connect()` call and reopen.
    Called by the Settings dialog after the operator saves new
    credentials so old connections aren't reused against a new host.
    """
    global _GENERATION
    api_client.reset()
    with _GEN_LOCK:
        _GENERATION += 1


def _build_dsn() -> str:
    """TNS alias if configured, else Easy Connect host:port/service."""
    if config_app.DB_USE_TNS and config_app.DB_TNS_ALIAS:
        return config_app.DB_TNS_ALIAS
    if not config_app.DB_HOST or not config_app.DB_SERVICE:
        return ""
    return oracledb.makedsn(
        config_app.DB_HOST, config_app.DB_PORT,
        service_name=config_app.DB_SERVICE,
    )


def _connect():
    """Return the Oracle connection for the *current thread*.

    Connections live in `threading.local` so a worker thread never
    pings or shares a connection that was opened on the main thread.
    """
    if config_app.USE_MOCK_DB:
        return None
    if oracledb is None:
        raise RuntimeError("oracledb package not installed.")
    if config_app.USE_THICK_MODE:
        ok, msg = _ensure_thick_initialized()
        if not ok:
            raise RuntimeError(f"Instant Client init failed: {msg}")

    cached = getattr(_THREAD_LOCAL, "conn", None)
    cached_gen = getattr(_THREAD_LOCAL, "gen", -1)
    if cached is not None and cached_gen == _GENERATION:
        try:
            cached.ping()
            return cached
        except Exception:
            try: cached.close()
            except Exception: pass
            _THREAD_LOCAL.conn = None

    dsn = _build_dsn()
    if not dsn:
        raise RuntimeError(
            "DB target not configured (set host/port/service or a TNS alias)."
        )
    # ---- Pure-Python TCP pre-check (Easy Connect mode only) ----
    # Probe the handshake ourselves with a short timeout BEFORE letting
    # oracledb anywhere near a possibly-offline host. On some Windows +
    # thick-mode setups, oracledb against an unreachable host can
    # SEGFAULT inside the native client — a pure-socket probe keeps
    # the failure in Python land where we can raise a normal error.
    if not (config_app.DB_USE_TNS and config_app.DB_TNS_ALIAS):
        host = config_app.DB_HOST
        port = int(config_app.DB_PORT or 0)
        if host and port:
            import socket
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    pass
            except (socket.timeout, OSError) as e:
                raise RuntimeError(
                    f"DB host {host}:{port} unreachable ({e})."
                )
    # tcp_connect_timeout caps how long we'll wait on a TCP handshake
    # before declaring the host unreachable — keeps the UI snappy when
    # the VPN is down (default would be ~30 s).
    conn = oracledb.connect(
        user=config_app.DB_USER,
        password=config_app.DB_PASSWORD,
        dsn=dsn,
        tcp_connect_timeout=5,
    )
    _THREAD_LOCAL.conn = conn
    _THREAD_LOCAL.gen  = _GENERATION
    return conn


def test_connection() -> tuple[bool, str]:
    if api_client.enabled():
        try:
            health = api_client.health()
            api_client.call("GET", "/brands")
            mode = health.get("mode", "unknown") if isinstance(health, dict) else "unknown"
            if isinstance(health, dict) and not health.get("ok", False):
                return False, f"Server API reached, but DB health is failing: {health.get('db')}"
            return True, f"Connected to server API ({mode}) at {config_app.SERVER_API_BASE_URL}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    if config_app.USE_MOCK_DB:
        return False, "DB credentials not configured (running offline)."
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM DUAL")
            cur.fetchone()
        if config_app.DB_USE_TNS and config_app.DB_TNS_ALIAS:
            target = f"TNS alias '{config_app.DB_TNS_ALIAS}'"
        else:
            target = f"{config_app.DB_HOST}:{config_app.DB_PORT}/{config_app.DB_SERVICE}"
        return True, f"Connected to {target}  [{current_mode()}]"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------- Reference data ----------
def fetch_brands() -> List[Dict[str, Any]]:
    """Always returns the configured BRANDS — same list whether online or offline.

    Each entry: {brand, org_code, inv_code}.
    """
    if api_client.enabled():
        try:
            return list(api_client.call("GET", "/brands") or [])
        except Exception:
            return []
    return [
        {"brand": name, "org_code": org_code, "inv_code": inv_code}
        for (name, org_code, inv_code) in config_app.BRANDS
    ]


def fetch_item_codes(brand: str) -> List[Dict[str, str]]:
    """Live items come from APPS.XXFG_ORG_ITEMS keyed by ORGANIZATION_ID
    — same query pattern the pallet app uses. Returns [] when offline OR
    when the live connection fails (VPN down) so the UI degrades to
    free-typed item entry."""
    if api_client.enabled():
        org_id = next(
            (b.get("inv_code") for b in fetch_brands() if b.get("brand") == brand),
            None,
        )
        if not org_id:
            return []
        try:
            return list(api_client.call("GET", "/items", params={"org_id": org_id}) or [])
        except Exception:
            return []
    if config_app.USE_MOCK_DB:
        return []
    # Map brand → inv_code (numeric ORGANIZATION_ID) from BRANDS
    org_id = next((iv for (n, _oc, iv) in config_app.BRANDS if n == brand), None)
    if not org_id:
        return []
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT Item_Code, CAT5 AS item_size
                  FROM APPS.XXFG_ORG_ITEMS
                 WHERE ORGANIZATION_ID = :oid
                   AND Item_Code IS NOT NULL
                 ORDER BY Item_Code
                """,
                oid=org_id,
            )
            return [
                {"code": r[0], "desc": "", "size": r[1] or ""}
                for r in cur.fetchall()
            ]
    except Exception:
        return []


def fetch_sample_config(size_code: str) -> Optional[Dict[str, float]]:
    """Look up XXFG_SAMPLE_CARTON_CONFIG by SIZE_CODE.

    Returns {'normal_pcs', 'sample_pcs', 'conversion'} or None if the
    size isn't in the config table (e.g. a freshly-typed custom size).
    Falls back to the offline FALLBACK_SAMPLE_CONFIG when USE_MOCK_DB.
    """
    if not size_code:
        return None
    if api_client.enabled():
        try:
            row = api_client.call(
                "GET", "/sample-config", params={"size_code": size_code}
            )
            return {
                "normal_pcs": int(row["normal_pcs"]),
                "sample_pcs": int(row["sample_pcs"]),
                "conversion": float(row["conversion"]),
            }
        except Exception:
            return None
    if config_app.USE_MOCK_DB:
        row = config_app.FALLBACK_SAMPLE_CONFIG.get(size_code)
        if not row:
            return None
        n, s, c = row
        return {"normal_pcs": int(n), "sample_pcs": int(s), "conversion": float(c)}
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT NORMAL_PCS_CTN, SAMPLE_PCS_CTN, CONVERSION_CTN
                  FROM APPS.XXFG_SAMPLE_CARTON_CONFIG
                 WHERE SIZE_CODE = :s
                   AND ROWNUM = 1
                """,
                s=size_code,
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "normal_pcs": int(row[0]),
                "sample_pcs": int(row[1]),
                "conversion": float(row[2]),
            }
    except Exception:
        return None


def fetch_max_lpn_counter_today() -> Optional[int]:
    """Highest LPN counter for TODAY according to XXFG_CARTON_MASTER.

    Used as the recovery source when the local lpn_state.json file is
    missing, corrupt, or holds a stale date — e.g. fresh install on a
    rebuilt machine, accidentally wiped %ProgramData% folder, or a
    crash that left the file unreadable.

    Today is identified by the YYMMDD prefix embedded in LPN_ID:
        LPN-C-260619000001  ->  LPN_ID = 260619000001
        LPN_ID range for today = [260619000000, 260620000000)

    Returns the COUNTER portion (e.g. 100 means the highest carton
    printed today is LPN-C-260619000100). None when there are no rows
    for today, or when the DB is offline / errors.
    """
    today_tag = datetime.now().strftime("%y%m%d")
    if api_client.enabled():
        try:
            row = api_client.call("GET", "/lpn/peek")
            lpn_num = int(row.get("lpn_num") or 0)
            floor = int(today_tag + "000000")
            counter = lpn_num - floor - 1
            return counter if counter > 0 else None
        except Exception:
            return None
    try:
        floor = int(today_tag + "000000")           # 260619000000
        ceiling = floor + 1_000_000                  # 260620000000
    except ValueError:
        return None

    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            best = 0
            for r in _MEM_ROWS:
                lid = r.get("LPN_ID")
                if lid is None:
                    continue
                try:
                    lid = int(lid)
                except (TypeError, ValueError):
                    continue
                if floor <= lid < ceiling and lid > best:
                    best = lid
        return (best - floor) if best > floor else None

    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT NVL(MAX(LPN_ID), 0)
                  FROM {config_app.CARTON_TABLE}
                 WHERE LPN_ID >= :lo
                   AND LPN_ID <  :hi
                """,
                lo=floor, hi=ceiling,
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return None
            max_lpn = int(row[0])
            counter = max_lpn - floor
            return counter if counter > 0 else None
    except Exception:
        return None


def fetch_normal_pcs_per_ctn(organization_id, item_code: str) -> Optional[float]:
    """PCS-per-CTN for an item from APPS.XXFG_UOM_CONVERSIONS_V.

    Used by the form for *Regular* (Normal) CTN type. CONVERSION_RATE
    on this view holds the per-carton quantity for the item — verified
    in the live DB: rows are stored with PRIMARY_UOM_CODE='SFT' and
    TARGET_UOM_CODE='CTN', so we read CONVERSION_RATE directly. Sample
    cartons keep using XXFG_SAMPLE_CARTON_CONFIG (separate helper).

    organization_id  numeric ORGANIZATION_ID (e.g. 370 for Alexander).
    item_code        e.g. 'AAS60601'.

    Returns the conversion rate as a float, or None when offline / no
    active row for the item. The caller (form_panel) falls back to
    XXFG_SAMPLE_CARTON_CONFIG.NORMAL_PCS_CTN when None is returned, so
    items that don't have a UOM-view row still work — they just use
    the sample-config table.
    """
    if not organization_id or not item_code:
        return None
    if api_client.enabled():
        try:
            row = api_client.call(
                "GET",
                "/uom-pcs-per-ctn",
                params={"org_id": organization_id, "item_code": item_code},
            )
            return float(row["pcs_per_ctn"])
        except Exception:
            return None
    if config_app.USE_MOCK_DB:
        # Offline mode — no live view. Caller falls back to the
        # sample_config table's NORMAL_PCS_CTN.
        return None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT CONVERSION_RATE
                  FROM APPS.XXFG_UOM_CONVERSIONS_V
                 WHERE ORGANIZATION_ID = :org
                   AND ITEM_CODE = :code
                   AND (DISABLE_DATE IS NULL OR DISABLE_DATE > SYSDATE)
                 ORDER BY DISABLE_DATE NULLS FIRST
                 FETCH FIRST 1 ROWS ONLY
                """,
                org=organization_id, code=item_code,
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            return float(row[0])
    except Exception:
        return None


# ---------- Insert / soft-delete / reprint ----------
def _fmt_err(e: Exception) -> str:
    """Compact, user-readable rendering of an exception."""
    return f"{type(e).__name__}: {e}"


def _print_count_from_status(status: Optional[str]) -> int:
    """Number of physical prints represented by a CARTON_MASTER row.

      'PRINTED'        → 1  (one print, never reprinted)
      'PRINTED-RP1'    → 2  (original + 1 reprint)
      'PRINTED-RPn'    → n + 1
      anything else    → 0  (skip — e.g. DELETED, or non-PRINTED status)

    Mirrors the Oracle CASE/REGEXP expression in batch_master_finalize."""
    if not status:
        return 0
    s = status.upper().strip()
    if "DELETED" in s:
        return 0
    if not s.startswith("PRINTED"):
        return 0
    if "-RP" not in s:
        return 1
    try:
        return 1 + int(s.rsplit("-RP", 1)[1])
    except (ValueError, IndexError):
        return 1


def insert_carton(row: Dict[str, Any]) -> tuple[bool, str]:
    """Insert one printed carton. `row` keys MUST match XXFG_CARTON_MASTER column names.
    CARTON_ID is auto-populated from MAX+1.
    Returns (ok, error_message). error_message is '' on success."""
    if api_client.enabled():
        try:
            api_client.call("POST", "/cartons", row, idempotent=True)
            return True, ""
        except Exception as e:
            return False, f"API insert failed: {_fmt_err(e)}"
    if config_app.USE_MOCK_DB:
        try:
            with _MEM_LOCK:
                # Assign a synthetic CARTON_ID for the offline cache too
                row = dict(row)
                if not row.get("CARTON_ID"):
                    row["CARTON_ID"] = max(
                        (r.get("CARTON_ID") or 0 for r in _MEM_ROWS),
                        default=0,
                    ) + 1
                _MEM_ROWS.insert(0, row)
            return True, ""
        except Exception as e:
            return False, f"In-memory insert failed: {_fmt_err(e)}"
    try:
        row = dict(row)   # don't mutate caller's dict
        row["CARTON_ID"] = _next_carton_id()
        placeholders = ", ".join(f":{c}" for c in INSERTED_COLUMNS)
        cols = ", ".join(INSERTED_COLUMNS)
        sql = f"INSERT INTO {config_app.CARTON_TABLE} ({cols}) VALUES ({placeholders})"
        conn = _connect()
        with conn.cursor() as cur:
            binds = {c: row.get(c) for c in INSERTED_COLUMNS}
            cur.execute(sql, binds)
            conn.commit()
        return True, ""
    except Exception as e:
        return False, f"DB insert failed: {_fmt_err(e)}"


def soft_delete(carton_code: str, by_user: str = "DIKAI_GUI") -> tuple[bool, str]:
    """Mark row STATUS='DELETED'. We look up by CARTON_CODE (the human
    'LPN-C-...' string) because the LPN_ID column is NUMBER.
    Returns (ok, message)."""
    if api_client.enabled():
        try:
            api_client.call(
                "DELETE",
                f"/cartons/{parse.quote(str(carton_code), safe='')}",
                {"by_user": by_user},
            )
            return True, ""
        except Exception as e:
            return False, f"API delete failed: {_fmt_err(e)}"
    if config_app.USE_MOCK_DB:
        try:
            with _MEM_LOCK:
                for r in _MEM_ROWS:
                    if str(r.get("CARTON_CODE")) == str(carton_code) \
                       or str(r.get("LPN_ID")) == str(carton_code):
                        r["STATUS"] = "DELETED"
                        r["LAST_UPDATED_BY"] = by_user
                        r["LAST_UPDATE_DATE"] = datetime.now()
                        return True, ""
            return False, f"Row {carton_code} not found (offline cache)."
        except Exception as e:
            return False, f"Delete failed: {_fmt_err(e)}"
    sql = f"""
        UPDATE {config_app.CARTON_TABLE}
           SET STATUS = 'DELETED',
               LAST_UPDATED_BY = :u,
               LAST_UPDATE_DATE = SYSDATE
         WHERE CARTON_CODE = :cc
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(sql, u=by_user, cc=carton_code)
            conn.commit()
            if cur.rowcount > 0:
                return True, ""
            return False, f"Row {carton_code} not found in {config_app.CARTON_TABLE}."
    except Exception as e:
        return False, f"DB delete failed: {_fmt_err(e)}"


@dataclass
class BatchRow:
    """One row from xxfg_carton_batch_master."""
    batch_id:            Optional[int] = None
    organization_id:     str = ""
    batch_no:            str = ""
    production_date:     Optional[datetime] = None
    item_code:           str = ""
    product_type:        str = ""   # ctn_type
    size_code:           str = ""
    uom_code:            str = ""
    batch_date:          Optional[datetime] = None
    lot_no:              str = ""
    brand:               str = ""
    grade:               str = ""
    shift:               str = ""
    status:              str = "N"
    production_qty:      float = 0.0
    produced_carton_qty: float = 0.0


# In-memory batch_master mirror for offline / mock mode.
_MEM_BATCH_ROWS: list[Dict[str, Any]] = []
_MEM_BATCH_SEQ:  int = 0


def _api_batch_row(d: Dict[str, Any]) -> BatchRow:
    return BatchRow(
        batch_id=int(d["batch_id"]) if d.get("batch_id") is not None else None,
        organization_id=str(d.get("organization_id") or ""),
        batch_no=d.get("batch_no") or "",
        production_date=_parse_api_datetime(d.get("production_date")),
        item_code=d.get("item_code") or "",
        product_type=d.get("product_type") or "",
        size_code=d.get("size_code") or "",
        uom_code=d.get("uom_code") or "",
        batch_date=_parse_api_datetime(d.get("batch_date")),
        lot_no=str(d.get("lot_no") or ""),
        brand=d.get("brand") or "",
        grade=d.get("grade") or "",
        shift=d.get("shift") or "",
        status=d.get("status") or "N",
        production_qty=float(d.get("production_qty") or 0),
        produced_carton_qty=float(d.get("produced_carton_qty") or 0),
    )


def query_batches(
    *,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
    brand:     Optional[str] = None,
    shift:     Optional[str] = None,
    grade:     Optional[str] = None,
    status:    Optional[str] = None,          # 'N' / 'Y' / None
    item_code_like: Optional[str] = None,
    lot_like:       Optional[str] = None,
    batch_no_like:  Optional[str] = None,
    limit:     int = 500,
    offset:    int = 0,
) -> List[BatchRow]:
    """Read straight from xxfg_carton_batch_master. The grouped
    aggregation happens at INSERT time (see batch_master_finalize);
    the view just reflects whatever rows are stored.

    Supports the filter set the History dialog's Batches mode exposes:
    date range, brand, shift, grade, exact status (N/Y), and
    case-insensitive substring matches on item_code / lot_no /
    batch_no."""
    if api_client.enabled():
        try:
            rows = api_client.call("GET", "/batches", params={
                "date_from": _api_param_datetime(date_from),
                "date_to": _api_param_datetime(date_to),
                "brand": brand,
                "shift": shift,
                "grade": grade,
                "status": status,
                "item_code_like": item_code_like,
                "lot_like": lot_like,
                "batch_no_like": batch_no_like,
                "limit": limit,
                "offset": offset,
            }) or []
            _set_last_query_error("")
            return [_api_batch_row(d) for d in rows]
        except Exception as e:
            _set_last_query_error(f"Batch API query failed: {_fmt_err(e)}")
            return []
    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            rows = [dict(r) for r in _MEM_BATCH_ROWS]
        out: List[BatchRow] = []
        for d in rows:
            if brand and d.get("BRAND") != brand:   continue
            if shift and d.get("SHIFT") != shift:   continue
            if grade and d.get("GRADE") != grade:   continue
            if status and (d.get("STATUS") or "").upper() != status.upper(): continue
            if item_code_like and item_code_like.upper() not in (d.get("ITEM_CODE") or "").upper():
                continue
            if lot_like and lot_like.upper() not in str(d.get("LOT_NO") or "").upper():
                continue
            if batch_no_like and batch_no_like.upper() not in (d.get("BATCH_NO") or "").upper():
                continue
            bd = d.get("BATCH_DATE")
            if date_from and bd and bd < date_from: continue
            if date_to   and bd and bd > date_to:   continue
            out.append(BatchRow(
                batch_id=d.get("BATCH_ID"),
                organization_id=str(d.get("ORGANIZATION_ID") or ""),
                batch_no=d.get("BATCH_NO") or "",
                production_date=d.get("PRODUCTION_DATE"),
                item_code=d.get("ITEM_CODE") or "",
                product_type=d.get("PRODUCT_TYPE") or "",
                size_code=d.get("SIZE_CODE") or "",
                uom_code=d.get("UOM_CODE") or "",
                batch_date=d.get("BATCH_DATE"),
                lot_no=str(d.get("LOT_NO") or ""),
                brand=d.get("BRAND") or "",
                grade=d.get("GRADE") or "",
                shift=d.get("SHIFT") or "",
                status=d.get("STATUS") or "N",
                production_qty=float(d.get("PRODUCTION_QTY") or 0),
                produced_carton_qty=float(d.get("PRODUCED_CARTON_QTY") or 0),
            ))
        offset = max(0, int(offset or 0))
        return out[offset:offset + limit]

    where: list[str] = []
    binds: Dict[str, Any] = {}
    if date_from:
        where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = date_from
    if date_to:
        where.append("BATCH_DATE <= :dto");   binds["dto"]   = date_to
    if brand:
        where.append("BRAND = :brand");       binds["brand"] = brand
    if shift:
        where.append("SHIFT = :shift");       binds["shift"] = shift
    if grade:
        where.append("GRADE = :grade");       binds["grade"] = grade
    if status:
        where.append("STATUS = :status");     binds["status"] = status.upper()
    if item_code_like:
        where.append("UPPER(ITEM_CODE) LIKE :item"); binds["item"] = f"%{item_code_like.upper()}%"
    if lot_like:
        where.append("UPPER(LOT_NO) LIKE :lot"); binds["lot"] = f"%{lot_like.upper()}%"
    if batch_no_like:
        where.append("UPPER(BATCH_NO) LIKE :bn"); binds["bn"] = f"%{batch_no_like.upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT BATCH_ID, ORGANIZATION_ID, BATCH_NO, PRODUCTION_DATE,
               ITEM_CODE, PRODUCT_TYPE, SIZE_CODE, UOM_CODE, BATCH_DATE,
               LOT_NO, BRAND, GRADE, SHIFT, STATUS,
               PRODUCTION_QTY, PRODUCED_CARTON_QTY
         FROM {config_app.CARTON_BATCH_TABLE}
          {where_sql}
         ORDER BY BATCH_ID DESC
         OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
    """
    binds["lim"] = limit
    binds["off"] = max(0, int(offset or 0))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            out = []
            for r in cur.fetchall():
                out.append(BatchRow(
                    batch_id=int(r[0]) if r[0] is not None else None,
                    organization_id=str(r[1] or ""),
                    batch_no=r[2] or "",
                    production_date=r[3],
                    item_code=r[4] or "",
                    product_type=r[5] or "",
                    size_code=r[6] or "",
                    uom_code=r[7] or "",
                    batch_date=r[8],
                    lot_no=str(r[9] or ""),
                    brand=r[10] or "",
                    grade=r[11] or "",
                    shift=r[12] or "",
                    status=r[13] or "N",
                    production_qty=float(r[14] or 0),
                    produced_carton_qty=float(r[15] or 0),
                ))
            _set_last_query_error("")
            return out
    except Exception as e:
        _set_last_query_error(f"Batch query failed: {_fmt_err(e)}")
        return []


def count_batches_filtered(
    *,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
    brand:     Optional[str] = None,
    shift:     Optional[str] = None,
    grade:     Optional[str] = None,
    status:    Optional[str] = None,
    item_code_like: Optional[str] = None,
    lot_like:       Optional[str] = None,
    batch_no_like:  Optional[str] = None,
) -> int:
    if api_client.enabled():
        try:
            res = api_client.call("GET", "/batches", params={
                "date_from": _api_param_datetime(date_from),
                "date_to": _api_param_datetime(date_to),
                "brand": brand,
                "shift": shift,
                "grade": grade,
                "status": status,
                "item_code_like": item_code_like,
                "lot_like": lot_like,
                "batch_no_like": batch_no_like,
                "limit": 1,
                "offset": 0,
                "include_total": "true",
            }) or {}
            _set_last_query_error("")
            return int(res.get("total") or 0) if isinstance(res, dict) else len(res)
        except Exception as e:
            _set_last_query_error(f"Batch API count failed: {_fmt_err(e)}")
            return 0

    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            rows = [dict(r) for r in _MEM_BATCH_ROWS]
        return len(query_batches(
            date_from=date_from, date_to=date_to, brand=brand, shift=shift,
            grade=grade, status=status, item_code_like=item_code_like,
            lot_like=lot_like, batch_no_like=batch_no_like,
            limit=len(rows) or 1, offset=0,
        ))

    where: list[str] = []
    binds: Dict[str, Any] = {}
    if date_from:
        where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = date_from
    if date_to:
        where.append("BATCH_DATE <= :dto");   binds["dto"]   = date_to
    if brand:
        where.append("BRAND = :brand");       binds["brand"] = brand
    if shift:
        where.append("SHIFT = :shift");       binds["shift"] = shift
    if grade:
        where.append("GRADE = :grade");       binds["grade"] = grade
    if status:
        where.append("STATUS = :status");     binds["status"] = status.upper()
    if item_code_like:
        where.append("UPPER(ITEM_CODE) LIKE :item"); binds["item"] = f"%{item_code_like.upper()}%"
    if lot_like:
        where.append("UPPER(LOT_NO) LIKE :lot"); binds["lot"] = f"%{lot_like.upper()}%"
    if batch_no_like:
        where.append("UPPER(BATCH_NO) LIKE :bn"); binds["bn"] = f"%{batch_no_like.upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {config_app.CARTON_BATCH_TABLE} {where_sql}",
                binds,
            )
            _set_last_query_error("")
            return int(cur.fetchone()[0])
    except Exception as e:
        _set_last_query_error(f"Batch count failed: {_fmt_err(e)}")
        return 0


def query_batches_page(*, limit: int = 30, offset: int = 0, **filters) -> tuple[List[BatchRow], int]:
    if api_client.enabled():
        try:
            res = api_client.call("GET", "/batches", params={
                "date_from": _api_param_datetime(filters.get("date_from")),
                "date_to": _api_param_datetime(filters.get("date_to")),
                "brand": filters.get("brand"),
                "shift": filters.get("shift"),
                "grade": filters.get("grade"),
                "status": filters.get("status"),
                "item_code_like": filters.get("item_code_like"),
                "lot_like": filters.get("lot_like"),
                "batch_no_like": filters.get("batch_no_like"),
                "limit": limit,
                "offset": offset,
                "include_total": "true",
            }) or {}
            if isinstance(res, dict):
                rows = [_api_batch_row(d) for d in (res.get("rows") or [])]
                total = int(res.get("total") or 0)
            else:
                rows = [_api_batch_row(d) for d in res]
                total = len(rows)
            _set_last_query_error("")
            return rows, total
        except Exception as e:
            _set_last_query_error(f"Batch API query failed: {_fmt_err(e)}")
            return [], 0

    rows = query_batches(limit=limit, offset=offset, **filters)
    total = count_batches_filtered(**filters)
    return rows, total


# NOTE: XXFG_CARTON_BATCH_MASTER is now maintained by an Oracle trigger
# on XXFG_CARTON_MASTER. The app never inserts or updates batch_master
# rows itself. Read-only access via query_batches() is still used by the
# History dialog's "Batches" view.


def find_duplicate(
    *,
    brand: str,
    item_code: str,
    organization_id: str = "",
    shift: str = "",
    grade: str = "",
    size_code: str = "",
    lot_number: str = "",
    batch_date: Optional[datetime] = None,
) -> Optional[str]:
    """Return the CARTON_CODE of an existing non-deleted row that matches
    the operator-controlled identity fields, or None.

    Match criteria (from the operator's spec):
        brand, item_code, organization_id, shift, grade, size_code,
        lot_number, and same calendar date (batch_date truncated).

    Returns the MOST RECENT matching CARTON_CODE so subsequent reprint
    bumps walk that row's STATUS forward (PRINTED → -RP1 → -RP2 …).

    Best-effort: returns None on any DB error so the duplicate-check is
    never the reason an auto-print stalls.
    """
    if not brand or not item_code:
        return None    # Not enough identity to declare a duplicate

    # ----- in-memory store path -----
    if config_app.USE_MOCK_DB:
        try:
            target_date = batch_date.date() if batch_date else None
            with _MEM_LOCK:
                # Newest first — _MEM_ROWS is prepend-inserted.
                for r in _MEM_ROWS:
                    if (r.get("STATUS") or "").upper() == "DELETED":
                        continue
                    if r.get("BRAND") != brand:                 continue
                    if r.get("ITEM_CODE") != item_code:         continue
                    if organization_id and str(r.get("ORGANIZATION_ID") or "") != organization_id:
                        continue
                    if shift and r.get("SHIFT") != shift:       continue
                    if grade and r.get("GRADE") != grade:       continue
                    if size_code and r.get("SIZE_CODE") != size_code:
                        continue
                    if lot_number and r.get("LOT_NUMBER") != lot_number:
                        continue
                    if target_date is not None:
                        bd = r.get("BATCH_DATE")
                        if not bd or bd.date() != target_date:
                            continue
                    return r.get("CARTON_CODE") or None
            return None
        except Exception:
            return None

    # ----- live Oracle path -----
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT CARTON_CODE
                  FROM {config_app.CARTON_TABLE}
                 WHERE (STATUS IS NULL OR STATUS <> 'DELETED')
                   AND BRAND = :brand
                   AND ITEM_CODE = :item_code
                   AND (:org = '' OR ORGANIZATION_ID = :org)
                   AND (:shift = '' OR SHIFT = :shift)
                   AND (:grade = '' OR GRADE = :grade)
                   AND (:size_code = '' OR SIZE_CODE = :size_code)
                   AND (:lot_number = '' OR LOT_NUMBER = :lot_number)
                   AND (:bdate IS NULL OR TRUNC(BATCH_DATE) = TRUNC(:bdate))
                 ORDER BY CREATION_DATE DESC
                 FETCH FIRST 1 ROWS ONLY
                """,
                brand=brand,
                item_code=item_code,
                org=organization_id or "",
                shift=shift or "",
                grade=grade or "",
                size_code=size_code or "",
                lot_number=lot_number or "",
                bdate=batch_date,
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def mark_reprint(carton_code: str, by_user: str = "DIKAI_GUI") -> tuple[bool, str]:
    """Append/bump an -RPn suffix on STATUS. We look up by CARTON_CODE
    (the human 'LPN-C-...' string) because LPN_ID is NUMBER.
    Returns (ok, message)."""
    if api_client.enabled():
        try:
            api_client.call(
                "POST",
                f"/cartons/{parse.quote(str(carton_code), safe='')}/reprint",
                {"by_user": by_user},
            )
            return True, ""
        except Exception as e:
            return False, f"API reprint mark failed: {_fmt_err(e)}"
    if config_app.USE_MOCK_DB:
        try:
            with _MEM_LOCK:
                for r in _MEM_ROWS:
                    if str(r.get("CARTON_CODE")) == str(carton_code) \
                       or str(r.get("LPN_ID")) == str(carton_code):
                        cur_status = r.get("STATUS") or ""
                        if "-RP" in cur_status:
                            base, _, n = cur_status.rpartition("-RP")
                            try:
                                r["STATUS"] = f"{base}-RP{int(n) + 1}"
                            except ValueError:
                                r["STATUS"] = f"{cur_status}-RP1"
                        else:
                            r["STATUS"] = f"{cur_status or 'PRINTED'}-RP1"
                        r["LAST_UPDATED_BY"] = by_user
                        r["LAST_UPDATE_DATE"] = datetime.now()
                        return True, ""
            return False, f"Row {carton_code} not found (offline cache)."
        except Exception as e:
            return False, f"Reprint mark failed: {_fmt_err(e)}"
    sql = f"""
        UPDATE {config_app.CARTON_TABLE}
           SET STATUS = CASE
                          WHEN STATUS LIKE '%-RP%'
                          THEN REGEXP_REPLACE(STATUS, '-RP(\\d+)$', '-RP' || (TO_NUMBER(REGEXP_SUBSTR(STATUS, '\\d+$')) + 1))
                          ELSE NVL(STATUS, 'PRINTED') || '-RP1'
                        END,
               LAST_UPDATED_BY = :u,
               LAST_UPDATE_DATE = SYSDATE
         WHERE CARTON_CODE = :cc
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(sql, u=by_user, cc=carton_code)
            conn.commit()
            if cur.rowcount > 0:
                return True, ""
            return False, f"Row {carton_code} not found in {config_app.CARTON_TABLE}."
    except Exception as e:
        return False, f"DB reprint mark failed: {_fmt_err(e)}"


# ---------- History query ----------
def _row_from_mem(d: Dict[str, Any]) -> CartonRow:
    def _as_int(v):
        try: return int(v) if v is not None and v != "" else None
        except (TypeError, ValueError): return None
    def _as_float(v):
        try: return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError): return None
    return CartonRow(
        lpn_id=str(d.get("CARTON_CODE") or d.get("LPN_ID") or ""),
        carton_code=d.get("CARTON_CODE") or "",
        brand=d.get("BRAND") or "",
        item_code=d.get("ITEM_CODE") or "",
        lot_number=d.get("LOT_NUMBER") or "",
        shift=d.get("SHIFT") or "",
        grade=d.get("GRADE") or "",
        size_code=d.get("SIZE_CODE") or "",
        batch_no=d.get("BATCH_NO") or "",
        batch_date=d.get("BATCH_DATE"),
        batch_time=d.get("BATCH_TIME") or "",
        status=d.get("STATUS") or "",
        qr_code=d.get("QR_CODE") or "",
        carton_id=_as_int(d.get("CARTON_ID")),
        organization_id=str(d.get("ORGANIZATION_ID") or ""),
        inventory_item_id=str(d.get("INVENTORY_ITEM_ID") or ""),
        item_desc=d.get("ITEM_DESC") or "",
        carton_qty=_as_float(d.get("CARTON_QTY")),
        uom_code=d.get("UOM_CODE") or "",
        lpn_id_num=_as_int(d.get("LPN_ID")),
        lpn_context=d.get("LPN_CONTEXT") or "",
        lot_no=str(d.get("LOT_NO") or ""),
        grade_code=d.get("GRADE_CODE") or "",
        no_pcs=_as_int(d.get("NO_PCS")),
        ctn_type=d.get("CTN_TYPE") or "",
        created_by=d.get("CREATED_BY") or "",
        creation_date=d.get("CREATION_DATE"),
    )


def query_history(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    brand: Optional[str] = None,
    item_code_like: Optional[str] = None,
    lot_like: Optional[str] = None,
    shift: Optional[str] = None,
    grade: Optional[str] = None,
    status: Optional[str] = None,
    lpn_like: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> List[CartonRow]:

    if api_client.enabled():
        try:
            rows = api_client.call("GET", "/cartons", params={
                "date_from": _api_param_datetime(date_from),
                "date_to": _api_param_datetime(date_to),
                "brand": brand,
                "item_code_like": item_code_like,
                "lot_like": lot_like,
                "shift": shift,
                "grade": grade,
                "status": status,
                "lpn_like": lpn_like,
                "include_deleted": str(bool(include_deleted)).lower(),
                "limit": limit,
                "offset": offset,
            }) or []
            _set_last_query_error("")
            return [_api_carton_row(r) for r in rows]
        except Exception as e:
            _set_last_query_error(f"History API query failed: {_fmt_err(e)}")
            return []

    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            rows = [dict(r) for r in _MEM_ROWS]

        def keep(d: Dict[str, Any]) -> bool:
            st = (d.get("STATUS") or "").upper()
            if not include_deleted and st == "DELETED":
                return False
            if brand and d.get("BRAND") != brand:
                return False
            if shift and d.get("SHIFT") != shift:
                return False
            if grade and d.get("GRADE") != grade:
                return False
            if status and status.upper() not in st:
                return False
            if item_code_like and item_code_like.upper() not in (d.get("ITEM_CODE") or "").upper():
                return False
            if lot_like and lot_like.upper() not in (d.get("LOT_NUMBER") or "").upper():
                return False
            if lpn_like and lpn_like.upper() not in str(d.get("CARTON_CODE") or d.get("LPN_ID") or "").upper():
                return False
            bd = d.get("BATCH_DATE")
            if date_from and bd and bd < date_from:
                return False
            if date_to and bd and bd > date_to:
                return False
            return True

        offset = max(0, int(offset or 0))
        return [_row_from_mem(r) for r in rows if keep(r)][offset:offset + limit]

    where: list[str] = []
    binds: Dict[str, Any] = {}
    if not include_deleted:
        where.append("(STATUS IS NULL OR STATUS <> 'DELETED')")
    if date_from:
        where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = date_from
    if date_to:
        where.append("BATCH_DATE <= :dto");   binds["dto"] = date_to
    if brand:
        where.append("BRAND = :brand");       binds["brand"] = brand
    if shift:
        where.append("SHIFT = :shift");       binds["shift"] = shift
    if grade:
        where.append("GRADE = :grade");       binds["grade"] = grade
    if status:
        where.append("STATUS LIKE :status");  binds["status"] = f"%{status}%"
    if item_code_like:
        where.append("UPPER(ITEM_CODE) LIKE :item"); binds["item"] = f"%{item_code_like.upper()}%"
    if lot_like:
        where.append("UPPER(LOT_NUMBER) LIKE :lot"); binds["lot"] = f"%{lot_like.upper()}%"
    if lpn_like:
        where.append("UPPER(CARTON_CODE) LIKE :lpn"); binds["lpn"] = f"%{lpn_like.upper()}%"

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    # Full column read — CARTON_CODE surfaces as the operator-visible
    # "LPN" because the NUMBER LPN_ID column only holds the numeric tail.
    sql = f"""
        SELECT CARTON_CODE AS LPN_DISPLAY, CARTON_CODE, BRAND, ITEM_CODE,
               LOT_NUMBER, SHIFT, GRADE, SIZE_CODE, BATCH_NO, BATCH_DATE,
               BATCH_TIME, STATUS, QR_CODE,
               CARTON_ID, ORGANIZATION_ID, INVENTORY_ITEM_ID, ITEM_DESC,
               CARTON_QTY, UOM_CODE, LPN_ID, LPN_CONTEXT, LOT_NO,
               GRADE_CODE, NO_PCS, CTN_TYPE, CREATED_BY, CREATION_DATE
          FROM {config_app.CARTON_TABLE}
          {where_sql}
         ORDER BY CREATION_DATE DESC
         OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
    """
    binds["lim"] = limit
    binds["off"] = max(0, int(offset or 0))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            out = []
            for r in cur.fetchall():
                out.append(CartonRow(
                    lpn_id=str(r[0] or ""),
                    carton_code=r[1] or "",
                    brand=r[2] or "",
                    item_code=r[3] or "",
                    lot_number=r[4] or "",
                    shift=r[5] or "",
                    grade=r[6] or "",
                    size_code=r[7] or "",
                    batch_no=r[8] or "",
                    batch_date=r[9],
                    batch_time=r[10] or "",
                    status=r[11] or "",
                    qr_code=r[12] or "",
                    carton_id=int(r[13]) if r[13] is not None else None,
                    organization_id=str(r[14] or ""),
                    inventory_item_id=str(r[15] or ""),
                    item_desc=r[16] or "",
                    carton_qty=float(r[17]) if r[17] is not None else None,
                    uom_code=r[18] or "",
                    lpn_id_num=int(r[19]) if r[19] is not None else None,
                    lpn_context=r[20] or "",
                    lot_no=str(r[21] or ""),
                    grade_code=r[22] or "",
                    no_pcs=int(r[23]) if r[23] is not None else None,
                    ctn_type=r[24] or "",
                    created_by=r[25] or "",
                    creation_date=r[26],
                ))
            _set_last_query_error("")
            return out
    except Exception as e:
        _set_last_query_error(f"History query failed: {_fmt_err(e)}")
        return []


def count_history_filtered(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    brand: Optional[str] = None,
    item_code_like: Optional[str] = None,
    lot_like: Optional[str] = None,
    shift: Optional[str] = None,
    grade: Optional[str] = None,
    status: Optional[str] = None,
    lpn_like: Optional[str] = None,
    include_deleted: bool = False,
) -> int:
    if api_client.enabled():
        try:
            res = api_client.call("GET", "/cartons", params={
                "date_from": _api_param_datetime(date_from),
                "date_to": _api_param_datetime(date_to),
                "brand": brand,
                "item_code_like": item_code_like,
                "lot_like": lot_like,
                "shift": shift,
                "grade": grade,
                "status": status,
                "lpn_like": lpn_like,
                "include_deleted": str(bool(include_deleted)).lower(),
                "limit": 1,
                "offset": 0,
                "include_total": "true",
            }) or {}
            _set_last_query_error("")
            return int(res.get("total") or 0) if isinstance(res, dict) else len(res)
        except Exception as e:
            _set_last_query_error(f"History API count failed: {_fmt_err(e)}")
            return 0

    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            rows = [dict(r) for r in _MEM_ROWS]
        return len(query_history(
            date_from=date_from, date_to=date_to, brand=brand,
            item_code_like=item_code_like, lot_like=lot_like, shift=shift,
            grade=grade, status=status, lpn_like=lpn_like,
            include_deleted=include_deleted, limit=len(rows) or 1, offset=0,
        ))

    where: list[str] = []
    binds: Dict[str, Any] = {}
    if not include_deleted:
        where.append("(STATUS IS NULL OR STATUS <> 'DELETED')")
    if date_from:
        where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = date_from
    if date_to:
        where.append("BATCH_DATE <= :dto");   binds["dto"] = date_to
    if brand:
        where.append("BRAND = :brand");       binds["brand"] = brand
    if shift:
        where.append("SHIFT = :shift");       binds["shift"] = shift
    if grade:
        where.append("GRADE = :grade");       binds["grade"] = grade
    if status:
        where.append("STATUS LIKE :status");  binds["status"] = f"%{status}%"
    if item_code_like:
        where.append("UPPER(ITEM_CODE) LIKE :item"); binds["item"] = f"%{item_code_like.upper()}%"
    if lot_like:
        where.append("UPPER(LOT_NUMBER) LIKE :lot"); binds["lot"] = f"%{lot_like.upper()}%"
    if lpn_like:
        where.append("UPPER(CARTON_CODE) LIKE :lpn"); binds["lpn"] = f"%{lpn_like.upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {config_app.CARTON_TABLE} {where_sql}",
                binds,
            )
            _set_last_query_error("")
            return int(cur.fetchone()[0])
    except Exception as e:
        _set_last_query_error(f"History count failed: {_fmt_err(e)}")
        return 0


def query_history_page(*, limit: int = 30, offset: int = 0, **filters) -> tuple[List[CartonRow], int]:
    if api_client.enabled():
        try:
            res = api_client.call("GET", "/cartons", params={
                "date_from": _api_param_datetime(filters.get("date_from")),
                "date_to": _api_param_datetime(filters.get("date_to")),
                "brand": filters.get("brand"),
                "item_code_like": filters.get("item_code_like"),
                "lot_like": filters.get("lot_like"),
                "shift": filters.get("shift"),
                "grade": filters.get("grade"),
                "status": filters.get("status"),
                "lpn_like": filters.get("lpn_like"),
                "include_deleted": str(bool(filters.get("include_deleted", False))).lower(),
                "limit": limit,
                "offset": offset,
                "include_total": "true",
            }) or {}
            if isinstance(res, dict):
                rows = [_api_carton_row(d) for d in (res.get("rows") or [])]
                total = int(res.get("total") or 0)
            else:
                rows = [_api_carton_row(d) for d in res]
                total = len(rows)
            _set_last_query_error("")
            return rows, total
        except Exception as e:
            _set_last_query_error(f"History API query failed: {_fmt_err(e)}")
            return [], 0

    rows = query_history(limit=limit, offset=offset, **filters)
    total = count_history_filtered(**filters)
    return rows, total


# ---------- last-query error stash (so dialogs can surface a message
#            without changing return types of read functions) ----------
_LAST_QUERY_ERROR: str = ""


def _set_last_query_error(msg: str) -> None:
    global _LAST_QUERY_ERROR
    _LAST_QUERY_ERROR = msg


def last_query_error() -> str:
    """Most recent message recorded by a read function (query_history, etc.)."""
    return _LAST_QUERY_ERROR


def count_today() -> int:
    if api_client.enabled():
        try:
            row = api_client.call("GET", "/cartons/count", params={"scope": "today"})
            return int(row.get("count") or 0)
        except Exception:
            return 0
    if config_app.USE_MOCK_DB:
        today = datetime.now().date()
        with _MEM_LOCK:
            return sum(
                1 for r in _MEM_ROWS
                if r.get("BATCH_DATE") and r["BATCH_DATE"].date() == today
                and (r.get("STATUS") or "").upper() != "DELETED"
            )
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*) FROM {config_app.CARTON_TABLE}
                     WHERE TRUNC(CREATION_DATE) = TRUNC(SYSDATE)
                       AND (STATUS IS NULL OR STATUS <> 'DELETED')""")
            return int(cur.fetchone()[0])
    except Exception:
        return 0


def count_total() -> int:
    if api_client.enabled():
        try:
            row = api_client.call("GET", "/cartons/count", params={"scope": "total"})
            return int(row.get("count") or 0)
        except Exception:
            return 0
    if config_app.USE_MOCK_DB:
        with _MEM_LOCK:
            return sum(
                1 for r in _MEM_ROWS
                if (r.get("STATUS") or "").upper() != "DELETED"
            )
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*) FROM {config_app.CARTON_TABLE}
                     WHERE (STATUS IS NULL OR STATUS <> 'DELETED')""")
            return int(cur.fetchone()[0])
    except Exception:
        return 0
