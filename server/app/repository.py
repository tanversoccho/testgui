"""Data-access layer. One function per API operation. Each function
dispatches to the in-memory mock store or live Oracle based on
settings.USE_MOCK_DB."""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import db, queries
from .config import settings


INSERTED_COLUMNS: Tuple[str, ...] = (
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

# Serializes MAX(CARTON_ID)+1 + INSERT pairs to avoid PK collision.
_ID_LOCK = threading.Lock()


# ====================================================================
# Reference data
# ====================================================================
_FALLBACK_SAMPLE_CFG: Dict[str, Tuple[int, int, float]] = {
    "20X30":        (25, 10, 0.40),
    "25X40":        (10,  6, 0.60),
    "30X45":        ( 8,  6, 0.75),
    "30X50":        ( 8,  6, 0.75),
    "30X60":        ( 6,  6, 1.00),
    "40X40":        ( 8,  6, 0.75),
    "60X60":        ( 4,  4, 1.00),
    "30X60(ROCKX)": ( 5,  6, 1.20),
}

_MOCK_ITEMS_BY_ORG: Dict[int, List[Dict[str, str]]] = {
    481: [{"code": "MNL60601", "size": "60X60", "desc": "Monalisa Plain"},
          {"code": "MNL30450", "size": "30X45", "desc": "Monalisa 30x45"}],
    370: [{"code": "AAS60601", "size": "60X60", "desc": "Alexander 60x60"},
          {"code": "AAS40400", "size": "40X40", "desc": "Alexander 40x40"}],
    369: [{"code": "XGVT66143", "size": "60X60", "desc": "X Monica VT 60x60"}],
    522: [{"code": "XTL30600", "size": "30X60", "desc": "X Tiles 30x60"}],
    368: [{"code": "VNS25400", "size": "25X40", "desc": "Venus 25x40"}],
}


def fetch_items(org_id: int) -> List[Dict[str, str]]:
    if settings.USE_MOCK_DB:
        return _MOCK_ITEMS_BY_ORG.get(org_id, [])
    with db.cursor() as cur:
        cur.execute(queries.FETCH_ITEMS.sql, oid=org_id)
        return [{"code": r[0], "size": r[1] or "", "desc": ""} for r in cur.fetchall()]


def fetch_sample_config(size_code: str) -> Optional[Dict[str, Any]]:
    if settings.USE_MOCK_DB:
        row = _FALLBACK_SAMPLE_CFG.get(size_code)
        if not row:
            return None
        n, s, c = row
        return {"normal_pcs": n, "sample_pcs": s, "conversion": c}
    with db.cursor() as cur:
        cur.execute(queries.FETCH_SAMPLE_CONFIG.sql, s=size_code)
        row = cur.fetchone()
        if not row:
            return None
        return {"normal_pcs": int(row[0]), "sample_pcs": int(row[1]), "conversion": float(row[2])}


def fetch_uom_pcs_per_ctn(org_id: int, item_code: str) -> Optional[float]:
    if settings.USE_MOCK_DB:
        # In mock, fall back to sample-config NORMAL_PCS_CTN if we know it.
        items = _MOCK_ITEMS_BY_ORG.get(org_id, [])
        for it in items:
            if it["code"] == item_code:
                size = it.get("size") or ""
                row = _FALLBACK_SAMPLE_CFG.get(size)
                if row:
                    return float(row[0])
        return None
    with db.cursor() as cur:
        cur.execute(queries.FETCH_UOM_PCS_PER_CTN.sql, org=org_id, code=item_code)
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return float(row[0])


# ====================================================================
# Carton writes
# ====================================================================
def _next_carton_id() -> int:
    with db.cursor() as cur:
        cur.execute(
            queries.NEXT_CARTON_ID.sql.format(carton_table=settings.CARTON_TABLE)
        )
        return int(cur.fetchone()[0])


def insert_carton(row: Dict[str, Any]) -> Tuple[bool, str, Optional[int]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            row = dict(row)
            row["CARTON_ID"] = max(
                (r.get("CARTON_ID") or 0 for r in db.MOCK_CARTONS), default=0
            ) + 1
            row.setdefault("CREATION_DATE", datetime.now())
            row.setdefault("STATUS", "PRINTED")
            db.MOCK_CARTONS.insert(0, row)
            return True, "", row["CARTON_ID"]
    with _ID_LOCK:
        try:
            row = dict(row)
            row["CARTON_ID"] = _next_carton_id()
            row.setdefault("CREATION_DATE", datetime.now())
            row.setdefault("STATUS", "PRINTED")
            placeholders = ", ".join(f":{c}" for c in INSERTED_COLUMNS)
            cols = ", ".join(INSERTED_COLUMNS)
            sql = queries.INSERT_CARTON.sql.format(
                carton_table=settings.CARTON_TABLE,
                cols=cols,
                placeholders=placeholders,
            )
            with db.cursor() as cur:
                cur.execute(sql, {c: row.get(c) for c in INSERTED_COLUMNS})
            return True, "", row["CARTON_ID"]
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", None


def soft_delete_carton(carton_code: str, by_user: str) -> Tuple[bool, str]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            for r in db.MOCK_CARTONS:
                if r.get("CARTON_CODE") == carton_code:
                    r["STATUS"] = "DELETED"
                    r["LAST_UPDATED_BY"] = by_user
                    r["LAST_UPDATE_DATE"] = datetime.now()
                    return True, ""
            return False, "Carton not found"
    sql = queries.SOFT_DELETE_CARTON.sql.format(carton_table=settings.CARTON_TABLE)
    try:
        with db.cursor() as cur:
            cur.execute(sql, u=by_user, cc=carton_code)
            if cur.rowcount > 0:
                return True, ""
            return False, "Carton not found"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def mark_reprint(carton_code: str, by_user: str) -> Tuple[bool, str, Optional[str]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            for r in db.MOCK_CARTONS:
                if r.get("CARTON_CODE") == carton_code:
                    cur_status = r.get("STATUS") or "PRINTED"
                    if "-RP" in cur_status:
                        base, _, n = cur_status.rpartition("-RP")
                        try:
                            new = f"{base}-RP{int(n) + 1}"
                        except ValueError:
                            new = f"{cur_status}-RP1"
                    else:
                        new = f"{cur_status}-RP1"
                    r["STATUS"] = new
                    r["LAST_UPDATED_BY"] = by_user
                    r["LAST_UPDATE_DATE"] = datetime.now()
                    return True, "", new
            return False, "Carton not found", None
    sql = queries.MARK_REPRINT.sql.format(carton_table=settings.CARTON_TABLE)
    try:
        with db.cursor() as cur:
            cur.execute(sql, u=by_user, cc=carton_code)
            if cur.rowcount == 0:
                return False, "Carton not found", None
            cur.execute(
                queries.SELECT_REPRINT_STATUS.sql.format(
                    carton_table=settings.CARTON_TABLE
                ),
                cc=carton_code,
            )
            row = cur.fetchone()
            new_status = row[0] if row else None
            return True, "", new_status
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None


# ====================================================================
# Carton reads
# ====================================================================
_ORACLE_COLS = [
    "lpn_display", "carton_code", "brand", "item_code", "lot_number", "shift",
    "grade", "size_code", "batch_no", "batch_date", "batch_time", "status",
    "qr_code", "carton_id", "organization_id", "inventory_item_id", "item_desc",
    "carton_qty", "uom_code", "lpn_id", "lpn_context", "lot_no", "grade_code",
    "no_pcs", "ctn_type", "created_by", "creation_date",
]
_ORACLE_SELECT = queries.CARTON_SELECT_COLUMNS


def _carton_oracle_row_to_dict(r) -> Dict[str, Any]:
    return {col: r[i] for i, col in enumerate(_ORACLE_COLS)}


def _carton_mock_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "carton_code": r.get("CARTON_CODE"),
        "qr_code": r.get("QR_CODE"),
        "brand": r.get("BRAND"),
        "item_code": r.get("ITEM_CODE"),
        "item_desc": r.get("ITEM_DESC"),
        "lot_number": r.get("LOT_NUMBER"),
        "shift": r.get("SHIFT"),
        "grade": r.get("GRADE"),
        "size_code": r.get("SIZE_CODE"),
        "batch_no": r.get("BATCH_NO"),
        "batch_date": r.get("BATCH_DATE"),
        "batch_time": r.get("BATCH_TIME"),
        "status": r.get("STATUS"),
        "carton_id": r.get("CARTON_ID"),
        "organization_id": r.get("ORGANIZATION_ID"),
        "inventory_item_id": r.get("INVENTORY_ITEM_ID"),
        "carton_qty": r.get("CARTON_QTY"),
        "uom_code": r.get("UOM_CODE"),
        "lpn_id": r.get("LPN_ID"),
        "lpn_context": r.get("LPN_CONTEXT"),
        "lot_no": r.get("LOT_NO"),
        "grade_code": r.get("GRADE_CODE"),
        "no_pcs": r.get("NO_PCS"),
        "ctn_type": r.get("CTN_TYPE"),
        "total_planned_qty": r.get("TOTAL_PLANNED_QTY"),
        "plant_code": r.get("PLANT_CODE"),
        "created_by": r.get("CREATED_BY"),
        "creation_date": r.get("CREATION_DATE"),
    }
    return out


def get_carton(carton_code: str) -> Optional[Dict[str, Any]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            for r in db.MOCK_CARTONS:
                if r.get("CARTON_CODE") == carton_code:
                    return _carton_mock_to_dict(r)
        return None
    sql = queries.GET_CARTON.sql.format(
        select_cols=_ORACLE_SELECT,
        carton_table=settings.CARTON_TABLE,
    )
    with db.cursor() as cur:
        cur.execute(sql, cc=carton_code)
        row = cur.fetchone()
        if not row:
            return None
        return _carton_oracle_row_to_dict(row)


def _passes_mock_filter(r: Dict[str, Any], f: Dict[str, Any]) -> bool:
    st = (r.get("STATUS") or "").upper()
    if not f.get("include_deleted") and st == "DELETED":
        return False
    if f.get("brand") and r.get("BRAND") != f["brand"]: return False
    if f.get("shift") and r.get("SHIFT") != f["shift"]: return False
    if f.get("grade") and r.get("GRADE") != f["grade"]: return False
    if f.get("status") and f["status"].upper() not in st: return False
    if f.get("item_code_like") and f["item_code_like"].upper() not in (r.get("ITEM_CODE") or "").upper():
        return False
    if f.get("lot_like") and f["lot_like"].upper() not in (r.get("LOT_NUMBER") or "").upper():
        return False
    if f.get("lpn_like") and f["lpn_like"].upper() not in (r.get("CARTON_CODE") or "").upper():
        return False
    bd = r.get("BATCH_DATE")
    if f.get("date_from") and bd and bd < f["date_from"]: return False
    if f.get("date_to") and bd and bd > f["date_to"]: return False
    return True


def query_cartons(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            rows = [dict(r) for r in db.MOCK_CARTONS]
        offset = max(0, int(f.get("offset") or 0))
        limit = int(f.get("limit") or 200)
        filtered = [_carton_mock_to_dict(r) for r in rows if _passes_mock_filter(r, f)]
        return filtered[offset:offset + limit]

    where: List[str] = []
    binds: Dict[str, Any] = {}
    if not f.get("include_deleted"):
        where.append("(STATUS IS NULL OR STATUS <> 'DELETED')")
    if f.get("date_from"):       where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = f["date_from"]
    if f.get("date_to"):         where.append("BATCH_DATE <= :dto");   binds["dto"]   = f["date_to"]
    if f.get("brand"):           where.append("BRAND = :brand");        binds["brand"] = f["brand"]
    if f.get("shift"):           where.append("SHIFT = :shift");        binds["shift"] = f["shift"]
    if f.get("grade"):           where.append("GRADE = :grade");        binds["grade"] = f["grade"]
    if f.get("status"):          where.append("STATUS LIKE :status");   binds["status"] = f"%{f['status']}%"
    if f.get("item_code_like"):
        where.append("UPPER(ITEM_CODE) LIKE :item")
        binds["item"] = f"%{f['item_code_like'].upper()}%"
    if f.get("lot_like"):
        where.append("UPPER(LOT_NUMBER) LIKE :lot")
        binds["lot"] = f"%{f['lot_like'].upper()}%"
    if f.get("lpn_like"):
        where.append("UPPER(CARTON_CODE) LIKE :lpn")
        binds["lpn"] = f"%{f['lpn_like'].upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = queries.QUERY_CARTONS.sql.format(
        select_cols=_ORACLE_SELECT,
        carton_table=settings.CARTON_TABLE,
        where_sql=where_sql,
    )
    binds["lim"] = f.get("limit", 200)
    binds["off"] = max(0, int(f.get("offset") or 0))
    with db.cursor() as cur:
        cur.execute(sql, binds)
        return [_carton_oracle_row_to_dict(r) for r in cur.fetchall()]


def count_query_cartons(f: Dict[str, Any]) -> int:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            return sum(1 for r in db.MOCK_CARTONS if _passes_mock_filter(r, f))

    where: List[str] = []
    binds: Dict[str, Any] = {}
    if not f.get("include_deleted"):
        where.append("(STATUS IS NULL OR STATUS <> 'DELETED')")
    if f.get("date_from"):       where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = f["date_from"]
    if f.get("date_to"):         where.append("BATCH_DATE <= :dto");   binds["dto"]   = f["date_to"]
    if f.get("brand"):           where.append("BRAND = :brand");        binds["brand"] = f["brand"]
    if f.get("shift"):           where.append("SHIFT = :shift");        binds["shift"] = f["shift"]
    if f.get("grade"):           where.append("GRADE = :grade");        binds["grade"] = f["grade"]
    if f.get("status"):          where.append("STATUS LIKE :status");   binds["status"] = f"%{f['status']}%"
    if f.get("item_code_like"):
        where.append("UPPER(ITEM_CODE) LIKE :item")
        binds["item"] = f"%{f['item_code_like'].upper()}%"
    if f.get("lot_like"):
        where.append("UPPER(LOT_NUMBER) LIKE :lot")
        binds["lot"] = f"%{f['lot_like'].upper()}%"
    if f.get("lpn_like"):
        where.append("UPPER(CARTON_CODE) LIKE :lpn")
        binds["lpn"] = f"%{f['lpn_like'].upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = queries.COUNT_QUERY_CARTONS.sql.format(
        carton_table=settings.CARTON_TABLE,
        where_sql=where_sql,
    )
    with db.cursor() as cur:
        cur.execute(sql, binds)
        return int(cur.fetchone()[0])


def count_cartons(scope: str) -> int:
    if scope not in ("today", "total"):
        raise ValueError("scope must be 'today' or 'total'")
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            if scope == "today":
                today = datetime.now().date()
                return sum(
                    1 for r in db.MOCK_CARTONS
                    if r.get("BATCH_DATE") and r["BATCH_DATE"].date() == today
                    and (r.get("STATUS") or "").upper() != "DELETED"
                )
            return sum(
                1 for r in db.MOCK_CARTONS
                if (r.get("STATUS") or "").upper() != "DELETED"
            )
    with db.cursor() as cur:
        if scope == "today":
            cur.execute(
                queries.COUNT_TODAY.sql.format(carton_table=settings.CARTON_TABLE)
            )
        else:
            cur.execute(
                queries.COUNT_TOTAL.sql.format(carton_table=settings.CARTON_TABLE)
            )
        return int(cur.fetchone()[0])


# ====================================================================
# Batch master
# ====================================================================
_BATCH_COLS = (
    "batch_id", "organization_id", "batch_no", "production_date",
    "item_code", "product_type", "size_code", "uom_code", "batch_date",
    "lot_no", "brand", "grade", "shift", "status",
    "production_qty", "produced_carton_qty",
)


def query_batches(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            rows = [dict(r) for r in db.MOCK_BATCHES]
        out: List[Dict[str, Any]] = []
        for r in rows:
            if f.get("brand") and r.get("BRAND") != f["brand"]: continue
            if f.get("shift") and r.get("SHIFT") != f["shift"]: continue
            if f.get("grade") and r.get("GRADE") != f["grade"]: continue
            if f.get("status") and (r.get("STATUS") or "").upper() != f["status"].upper(): continue
            if f.get("item_code_like") and f["item_code_like"].upper() not in (r.get("ITEM_CODE") or "").upper():
                continue
            if f.get("lot_like") and f["lot_like"].upper() not in (r.get("LOT_NO") or "").upper():
                continue
            if f.get("batch_no_like") and f["batch_no_like"].upper() not in (r.get("BATCH_NO") or "").upper():
                continue
            bd = r.get("BATCH_DATE")
            if f.get("date_from") and bd and bd < f["date_from"]: continue
            if f.get("date_to") and bd and bd > f["date_to"]: continue
            out.append({k.lower(): r.get(k) for k in (
                "BATCH_ID", "ORGANIZATION_ID", "BATCH_NO", "PRODUCTION_DATE",
                "ITEM_CODE", "PRODUCT_TYPE", "SIZE_CODE", "UOM_CODE", "BATCH_DATE",
                "LOT_NO", "BRAND", "GRADE", "SHIFT", "STATUS",
                "PRODUCTION_QTY", "PRODUCED_CARTON_QTY",
            )})
        offset = max(0, int(f.get("offset") or 0))
        limit = int(f.get("limit") or 500)
        return out[offset:offset + limit]
    where: List[str] = []
    binds: Dict[str, Any] = {}
    if f.get("date_from"):  where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = f["date_from"]
    if f.get("date_to"):    where.append("BATCH_DATE <= :dto");   binds["dto"]   = f["date_to"]
    if f.get("brand"):      where.append("BRAND = :brand");        binds["brand"] = f["brand"]
    if f.get("shift"):      where.append("SHIFT = :shift");        binds["shift"] = f["shift"]
    if f.get("grade"):      where.append("GRADE = :grade");        binds["grade"] = f["grade"]
    if f.get("status"):     where.append("STATUS = :status");      binds["status"] = f["status"].upper()
    if f.get("item_code_like"):
        where.append("UPPER(ITEM_CODE) LIKE :item")
        binds["item"] = f"%{f['item_code_like'].upper()}%"
    if f.get("lot_like"):
        where.append("UPPER(LOT_NO) LIKE :lot")
        binds["lot"] = f"%{f['lot_like'].upper()}%"
    if f.get("batch_no_like"):
        where.append("UPPER(BATCH_NO) LIKE :bn")
        binds["bn"] = f"%{f['batch_no_like'].upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = queries.QUERY_BATCHES.sql.format(
        batch_table=settings.CARTON_BATCH_TABLE,
        where_sql=where_sql,
    )
    binds["lim"] = f.get("limit", 500)
    binds["off"] = max(0, int(f.get("offset") or 0))
    with db.cursor() as cur:
        cur.execute(sql, binds)
        return [dict(zip(_BATCH_COLS, r)) for r in cur.fetchall()]


def count_query_batches(f: Dict[str, Any]) -> int:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            rows = [dict(r) for r in db.MOCK_BATCHES]
        total = 0
        for r in rows:
            if f.get("brand") and r.get("BRAND") != f["brand"]: continue
            if f.get("shift") and r.get("SHIFT") != f["shift"]: continue
            if f.get("grade") and r.get("GRADE") != f["grade"]: continue
            if f.get("status") and (r.get("STATUS") or "").upper() != f["status"].upper(): continue
            if f.get("item_code_like") and f["item_code_like"].upper() not in (r.get("ITEM_CODE") or "").upper():
                continue
            if f.get("lot_like") and f["lot_like"].upper() not in (r.get("LOT_NO") or "").upper():
                continue
            if f.get("batch_no_like") and f["batch_no_like"].upper() not in (r.get("BATCH_NO") or "").upper():
                continue
            bd = r.get("BATCH_DATE")
            if f.get("date_from") and bd and bd < f["date_from"]: continue
            if f.get("date_to") and bd and bd > f["date_to"]: continue
            total += 1
        return total

    where: List[str] = []
    binds: Dict[str, Any] = {}
    if f.get("date_from"):  where.append("BATCH_DATE >= :dfrom"); binds["dfrom"] = f["date_from"]
    if f.get("date_to"):    where.append("BATCH_DATE <= :dto");   binds["dto"]   = f["date_to"]
    if f.get("brand"):      where.append("BRAND = :brand");        binds["brand"] = f["brand"]
    if f.get("shift"):      where.append("SHIFT = :shift");        binds["shift"] = f["shift"]
    if f.get("grade"):      where.append("GRADE = :grade");        binds["grade"] = f["grade"]
    if f.get("status"):     where.append("STATUS = :status");      binds["status"] = f["status"].upper()
    if f.get("item_code_like"):
        where.append("UPPER(ITEM_CODE) LIKE :item")
        binds["item"] = f"%{f['item_code_like'].upper()}%"
    if f.get("lot_like"):
        where.append("UPPER(LOT_NO) LIKE :lot")
        binds["lot"] = f"%{f['lot_like'].upper()}%"
    if f.get("batch_no_like"):
        where.append("UPPER(BATCH_NO) LIKE :bn")
        binds["bn"] = f"%{f['batch_no_like'].upper()}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = queries.COUNT_QUERY_BATCHES.sql.format(
        batch_table=settings.CARTON_BATCH_TABLE,
        where_sql=where_sql,
    )
    with db.cursor() as cur:
        cur.execute(sql, binds)
        return int(cur.fetchone()[0])


def batch_status(batch_no: str, org_id: str) -> Dict[str, bool]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            exists = False
            finalized = False
            for r in db.MOCK_BATCHES:
                if r.get("BATCH_NO") == batch_no and (
                    not org_id or str(r.get("ORGANIZATION_ID") or "") == org_id
                ):
                    exists = True
                    if (r.get("STATUS") or "").upper() == "Y":
                        finalized = True
            return {"exists": exists, "finalized": finalized}
    with db.cursor() as cur:
        cur.execute(
            queries.BATCH_STATUS.sql.format(batch_table=settings.CARTON_BATCH_TABLE),
            bn=batch_no, org=org_id or "",
        )
        row = cur.fetchone()
        if not row:
            return {"exists": False, "finalized": False}
        finalized = (int(row[0] or 0) == 1)
        exists = (int(row[1] or 0) > 0)
        return {"exists": exists, "finalized": finalized}


def insert_initial_batch(row: Dict[str, Any]) -> Tuple[bool, str, Optional[int]]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            db.MOCK_BATCH_SEQ[0] += 1
            r = {
                "BATCH_ID": db.MOCK_BATCH_SEQ[0],
                "ORGANIZATION_ID": row.get("ORGANIZATION_ID"),
                "BATCH_NO": row.get("BATCH_NO"),
                "PRODUCTION_DATE": row.get("BATCH_DATE"),
                "ITEM_CODE": row.get("ITEM_CODE"),
                "PRODUCT_TYPE": row.get("CTN_TYPE"),
                "SIZE_CODE": row.get("SIZE_CODE"),
                "UOM_CODE": row.get("UOM_CODE"),
                "BATCH_DATE": row.get("BATCH_DATE"),
                "LOT_NO": row.get("LOT_NUMBER"),
                "BRAND": row.get("BRAND"),
                "GRADE": row.get("GRADE"),
                "SHIFT": row.get("SHIFT"),
                "STATUS": "N",
                "PRODUCTION_QTY": float(row.get("NO_PCS") or 0),
                "PRODUCED_CARTON_QTY": float(row.get("CARTON_QTY") or 0),
            }
            db.MOCK_BATCHES.insert(0, r)
            return True, "", r["BATCH_ID"]
    sql = queries.INSERT_INITIAL_BATCH.sql.format(
        batch_table=settings.CARTON_BATCH_TABLE,
        batch_seq=settings.CARTON_BATCH_SEQ,
    )
    try:
        with db.cursor() as cur:
            cur.execute(sql,
                org=row.get("ORGANIZATION_ID"),
                bn=row.get("BATCH_NO"),
                pdate=row.get("BATCH_DATE"),
                item=row.get("ITEM_CODE"),
                ptype=row.get("CTN_TYPE"),
                sz_c=row.get("SIZE_CODE"),
                uom_c=row.get("UOM_CODE"),
                bdate=row.get("BATCH_DATE"),
                lot=row.get("LOT_NUMBER"),
                brand=row.get("BRAND"),
                grade=row.get("GRADE"),
                shift=row.get("SHIFT"),
                pqty=float(row.get("NO_PCS") or 0),
                cqty=float(row.get("CARTON_QTY") or 0),
            )
            cur.execute(
                queries.SELECT_LATEST_BATCH_ID.sql.format(
                    batch_table=settings.CARTON_BATCH_TABLE
                ),
                bn=row.get("BATCH_NO"),
            )
            r2 = cur.fetchone()
            return True, "", int(r2[0]) if r2 else None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None


def finalize_batch(batch_no: str, org_id: str) -> Tuple[bool, str, int]:
    if settings.USE_MOCK_DB:
        with db.mock_lock:
            n = 0
            for r in db.MOCK_BATCHES:
                if r.get("BATCH_NO") == batch_no and (
                    not org_id or str(r.get("ORGANIZATION_ID") or "") == org_id
                ):
                    r["STATUS"] = "Y"
                    n += 1
            return True, "", n
    sql = queries.FINALIZE_BATCH.sql.format(
        batch_table=settings.CARTON_BATCH_TABLE,
        batch_seq=settings.CARTON_BATCH_SEQ,
        carton_table=settings.CARTON_TABLE,
    )
    try:
        with db.cursor() as cur:
            cur.execute(sql, bn=batch_no, org=org_id or "")
            return True, "", cur.rowcount or 0
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0
