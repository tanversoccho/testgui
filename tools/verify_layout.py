"""Headless layout sanity check.

Boots MainWindow at 1024x600, lets the event loop spin a few times so
every signal/slot has a chance to finalise widget geometries, then
saves a PNG screenshot. Optionally also dumps the history dialog and
settings dialog. Logic is untouched — this is a viewer harness only.

Usage:
    python tools/verify_layout.py <screenshot_dir>
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

# Make sure project root is on sys.path so `from ui...` works when run
# from anywhere.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import config_loader, config_app  # noqa: E402
# Force mock-mode so the History dialog gets populated rows (the real
# Oracle DB is only reachable from the operator's PC over VPN).
config_app.USE_MOCK_DB = True
from ui.main_window import MainWindow  # noqa: E402
from ui.theme import stylesheet  # noqa: E402
from ui.history_dialog import HistoryDialog  # noqa: E402
from ui.settings_dialog import SettingsDialog  # noqa: E402
from core import database  # noqa: E402
from core.carton_model import CartonLabel  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402


def _seed_mock_history(n: int = 20):
    """Inject `n` mock carton rows into the in-memory store so the
    history table has something to show."""
    base = datetime(2026, 6, 15, 8, 0)
    for i in range(n):
        c = CartonLabel(
            lpn_id=f"LPN-C-26061500{i:04d}",
            carton_code=f"LPN-C-26061500{i:04d}",
            brand="Monalisa",
            organization_id="481",
            item_code=f"BRAND-PR-{i:03d}",
            item_desc="Test item",
            lot_number=f"L-{i:02d}",
            grade="A",
            shift="M",
            size_code="500g",
            uom_code="CTN",
            pcs_type="Regular",
            carton_qty=1.0,
            pcs_per_ctn=24,
            batch_date=base + timedelta(hours=i),
            batch_time=(base + timedelta(hours=i)).strftime("%I:%M %p"),
            batch_status="N",
        )
        c.batch_no = c.build_batch_no()
        database.insert_carton(c.as_db_row())


def _shot(widget, path: Path):
    pm = widget.grab()
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.save(str(path), "PNG")
    print(f"   saved {path}  ({pm.width()}x{pm.height()})")


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "tools/_layout_shots")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    config_loader.load()
    # Re-assert mock mode after load() — saved settings may have flipped it.
    config_app.USE_MOCK_DB = True
    _seed_mock_history(n=20)

    # MainWindow tries to fit the screen's available geometry; on this
    # dev machine that's huge, so we force the Daewoo-like usable area
    # (1024x600 panel minus Windows title bar + taskbar ≈ 1024x530).
    win = MainWindow()
    win.resize(1024, 530)
    win.show()

    # Spin the loop enough for the first paint + the deferred form _emit.
    for _ in range(40):
        app.processEvents()
        time.sleep(0.01)

    _shot(win, out_dir / "main_window_1024x530.png")
    print(f"   main window size: {win.width()}x{win.height()}")
    print(f"   main minimum:     {win.minimumWidth()}x{win.minimumHeight()}")

    # History dialog — show non-modally so we can grab and tear down.
    try:
        hist = HistoryDialog(win, db_online=True)
        # Match the Daewoo usable area instead of the dialog's
        # opportunistic 1010×580 target.
        hist.resize(1010, 510)
        hist.show()
        for _ in range(40):
            app.processEvents()
            time.sleep(0.01)
        rows_visible = (hist.table.viewport().height() //
                        max(1, hist.table.verticalHeader().defaultSectionSize()))
        print(f"   history dialog size: {hist.width()}x{hist.height()}")
        print(f"   table viewport: {hist.table.viewport().width()}x{hist.table.viewport().height()}")
        print(f"   row height: {hist.table.verticalHeader().defaultSectionSize()}")
        print(f"   estimated rows visible: {rows_visible}")
        _shot(hist, out_dir / "history_dialog_1010x510.png")
        hist.close()
    except Exception as e:
        print(f"   history dialog failed: {e}")

    # Settings — capture too.
    try:
        sd = SettingsDialog(win.printer, win)
        sd.resize(900, 560)
        sd.show()
        for _ in range(30):
            app.processEvents()
            time.sleep(0.01)
        print(f"   settings dialog size: {sd.width()}x{sd.height()}")
        _shot(sd, out_dir / "settings_dialog_900x560.png")
        sd.close()
    except Exception as e:
        print(f"   settings dialog failed: {e}")

    print("done.")
    win.close()


if __name__ == "__main__":
    main()
