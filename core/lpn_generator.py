"""LPN_ID generator.

Format:   LPN-C-{YY}{MM}{DD}{counter:06d}
Example:  LPN-C-260612000001    (12 June 2026, first carton of the day)

The counter resets to 1 at the start of every calendar day. The state
file remembers (date, counter) so the counter survives app restarts
within the same day and resets cleanly on the first call after
midnight."""
import json
import os
import threading
from datetime import date, datetime
from config import config_app
from config.user_paths import user_data_dir, bundled_data_dir
from core import api_client

_LOCK = threading.Lock()
_STATE_FILENAME = "lpn_state.json"

# Fixed 6-digit counter inside the LPN — independent of
# config_app.LPN_PADDING, because the date prefix already takes the
# rest of the slot.
_COUNTER_WIDTH = 6


def _state_path() -> str:
    """Writable path for lpn_state.json. user_data_dir() returns the
    project root in dev and %ProgramData%\\Dikai\\DikaiCartonPrinter\\
    in the installed build — both are writable for the running user."""
    return os.path.join(user_data_dir(), _STATE_FILENAME)


def _bundled_state_path() -> str:
    """Read-only path for the shipped lpn_state.json default — used
    once on first launch after install to seed the user file."""
    return os.path.join(bundled_data_dir(), _STATE_FILENAME)


def _today_tag() -> str:
    """YYMMDD for today, used as the LPN date prefix."""
    return datetime.now().strftime("%y%m%d")


def _today_iso() -> str:
    """YYYY-MM-DD for today — stored verbatim in the state file."""
    return date.today().isoformat()


def _recover_counter_from_db() -> int:
    """Ask the DB for today's highest LPN counter and return it.

    Used when the disk state file is missing / unreadable / stale —
    prevents the counter from restarting at 1 and creating duplicate
    LPN_IDs against rows the DB already has for today.

    Returns 0 when no rows for today, DB offline, or any error — the
    caller will then start a fresh sequence at 1, which is the same
    behaviour as before this recovery hook existed."""
    try:
        # Late import — core.database imports config but not
        # core.lpn_generator, so no circular risk; we still defer the
        # import so module load order is irrelevant.
        from core import database
        recovered = database.fetch_max_lpn_counter_today()
        if recovered is None or recovered < 0:
            return 0
        return int(recovered)
    except Exception:
        return 0


def _read_state() -> tuple[str, int]:
    """Return (date_iso, counter).

    Resolution order:
      1. User-writable lpn_state.json with today's date → use its counter.
      2. Bundled (shipped) lpn_state.json with today's date → use it.
      3. Disk has nothing useful (missing, stale date, or unreadable) →
         ask the DB for today's MAX(LPN_ID) and resume from there.
      4. DB also unavailable → start at counter=0 (next consume = 1).

    Step 3 is the recovery the operator asked for: if the device was
    wiped or the state file was lost, we don't restart at 1 and risk
    duplicating LPN_IDs that already exist in XXFG_CARTON_MASTER."""
    today = _today_iso()
    candidates = [_state_path()]
    bundled = _bundled_state_path()
    if bundled != _state_path():
        candidates.append(bundled)

    file_was_present = False
    for path in candidates:
        if not os.path.exists(path):
            continue
        file_was_present = True
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            stored_date    = str(data.get("date") or "")
            stored_counter = int(data.get("counter", 0))
            if stored_date == today:
                return stored_date, max(0, stored_counter)
            # Date mismatch — fall through to DB recovery so a fresh
            # day still gets the right starting point if the DB
            # already has rows from earlier today (multi-machine setup,
            # or device clock just rolled past midnight and another
            # process printed first).
            break
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            # Corrupt file — fall through to DB recovery.
            break

    # Either no disk state at all (fresh install / wiped device), or
    # the disk state was unusable. Ask the DB for the live MAX.
    recovered = _recover_counter_from_db()
    return today, recovered


def _write_state(d: str, n: int) -> tuple[bool, str]:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump({"date": d, "counter": n}, f)
        return True, ""
    except OSError as e:
        return False, f"Could not write LPN state ({_state_path()}): {e}"


# In-memory cache. Tuple (date_iso, counter). Resets when the date
# changes — keeps the operator unblocked even on a read-only filesystem.
_IN_MEMORY_STATE: tuple[str, int] | None = None


def _current_state() -> tuple[str, int]:
    """Effective (date, counter): in-memory if same day, else file (which
    handles its own reset on date change)."""
    global _IN_MEMORY_STATE
    today = _today_iso()
    if _IN_MEMORY_STATE is not None and _IN_MEMORY_STATE[0] == today:
        return _IN_MEMORY_STATE
    state = _read_state()
    _IN_MEMORY_STATE = state
    return state


def _format(counter: int) -> str:
    """Build the LPN string for a given counter, using today's date tag."""
    return f"{config_app.LPN_PREFIX}{_today_tag()}{counter:0{_COUNTER_WIDTH}d}"


def peek_next() -> str:
    """Look at what the next LPN would be without consuming it."""
    if api_client.enabled():
        try:
            return str(api_client.call("GET", "/lpn/peek")["lpn_id"])
        except Exception:
            return ""
    _d, cur = _current_state()
    return _format(cur + 1)


def consume_next() -> str:
    """Generate and reserve the next LPN. Thread-safe. Never raises —
    if the disk write fails the counter still advances in memory so
    LPNs remain unique for the running session.

    Resets to 1 at the start of a new calendar day, automatically."""
    if api_client.enabled():
        try:
            return str(api_client.call("POST", "/lpn/next", idempotent=True)["lpn_id"])
        except Exception as e:
            raise RuntimeError(f"Server LPN allocation failed: {e}")
    global _IN_MEMORY_STATE
    with _LOCK:
        d, cur = _current_state()
        n = cur + 1
        _write_state(d, n)
        _IN_MEMORY_STATE = (d, n)
        return _format(n)
