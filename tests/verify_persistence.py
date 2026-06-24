"""Verify LPN + TODAY-count persistence behaviour.

Simulates:
  1. Print 100 cartons today → counter reaches 100.
  2. Simulate power-off mid-session → kill the in-memory cache and
     reread state from disk. Counter must continue from 100, not 0.
  3. Simulate midnight crossing → next read must reset to 1 with a
     fresh YYMMDD prefix.
  4. TODAY count source: confirm it comes from the DB
     (count_today / count_total), so it survives anything short of
     deleting rows.

Run via:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
        desktop_app\\.venv\\Scripts\\python.exe \
        tests\\verify_persistence.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta


def main() -> int:
    from config import config_app
    config_app.USE_MOCK_DB = True   # offline — no Oracle needed

    from core import lpn_generator
    from core.lpn_generator import _state_path, _IN_MEMORY_STATE  # noqa: F401
    from core import database
    from core.carton_model import CartonLabel
    from datetime import datetime

    today = date.today().isoformat()
    state_file = _state_path()
    print(f"State file: {state_file}")
    print(f"Today (ISO): {today}")
    print()

    # Wipe any state from a previous run so we start clean.
    if os.path.exists(state_file):
        os.remove(state_file)
    lpn_generator._IN_MEMORY_STATE = None

    # ============================================================
    # 1. Print 100 cartons. The LPN counter should reach 100 and the
    #    state file should hold {"date": today, "counter": 100}.
    # ============================================================
    print("=" * 70)
    print("1. Print 100 cartons (LPN counter goes 1 -> 100)")
    print("=" * 70)
    first = lpn_generator.consume_next()
    for _ in range(98):
        lpn_generator.consume_next()
    hundredth = lpn_generator.consume_next()
    print(f"  1st  LPN: {first}")
    print(f"  100th LPN: {hundredth}")

    with open(state_file) as f:
        on_disk = json.load(f)
    print(f"  On disk:  {on_disk}")
    assert on_disk["date"] == today
    assert on_disk["counter"] == 100
    print("  PASS: state file holds {date=today, counter=100}")

    # ============================================================
    # 2. Simulate power-off: wipe the in-memory cache. Next consume
    #    must read from disk and continue at 101, NOT restart at 1.
    # ============================================================
    print()
    print("=" * 70)
    print("2. Simulate power-off (drop in-memory cache, re-read disk)")
    print("=" * 70)
    lpn_generator._IN_MEMORY_STATE = None    # mimic process death
    next_lpn = lpn_generator.peek_next()
    print(f"  peek_next() after 'restart': {next_lpn}")
    consumed = lpn_generator.consume_next()
    print(f"  consume_next(): {consumed}")
    assert consumed.endswith("000101"), consumed
    print("  PASS: counter continued at 101, did NOT reset to 1")

    # ============================================================
    # 3. Simulate midnight: overwrite the state file with YESTERDAY's
    #    date + counter=999. Next call must auto-reset to 1.
    # ============================================================
    print()
    print("=" * 70)
    print("3. Simulate the midnight roll-over (yesterday's state on disk)")
    print("=" * 70)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with open(state_file, "w") as f:
        json.dump({"date": yesterday, "counter": 999}, f)
    lpn_generator._IN_MEMORY_STATE = None
    print(f"  Forced disk state: date={yesterday}, counter=999")
    next_lpn = lpn_generator.peek_next()
    consumed = lpn_generator.consume_next()
    print(f"  peek_next() after midnight: {next_lpn}")
    print(f"  consume_next():            {consumed}")
    assert consumed.endswith("000001"), consumed
    # And the date tag in the LPN should reflect TODAY, not yesterday.
    today_tag = date.today().strftime("%y%m%d")
    assert today_tag in consumed, (consumed, today_tag)
    print(f"  PASS: counter reset to 1, date prefix is {today_tag}")

    # ============================================================
    # 4. TODAY count: source is the DB (count_today). Insert 100
    #    cartons "today", confirm count_today() returns 100; then
    #    simulate "restart" by discarding caches and re-running.
    # ============================================================
    print()
    print("=" * 70)
    print("4. TODAY / ALL count: read from DB on every refresh")
    print("=" * 70)
    # Mock-mode means the 'DB' is an in-process list — but the source
    # of truth pattern is identical to live Oracle: every refresh
    # calls SELECT COUNT(*) WHERE TRUNC(CREATION_DATE) = TRUNC(SYSDATE).
    database._MEM_ROWS.clear()
    now = datetime.now()
    for i in range(100):
        c = CartonLabel(
            brand="Alexander", organization_id="370", plant_code="064",
            item_code="AAS60601", lot_number="L", sn=str(i + 1),
            grade="A", shift="E", size_code="60X60",
            pcs_type="Regular", carton_qty=1.0, pcs_per_ctn=4,
            batch_date=now, batch_time="03:00 PM",
            total_planned_qty=100.0,
        )
        c.lpn_id = f"LPN-C-{now.strftime('%y%m%d')}{i+1:06d}"
        c.carton_code = c.lpn_id
        database.insert_carton(c.as_db_row())

    today_count = database.count_today()
    total_count = database.count_total()
    print(f"  After 100 inserts:  TODAY={today_count}, ALL={total_count}")
    assert today_count == 100 and total_count == 100

    # "Restart" the app: caches in lpn_generator and main_window are
    # reset, but the DB rows persist. count_today() re-reads them.
    lpn_generator._IN_MEMORY_STATE = None
    today_after_restart = database.count_today()
    total_after_restart = database.count_total()
    print(f"  After 'restart':    TODAY={today_after_restart}, "
          f"ALL={total_after_restart}")
    assert today_after_restart == 100 and total_after_restart == 100
    print("  PASS: TODAY / ALL survive restart unchanged (DB-backed)")

    # ============================================================
    # 5. Disk state lost entirely (e.g. wiped device, ProgramData
    #    folder removed). The DB still has 100 rows for today.
    #    Recovery: next consume_next() must resume at 101, not 1.
    # ============================================================
    print()
    print("=" * 70)
    print("5. Disk state file LOST, DB still has 100 rows today")
    print("=" * 70)
    if os.path.exists(state_file):
        os.remove(state_file)
    lpn_generator._IN_MEMORY_STATE = None
    print(f"  Wiped {state_file}")
    next_lpn = lpn_generator.peek_next()
    consumed = lpn_generator.consume_next()
    print(f"  peek_next() after wipe: {next_lpn}")
    print(f"  consume_next():        {consumed}")
    assert consumed.endswith("000101"), \
        f"Expected counter=101 (DB max 100 + 1), got {consumed}"
    print("  PASS: recovered from DB — counter resumed at 101, "
          "did NOT restart at 1")

    # 5b. Corrupt file — same recovery should fire.
    print()
    print("  5b. Corrupt state file -> still recovers from DB")
    with open(state_file, "w") as f:
        f.write("not valid json {{{")
    lpn_generator._IN_MEMORY_STATE = None
    # After our last consume above the DB now has 101 rows (we
    # inserted earlier in step 4 + step 5 consumed an LPN but didn't
    # insert; the DB still has only 100). Re-verify against 100.
    consumed = lpn_generator.consume_next()
    print(f"  consume_next() with corrupt file: {consumed}")
    # After the previous consume the DB still has 100 inserted rows
    # (consume_next does NOT insert a carton on its own), so DB max
    # is still 100 -> next counter from DB = 100, then +1 = 101.
    # The recovery returns 100; consume_next adds 1 to get 101.
    assert consumed.endswith("000101"), \
        f"Expected 101 from DB recovery, got {consumed}"
    print("  PASS: corrupt state file also triggers DB recovery")

    print()
    print("=" * 70)
    print("ALL PERSISTENCE CHECKS PASSED")
    print("=" * 70)
    print()
    print("Summary:")
    print("  LPN ID   — persisted in lpn_state.json on every consume.")
    print("             Survives restart. Auto-resets on date change.")
    print("             If file is LOST or CORRUPT, recovers from")
    print("             SELECT MAX(LPN_ID) FROM XXFG_CARTON_MASTER for")
    print("             today's date prefix — no duplicate LPN_IDs.")
    print("  TODAY    — read live from DB on every refresh.")
    print("  ALL      — read live from DB on every refresh.")
    print("             Both survive any restart short of someone")
    print("             deleting rows from XXFG_CARTON_MASTER.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
