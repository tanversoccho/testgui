"""Side-by-side audit of core/database.py vs latepanda_app/core/database.py."""
import re
from pathlib import Path


def audit(path: str) -> None:
    src = Path(path).read_text(encoding="utf-8-sig")
    print(f"=== {path} ===")

    # INSERTED_COLUMNS list
    m = re.search(r"INSERTED_COLUMNS\s*=\s*\(([^)]+)\)", src, re.S)
    raw = m.group(1).replace("\n", " ")
    cols = {tok.strip(' "') for tok in raw.split(",") if tok.strip(' "')}
    must_have = {"PLANT_CODE", "TOTAL_PLANNED_QTY", "CARTON_CODE",
                 "STATUS", "LPN_ID", "CARTON_ID", "QR_CODE",
                 "ORGANIZATION_ID", "ITEM_CODE", "BRAND",
                 "CREATED_BY", "CREATION_DATE"}
    missing = must_have - cols
    print(f"  INSERTED_COLUMNS ({len(cols)} cols) has all required: "
          f"{not missing}  (missing: {missing})")

    # Critical function presence
    must_exist = [
        "insert_carton", "mark_reprint", "soft_delete",
        "query_history", "query_batches", "count_today", "count_total",
        "find_duplicate", "fetch_brands", "fetch_item_codes",
        "fetch_sample_config", "fetch_normal_pcs_per_ctn",
        "fetch_max_lpn_counter_today", "reset_connections",
        "test_connection", "current_mode",
    ]
    fns = set(re.findall(r"^def (\w+)\(", src, re.M))
    not_found = [f for f in must_exist if f not in fns]
    print(f"  All {len(must_exist)} required functions present: "
          f"{not not_found}  (missing: {not_found})")

    # batch_master WRITE functions must NOT exist
    forbidden = ["batch_master_insert_initial", "batch_master_finalize",
                 "batch_master_is_finalized", "batch_master_has_row"]
    found = [f for f in forbidden if f in fns]
    print(f"  batch_master WRITE helpers removed: "
          f"{not found}  (still present: {found})")

    # fetch_normal_pcs_per_ctn — simple SELECT, no UOM-code filter
    m = re.search(
        r"def fetch_normal_pcs_per_ctn[^\n]*\n(.*?)(?=^def |\Z)",
        src, re.S | re.M,
    )
    body = m.group(0) if m else ""
    has_bad_uom_filter = "PRIMARY_UOM_CODE = 'CTN'" in body
    has_correct_sql = (
        "SELECT CONVERSION_RATE" in body
        and "FROM APPS.XXFG_UOM_CONVERSIONS_V" in body
        and not has_bad_uom_filter
    )
    print(f"  fetch_normal_pcs_per_ctn — correct simple SELECT: "
          f"{has_correct_sql}  (wrong UOM filter present: {has_bad_uom_filter})")

    # CartonRow + BatchRow dataclasses
    has_carton_row = re.search(r"class CartonRow", src) is not None
    has_batch_row  = re.search(r"class BatchRow", src) is not None
    print(f"  CartonRow + BatchRow dataclasses: "
          f"{has_carton_row and has_batch_row}")

    print()


for p in ("core/database.py", "latepanda_app/core/database.py"):
    audit(p)
