"""Coverage audit — for every item the form can pick, does the
XXFG_UOM_CONVERSIONS_V view actually return a row?

Reports per brand:
  * total items in XXFG_ORG_ITEMS
  * items WITH a row in XXFG_UOM_CONVERSIONS_V
  * items MISSING from the view (grouped by size CAT5)
  * distinct (PRIMARY_UOM_CODE, TARGET_UOM_CODE) pairs actually in use

Run via:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
        desktop_app\\.venv\\Scripts\\python.exe \
        tests\\check_uom_coverage.py
"""
from __future__ import annotations
import sys


def main() -> int:
    from config import config_loader, config_app
    config_loader.load()
    config_app.USE_MOCK_DB = False

    from core import database
    print(f"Connecting to {config_app.DB_HOST}:{config_app.DB_PORT}/"
          f"{config_app.DB_SERVICE} as {config_app.DB_USER} …")
    try:
        conn = database._connect()
    except Exception as e:
        print(f"DB connect FAILED: {type(e).__name__}: {e}")
        return 2
    print(f"Connected.  {database.current_mode()}")

    with conn.cursor() as cur:
        # 1) Distinct (PRIMARY, TARGET) UOM pairs across the WHOLE view
        print()
        print("=" * 72)
        print("1. Distinct (PRIMARY, TARGET) UOM pairs in XXFG_UOM_CONVERSIONS_V")
        print("=" * 72)
        cur.execute("""
            SELECT PRIMARY_UOM_CODE, TARGET_UOM_CODE, COUNT(*) c
              FROM APPS.XXFG_UOM_CONVERSIONS_V
             GROUP BY PRIMARY_UOM_CODE, TARGET_UOM_CODE
             ORDER BY c DESC
        """)
        for r in cur.fetchall():
            print(f"   PRIMARY={r[0]!r:<8} TARGET={r[1]!r:<8} rows={r[2]}")

        # 2) Per-brand coverage report
        print()
        print("=" * 72)
        print("2. Per-brand coverage of XXFG_UOM_CONVERSIONS_V")
        print("=" * 72)
        brands = list(config_app.BRANDS)   # [(name, org_code, inv_code), ...]
        for (name, org_code, inv_code) in brands:
            cur.execute("""
                SELECT COUNT(DISTINCT ITEM_CODE)
                  FROM APPS.XXFG_ORG_ITEMS
                 WHERE ORGANIZATION_ID = :org
                   AND ITEM_CODE IS NOT NULL
            """, org=inv_code)
            total_items = int(cur.fetchone()[0] or 0)

            cur.execute("""
                SELECT COUNT(DISTINCT i.ITEM_CODE)
                  FROM APPS.XXFG_ORG_ITEMS i
                 WHERE i.ORGANIZATION_ID = :org
                   AND i.ITEM_CODE IS NOT NULL
                   AND EXISTS (
                       SELECT 1
                         FROM APPS.XXFG_UOM_CONVERSIONS_V v
                        WHERE v.ORGANIZATION_ID = i.ORGANIZATION_ID
                          AND v.ITEM_CODE        = i.ITEM_CODE
                          AND (v.DISABLE_DATE IS NULL OR v.DISABLE_DATE > SYSDATE)
                   )
            """, org=inv_code)
            covered = int(cur.fetchone()[0] or 0)
            missing = total_items - covered
            pct = (covered / total_items * 100.0) if total_items else 0.0
            print(f"  {name:<14} (org_code={org_code}, inv_code={inv_code}): "
                  f"{covered:>5} / {total_items:>5} covered  "
                  f"({pct:5.1f} %)  |  missing: {missing}")

        # 3) For each brand, list the missing items grouped by size (CAT5)
        print()
        print("=" * 72)
        print("3. Items WITHOUT a UOM-view row — grouped by size (CAT5)")
        print("=" * 72)
        for (name, org_code, inv_code) in brands:
            cur.execute("""
                SELECT NVL(CAT5, '(no size)'), COUNT(DISTINCT ITEM_CODE)
                  FROM APPS.XXFG_ORG_ITEMS i
                 WHERE i.ORGANIZATION_ID = :org
                   AND i.ITEM_CODE IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM APPS.XXFG_UOM_CONVERSIONS_V v
                        WHERE v.ORGANIZATION_ID = i.ORGANIZATION_ID
                          AND v.ITEM_CODE        = i.ITEM_CODE
                          AND (v.DISABLE_DATE IS NULL OR v.DISABLE_DATE > SYSDATE)
                   )
                 GROUP BY CAT5
                 ORDER BY COUNT(DISTINCT ITEM_CODE) DESC, NVL(CAT5, '(no size)')
            """, org=inv_code)
            rows = cur.fetchall()
            if not rows:
                print(f"  {name:<14}: every item is covered.")
                continue
            print(f"  {name:<14} — missing items per size:")
            for size, n in rows:
                print(f"      {size:<16}  {n:>4} items")

        # 4) Show up to 20 example missing item codes per brand, for
        #    spot-checking against the live DB.
        print()
        print("=" * 72)
        print("4. Sample missing ITEM_CODEs (first 20 per brand)")
        print("=" * 72)
        for (name, org_code, inv_code) in brands:
            cur.execute("""
                SELECT i.ITEM_CODE, NVL(i.CAT5, '(no size)')
                  FROM APPS.XXFG_ORG_ITEMS i
                 WHERE i.ORGANIZATION_ID = :org
                   AND i.ITEM_CODE IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM APPS.XXFG_UOM_CONVERSIONS_V v
                        WHERE v.ORGANIZATION_ID = i.ORGANIZATION_ID
                          AND v.ITEM_CODE        = i.ITEM_CODE
                          AND (v.DISABLE_DATE IS NULL OR v.DISABLE_DATE > SYSDATE)
                   )
                 ORDER BY i.ITEM_CODE
                 FETCH FIRST 20 ROWS ONLY
            """, org=inv_code)
            rows = cur.fetchall()
            if not rows:
                continue
            print(f"  {name:<14}:")
            for code, size in rows:
                print(f"      {code:<14} (size={size})")

        # 5) Spot-check the helper against the items shown in your
        #    screenshot, so we can see live CONVERSION_RATE values.
        print()
        print("=" * 72)
        print("5. Spot-check fetch_normal_pcs_per_ctn() on screenshot items")
        print("=" * 72)
        SAMPLES = [
            (370, "AHSTP202"),   # 26X30 stair
            (370, "BW102BR"),    # 20x7.5 border
            (370, "BW114L"),
            (370, "BW151G"),
            (370, "BW152BR"),    # the item the screenshot's WHERE used
            (370, "AAS60601"),   # 60X60 — what the form tests with
        ]
        for org, code in SAMPLES:
            rate = database.fetch_normal_pcs_per_ctn(org, code)
            tag = "OK" if rate is not None else "MISSING"
            print(f"   org={org}  item={code:<10}  -> "
                  f"{tag:<7} CONVERSION_RATE = {rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
