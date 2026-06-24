"""Background DB workers so the UI never blocks on Oracle calls.

Two QThread subclasses:
  - `DBCheckThread`   — runs init_oracle_client (if thick mode) + test_connection
  - `ItemFetchThread` — runs fetch_item_codes for the picked brand

Both emit a Qt signal with the result when done, so receivers can update
the UI on the GUI thread without worrying about thread safety.
"""
from __future__ import annotations
from typing import List, Dict
from PySide6.QtCore import QThread, Signal

from config import config_app
from core import database


class DBCheckThread(QThread):
    """Initialise Instant Client (if needed) and run test_connection."""
    result = Signal(bool, str)    # (ok, message)

    def run(self) -> None:
        try:
            if config_app.USE_THICK_MODE:
                ok, msg = database._ensure_thick_initialized()
                if not ok:
                    self.result.emit(False, msg)
                    return
            ok, msg = database.test_connection()
            self.result.emit(ok, msg)
        except Exception as e:  # belt + suspenders — never crash the thread
            self.result.emit(False, f"{type(e).__name__}: {e}")


class ItemFetchThread(QThread):
    """Background fetch_item_codes for one brand. Emits (brand, items)."""
    result = Signal(str, list)    # (brand, [{code, size, desc}, ...])

    def __init__(self, brand: str, parent=None):
        super().__init__(parent)
        self._brand = brand

    def run(self) -> None:
        try:
            items = database.fetch_item_codes(self._brand)
        except Exception:
            items = []
        self.result.emit(self._brand, items)


