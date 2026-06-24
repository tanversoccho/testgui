"""Load/save user-editable settings to a JSON file.

In dev the file sits at the project root next to main.py. In the
installed build the file lives under %ProgramData%\\Dikai\\
DikaiCartonPrinter\\ — a user-writable folder pre-created by the
installer. See config/user_paths.py for the full rationale."""
import json
import os
from . import config_app
from .user_paths import user_data_dir, bundled_data_dir

CONFIG_FILENAME = "dikai_config.json"


def _user_config_path() -> str:
    """Where we read and write the operator's current settings."""
    return os.path.join(user_data_dir(), CONFIG_FILENAME)


def _bundled_config_path() -> str:
    """Where the shipped factory defaults live (read-only). Used as a
    fallback when no user file exists yet."""
    return os.path.join(bundled_data_dir(), CONFIG_FILENAME)


# Keys we expose for editing
EDITABLE_KEYS = (
    "PRINTER_IP", "PRINTER_PORT", "PRINTER_PROTO", "PRINTER_TIMEOUT",
    "PRINTER_PKGID", "PRINTER_AUTO_INCREMENT_PKGID",
    "DB_HOST", "DB_PORT", "DB_SERVICE", "DB_USER", "DB_PASSWORD",
    "USE_MOCK_DB",
    "USE_SERVER_API", "SERVER_API_BASE_URL", "SERVER_DEVICE_ID",
    "SERVER_DEVICE_SECRET", "SERVER_API_TIMEOUT",
    "USE_THICK_MODE", "ORACLE_INSTANT_CLIENT_PATH", "TNS_ADMIN_PATH",
    "DB_USE_TNS", "DB_TNS_ALIAS",
    "PRINT_ENABLE", "MODULATION_SET", "VISCOSITY_SET",
    "INK_PRESSURE", "NOZZLE_TEMP", "CHARGE_VALUE",
    # Print sizing — QR + 'P' Modify Message Params
    "QR_MAX_DOTS", "QR_BORDER", "QR_ERROR_CORRECTION",
    "MSG_REVERSE", "MSG_INVERT", "MSG_WIDTH", "MSG_DELAY",
    "MSG_HEIGHT", "MSG_PRINTED_DOTS", "MSG_TRIG_TIMES",
    "MSG_GAP", "MSG_COL_REPEATS", "MSG_CHAR_SPACE",
)


def _apply(data: dict) -> None:
    """Patch the values from `data` onto the config_app module."""
    for k in EDITABLE_KEYS:
        if k in data:
            setattr(config_app, k, data[k])


def load() -> None:
    """Read JSON config and patch config_app values in place.

    Tries the user-writable path first; falls back to the bundled
    factory defaults if the user file doesn't exist yet (first launch
    after install). Silent on any error — defaults stay in place."""
    # User-edited file first
    user_path = _user_config_path()
    candidates = [user_path]
    bundled = _bundled_config_path()
    if bundled != user_path:
        candidates.append(bundled)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        _apply(data)
        return


def save() -> tuple[bool, str]:
    """Write current config_app values back to the user-writable JSON.

    Returns (ok, error_message). error_message is '' on success. The UI
    can use this to show the operator if the disk write failed
    (read-only volume, permissions, full disk, etc.) instead of
    silently losing settings."""
    path = _user_config_path()
    try:
        # user_data_dir() ensures the parent folder exists, but a fresh
        # install on a locked-down PC might still need an extra mkdir.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = {k: getattr(config_app, k) for k in EDITABLE_KEYS}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        return True, ""
    except OSError as e:
        return False, f"Could not write {path}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
