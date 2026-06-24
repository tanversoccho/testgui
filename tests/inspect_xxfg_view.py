"""Dump every column of XXFG_UMO_CONVERSIONS_V (or the UOM-spelled
variant) from the live Oracle DB via the project's own connection
layer.

Run via the project venv so oracledb + the Instant Client are loaded:

    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
        desktop_app\.venv\Scripts\python.exe \
        tests\inspect_xxfg_view.py
"""
from __future__ import annotations

import sys


CANDIDATES = [
    # (owner, name) — covers the most likely spellings + a synonym lookup
    ("APPS", "XXFG_UMO_CONVERSIONS_V"),
    ("APPS", "XXFG_UOM_CONVERSIONS_V"),
]


def _dump_columns(cur, owner: str, name: str) -> bool:
    """Pull column metadata for owner.name. Returns True if any row found."""
    cur.execute(
        """
        SELECT column_id, column_name, data_type, data_length,
               data_precision, data_scale, nullable
          FROM all_tab_columns
         WHERE owner = :o AND table_name = :n
         ORDER BY column_id
        """,
        o=owner, n=name,
    )
    rows = cur.fetchall()
    if not rows:
        return False
    print(f"\n=== {owner}.{name} ===")
    print(f"{'#':<3} {'COLUMN':<32} {'TYPE':<14} {'LEN':>6} {'PREC':>5} "
          f"{'SCALE':>5} {'NULL':<4}")
    print("-" * 75)
    for r in rows:
        cid, cname, dtype, dlen, dprec, dscale, dnull = r
        nullable = "Y" if dnull == "Y" else "N"
        # Build a friendly TYPE+size string
        typ = dtype
        if dtype in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR", "RAW"):
            typ = f"{dtype}({dlen})"
        elif dtype == "NUMBER" and dprec:
            typ = f"NUMBER({dprec}{f',{dscale}' if dscale else ''})"
        print(f"{cid:<3} {cname:<32} {typ:<14} {dlen if dlen else '':>6} "
              f"{dprec if dprec is not None else '':>5} "
              f"{dscale if dscale is not None else '':>5} {nullable:<4}")
    return True


def _resolve_synonym(cur, name: str) -> list[tuple[str, str]]:
    """Look up ALL_SYNONYMS for `name` (any owner). Returns [(owner, table_name), …]."""
    cur.execute(
        """
        SELECT owner, synonym_name, table_owner, table_name, db_link
          FROM all_synonyms
         WHERE synonym_name = :n
         ORDER BY owner
        """,
        n=name,
    )
    rows = cur.fetchall()
    out = []
    if rows:
        print(f"\nFound {len(rows)} synonym(s) for {name}:")
        for ow, syn, tow, tname, lnk in rows:
            print(f"  {ow}.{syn}  ->  {tow}.{tname}"
                  f"{('@' + lnk) if lnk else ''}")
            if tow and tname:
                out.append((tow, tname))
    return out


def _search_by_pattern(cur, pattern: str):
    """Fallback — fuzzy search ALL_OBJECTS for anything resembling the name."""
    cur.execute(
        """
        SELECT owner, object_name, object_type
          FROM all_objects
         WHERE object_name LIKE :p
           AND object_type IN ('TABLE', 'VIEW', 'SYNONYM',
                               'MATERIALIZED VIEW')
         ORDER BY owner, object_name
         FETCH FIRST 50 ROWS ONLY
        """,
        p=pattern,
    )
    rows = cur.fetchall()
    if not rows:
        print(f"  No objects match LIKE '{pattern}' in ALL_OBJECTS.")
        return
    print(f"\nFuzzy matches in ALL_OBJECTS for LIKE '{pattern}':")
    for ow, oname, otype in rows:
        print(f"  {ow:<20} {oname:<40} {otype}")


def main() -> int:
    # Load the project's config exactly as the live app does — same
    # Instant Client path, same DB host / service / user.
    from config import config_loader, config_app
    config_loader.load()
    config_app.USE_MOCK_DB = False    # force the live path

    print(f"Connecting to {config_app.DB_HOST}:{config_app.DB_PORT}/"
          f"{config_app.DB_SERVICE} as {config_app.DB_USER} …")

    from core import database
    try:
        conn = database._connect()
    except Exception as e:
        print(f"DB connect FAILED: {type(e).__name__}: {e}")
        return 2

    print(f"Connected.  {database.current_mode()}")

    with conn.cursor() as cur:
        any_found = False
        for owner, name in CANDIDATES:
            try:
                if _dump_columns(cur, owner, name):
                    any_found = True
            except Exception as e:
                print(f"  (query failed for {owner}.{name}: {e})")

        if not any_found:
            print("\nNo direct match — looking for synonyms …")
            for _, name in CANDIDATES:
                resolved = _resolve_synonym(cur, name)
                for ow, tname in resolved:
                    try:
                        if _dump_columns(cur, ow, tname):
                            any_found = True
                    except Exception as e:
                        print(f"  (query failed for {ow}.{tname}: {e})")

        if not any_found:
            print("\nStill nothing — fuzzy search ALL_OBJECTS …")
            _search_by_pattern(cur, "XXFG\\_U%CONVERSIONS%")
            _search_by_pattern(cur, "%UMO\\_CONVERSIONS%")
            _search_by_pattern(cur, "%UOM\\_CONVERSIONS%")

    return 0 if any_found else 1


if __name__ == "__main__":
    sys.exit(main())
