"""End-to-end verification: dumps the SQL the app actually sends to
Oracle for insert_carton(), and shows the bind values for the new
PLANT_CODE and TOTAL_PLANNED_QTY columns.

Run via:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
      desktop_app/.venv/Scripts/python.exe tests/verify_plant_code.py

Does NOT connect to Oracle — reconstructs the SQL the same way the
live insert path does, with the same column list and bind names, so
you can compare against what the live `oracledb` cursor would send.

XXFG_CARTON_BATCH_MASTER is no longer touched from the app — a DB
trigger maintains it automatically. The History dialog's Batches view
still reads it (query_batches), so a sanity SELECT for that table is
included at the bottom.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime


def main() -> int:
    from config import config_app
    config_app.USE_MOCK_DB = True   # so we don't actually open Oracle

    from core import database, lpn_generator
    from core.carton_model import CartonLabel

    # -------------------------------------------------------------
    # Build a CartonLabel exactly as form_panel.collect() would,
    # picking brand "Alexander" which has org_code='064', inv_code=370.
    # -------------------------------------------------------------
    c = CartonLabel(
        brand="Alexander",
        organization_id="370",   # ← inv_code: ORGANIZATION_ID column (VARCHAR2)
        plant_code="064",        # ← org_code: PLANT_CODE column
        item_code="AAS60601",
        item_desc="",
        lot_number="L-V",
        sn="M-123S",            # printed in QR only — NOT a DB column
        grade="A",
        shift="E",
        size_code="60X60",
        pcs_type="Regular",
        carton_qty=1.0,
        pcs_per_ctn=4,
        batch_date=datetime(2026, 6, 17, 14, 48),
        batch_time="02:48 PM",
        total_planned_qty=1500.0,   # ← TOTAL_PLANNED_QTY column (replaces batch_status)
    )
    c.lpn_id = lpn_generator.consume_next()
    c.carton_code = c.lpn_id
    db_row = c.as_db_row()
    db_row["CARTON_ID"] = 12345   # what _next_carton_id() would return in live mode

    # =============================================================
    # XXFG_CARTON_MASTER INSERT
    # =============================================================
    cols = ", ".join(database.INSERTED_COLUMNS)
    placeholders = ", ".join(f":{col}" for col in database.INSERTED_COLUMNS)
    carton_sql = (
        f"INSERT INTO {config_app.CARTON_TABLE} ({cols}) "
        f"VALUES ({placeholders})"
    )
    binds = {col: db_row.get(col) for col in database.INSERTED_COLUMNS}

    print("=" * 78)
    print("XXFG_CARTON_MASTER  (insert_carton)")
    print("=" * 78)
    print(f"TABLE: {config_app.CARTON_TABLE}")
    print()
    print("SQL:")
    print(carton_sql)
    print()
    print("BINDS:")
    for col in database.INSERTED_COLUMNS:
        marker = ""
        if col == "PLANT_CODE":
            marker = "  <-- PLANT_CODE column"
        elif col == "TOTAL_PLANNED_QTY":
            marker = "  <-- TOTAL_PLANNED_QTY column (replaces batch_status)"
        print(f"  :{col:<22}= {binds[col]!r}{marker}")

    assert "PLANT_CODE" in database.INSERTED_COLUMNS, "PLANT_CODE missing!"
    assert "TOTAL_PLANNED_QTY" in database.INSERTED_COLUMNS, "TOTAL_PLANNED_QTY missing!"
    assert binds["PLANT_CODE"] == "064"
    assert binds["TOTAL_PLANNED_QTY"] == 1500.0
    print()
    print("PASS: PLANT_CODE='064' and TOTAL_PLANNED_QTY=1500.0 bind correctly.")

    # =============================================================
    # ROUND-TRIP through database.insert_carton (in-memory store)
    # =============================================================
    print()
    print("=" * 78)
    print("ROUND-TRIP through database.insert_carton")
    print("=" * 78)
    ok, msg = database.insert_carton(db_row)
    assert ok, msg
    last = database._MEM_ROWS[0]
    assert last["PLANT_CODE"] == "064", last
    assert last["TOTAL_PLANNED_QTY"] == 1500.0, last
    print(f"insert_carton OK -> CARTON_MASTER row "
          f"PLANT_CODE={last['PLANT_CODE']!r}, "
          f"TOTAL_PLANNED_QTY={last['TOTAL_PLANNED_QTY']!r}")

    # batch_master_* write helpers are GONE — DB trigger handles
    # XXFG_CARTON_BATCH_MASTER. Confirm they are no longer importable.
    print()
    print("=" * 78)
    print("XXFG_CARTON_BATCH_MASTER — no longer written from the app")
    print("=" * 78)
    for fn in ("batch_master_insert_initial", "batch_master_finalize",
               "batch_master_is_finalized", "batch_master_has_row"):
        assert not hasattr(database, fn), f"database.{fn} should be removed"
        print(f"  removed: database.{fn}")

    # =============================================================
    # SELECTs to confirm on the live Oracle
    # =============================================================
    print()
    print("=" * 78)
    print("SELECTs to run on the live Oracle to confirm:")
    print("=" * 78)
    print("""
-- 1. New columns landed on the carton_master row.
SELECT CARTON_ID, CARTON_CODE, ORGANIZATION_ID, PLANT_CODE,
       ITEM_CODE, BATCH_NO, STATUS, TOTAL_PLANNED_QTY, CREATION_DATE
  FROM APPS.XXFG_CARTON_MASTER
 ORDER BY CREATION_DATE DESC
 FETCH FIRST 5 ROWS ONLY;

-- 2. The DB trigger fed the batch_master from the carton_master insert.
--    PLANT_CODE should match between the two.
SELECT BATCH_ID, ORGANIZATION_ID, PLANT_CODE, BATCH_NO,
       ITEM_CODE, BRAND, STATUS, PRODUCTION_QTY, PRODUCED_CARTON_QTY
  FROM APPS.XXFG_CARTON_BATCH_MASTER
 ORDER BY BATCH_ID DESC
 FETCH FIRST 5 ROWS ONLY;

-- 3. Sanity: PLANT_CODE per brand should match config_app.BRANDS:
--    '089' Monalisa, '063' X Monica, '064' Alexander, '093' X Tiles, '062' Venus
SELECT BRAND, PLANT_CODE, COUNT(*) cnt
  FROM APPS.XXFG_CARTON_MASTER
 GROUP BY BRAND, PLANT_CODE
 ORDER BY BRAND, PLANT_CODE;
""")
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
