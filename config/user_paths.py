"""Writable / read-only path resolution for shipped vs. dev runs.

Why this exists
---------------
In development the project lives in a writable folder, so the JSON
config and LPN state file can sit next to `main.py`. But the production
installer puts the bundled .exe under `C:\\Program Files\\Dikai\\…`,
which is **read-only** for the operator's normal Windows account.
Writing the config there raises ``[Errno 13] Permission denied``.

The Inno Setup installer pre-creates a sibling folder under
``%ProgramData%`` with user-modify permissions, and seeds it with the
shipped defaults:

    C:\\ProgramData\\Dikai\\DikaiCartonPrinter\\
        dikai_config.json     (operator-editable)
        default_config.json   (read-only reference)
        lpn_state.json
        logs\\

This module gives the rest of the codebase a single function to find
that folder — and a development-mode fallback to the project root so
running ``python main.py`` from source keeps working exactly as before.

Public API
----------
* ``user_data_dir()``     - the writable folder for config + state files
* ``bundled_data_dir()``  - read-only folder holding shipped defaults
                            (PyInstaller's MEIPASS unpack dir at runtime,
                            project root in development)
* ``is_frozen()``         - True when running as a PyInstaller bundle
"""
from __future__ import annotations

import os
import sys

# ProgramData layout used by the installer ([Files] section in
# desktop_app/installer/DikaiCartonPrinter.iss). Keep these strings in
# sync with the .iss values MyDataPublisher / MyDataDirName.
_PUBLISHER = "Dikai"
_APP_DIR   = "DikaiCartonPrinter"


def is_frozen() -> bool:
    """True when running inside a PyInstaller-built bundle."""
    return bool(getattr(sys, "frozen", False))


def _project_root_dev() -> str:
    """Folder of the original source tree (the parent of `config/`)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir() -> str:
    """Return the WRITABLE folder where dikai_config.json /
    lpn_state.json live.

    Frozen     -> %ProgramData%\\Dikai\\DikaiCartonPrinter
    Dev / src  -> <project root>

    Ensures the folder exists in either mode (creates it on first call
    if missing — useful when the .exe was launched without running
    the installer first).
    """
    if is_frozen():
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        path = os.path.join(base, _PUBLISHER, _APP_DIR)
    else:
        path = _project_root_dev()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def bundled_data_dir() -> str:
    """Return the READ-ONLY folder containing the shipped defaults.

    Frozen    -> the PyInstaller MEIPASS unpack dir (sibling files
                 added via --add-data are at the root of this dir)
    Dev / src -> <project root>

    Used when the user's copy of dikai_config.json doesn't exist yet
    (first launch, fresh install, or a wiped ProgramData) so we can
    seed from the bundled defaults without falling back to None.
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", _project_root_dev())
    return _project_root_dev()
