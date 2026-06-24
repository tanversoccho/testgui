"""Pull a few sample rows from XXFG_UOM_CONVERSIONS_V to understand the
direction of CONVERSION_RATE (PCS-per-CTN vs CTN-per-PCS) and what
PRIMARY / TARGET UOM codes look like in practice.

Run via:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
        desktop_app\\.venv\\Scripts\\python.exe \
        tests\\inspect_uom_rows.py
"""
from __future__ import annotations
import sys


def main() -> int:
    from config import config_loader, config_app
    config_loader.load()
    config_app.USE_MOCK_DB = False

    from core import database
    conn = database._connect()
    with conn.cursor() as cur:
        # 1) Distinct PRIMARY/TARGET UOM combinations on the view —
        #    tells us what conversion directions are stored.
        print("=" * 72)
        print("Distinct (PRIMARY_UOM_CODE, TARGET_UOM_CODE) pairs + row counts")
        print("=" * 72)
        cur.execute("""
            SELECT PRIMARY_UOM_CODE, TARGET_UOM_CODE, COUNT(*) c
              FROM APPS.XXFG_UOM_CONVERSIONS_V
             GROUP BY PRIMARY_UOM_CODE, TARGET_UOM_CODE
             ORDER BY c DESC
        """)
        for r in cur.fetchall():
            print(f"  PRIMARY={r[0]!r:<6} TARGET={r[1]!r:<6}  rows={r[2]}")

        # 2) Look at Alexander (ORGANIZATION_ID = 370) — the brand the
        #    user has been testing with — and pick AAS60601 if present.
        print()
        print("=" * 72)
        print("Sample rows for Alexander (org_id=370)")
        print("=" * 72)
        cur.execute("""
            SELECT ORGANIZATION_ID, ITEM_CODE, PRIMARY_UOM_CODE,
                   TARGET_UOM_CODE, CONVERSION_RATE, DISABLE_DATE
              FROM APPS.XXFG_UOM_CONVERSIONS_V
             WHERE ORGANIZATION_ID = 370
             ORDER BY ITEM_CODE, PRIMARY_UOM_CODE, TARGET_UOM_CODE
             FETCH FIRST 20 ROWS ONLY
        """)
        rows = cur.fetchall()
        if not rows:
            print("  (no rows for org 370)")
        else:
            print(f"{'ITEM_CODE':<12} {'PRIM':<5} {'TARG':<5} "
                  f"{'CONV_RATE':>12}  DISABLE")
            print("-" * 60)
            for r in rows:
                print(f"{r[1]:<12} {r[2] or '':<5} {r[3] or '':<5} "
                      f"{r[4]!s:>12}  {r[5]}")

        # 3) Look at the carton form's test item AAS60601 across all
        #    organizations.
        print()
        print("=" * 72)
        print("All rows for ITEM_CODE = 'AAS60601' (any org)")
        print("=" * 72)
        cur.execute("""
            SELECT ORGANIZATION_ID, ITEM_CODE, PRIMARY_UOM_CODE,
                   TARGET_UOM_CODE, CONVERSION_RATE, DISABLE_DATE
              FROM APPS.XXFG_UOM_CONVERSIONS_V
             WHERE ITEM_CODE = 'AAS60601'
             ORDER BY ORGANIZATION_ID, PRIMARY_UOM_CODE, TARGET_UOM_CODE
        """)
        rows = cur.fetchall()
        if not rows:
            print("  (no rows for AAS60601)")
        else:
            print(f"{'ORG':>5} {'PRIM':<5} {'TARG':<5} "
                  f"{'CONV_RATE':>12}  DISABLE")
            print("-" * 50)
            for r in rows:
                print(f"{r[0]:>5} {r[2] or '':<5} {r[3] or '':<5} "
                      f"{r[4]!s:>12}  {r[5]}")

        # 4) Compare against fallback sample-config values — sanity
        #    check that the live values look like a per-carton PCS count
        #    in the same ballpark as our FALLBACK_SAMPLE_CONFIG.
        print()
        print("=" * 72)
        print("Reference: FALLBACK_SAMPLE_CONFIG (offline / dev defaults)")
        print("=" * 72)
        for size, (n, s, c) in config_app.FALLBACK_SAMPLE_CONFIG.items():
            print(f"  {size:<14}  normal_pcs={n:<3}  sample_pcs={s:<3}  "
                  f"conversion={c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
