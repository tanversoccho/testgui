"""Smoke-test the GUI data path through the server API only.

This starts a real local FastAPI/Uvicorn server in mock-DB mode, points
the GUI/core configuration at that HTTP API, and exercises the same
database facade the UI uses. It should not open Oracle directly or write
to live data.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DIKAI_USE_MOCK_DB"] = "true"
os.environ.setdefault("DIKAI_DEVICES_RAW", "stm32-test:test")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str) -> None:
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError("server did not start in time")


def main() -> int:
    import uvicorn
    from PySide6.QtWidgets import QApplication

    from server.app.main import app as server_app

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(server_app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    qt_app = QApplication([])
    mw = None
    old_count_state = None
    try:
        _wait_for_health(base_url)

        from config import config_app
        from core import api_client, database, lpn_generator, printer_counts
        from core.carton_model import CartonLabel
        from core.printer_link import PrinterStatus
        from ui.main_window import MainWindow

        config_app.USE_SERVER_API = True
        config_app.USE_MOCK_DB = False
        config_app.SERVER_API_BASE_URL = f"{base_url}/api/v1"
        config_app.SERVER_DEVICE_ID = "stm32-test"
        config_app.SERVER_DEVICE_SECRET = "test"
        api_client.reset()
        database.reset_connections()
        old_count_state = printer_counts._read_state()
        try:
            os.remove(printer_counts._state_path())
        except OSError:
            pass

        ok, msg = database.test_connection()
        assert ok, msg
        print(f"GUI facade connected through API: {msg}")

        brands = database.fetch_brands()
        brand_row = next((b for b in brands if b["brand"] == "Monalisa"), None)
        assert brand_row, brands
        items = database.fetch_item_codes("Monalisa")
        assert items, "API item lookup returned no rows"
        item = items[0]
        item_code = item["code"]
        sample = database.fetch_sample_config(item.get("size") or "60X60")
        assert sample and sample["normal_pcs"] > 0, sample
        pcs_per_ctn = database.fetch_normal_pcs_per_ctn(brand_row["inv_code"], item_code)
        assert pcs_per_ctn, "API UOM lookup returned no pcs/ctn"
        print("Brands/items/sample/UOM resolved through API")

        peek = lpn_generator.peek_next()
        lpn = lpn_generator.consume_next()
        assert lpn, (peek, lpn)
        print(f"LPN allocated through API: {lpn}")

        carton = CartonLabel(
            carton_code=lpn,
            lpn_id=lpn,
            organization_id=str(brand_row["inv_code"]),
            plant_code=str(brand_row["org_code"]),
            brand="Monalisa",
            item_code=item_code,
            item_desc=item.get("desc", ""),
            lot_number="LOT-API",
            sn="1",
            grade="A",
            shift="M",
            size_code=item.get("size") or "60X60",
            pcs_type="Regular",
            carton_qty=1.0,
            pcs_per_ctn=int(pcs_per_ctn),
            batch_date=datetime(2026, 6, 23, 8, 0, 0),
            batch_time="08:00 AM",
            total_planned_qty=100,
            created_by="smoke-gui-api",
        )
        row = carton.as_db_row()
        inserted, err = database.insert_carton(row)
        assert inserted, err
        history = database.query_history(brand="Monalisa")
        assert any(r.carton_code == lpn for r in history), "inserted carton not returned by API history"
        assert isinstance(database.count_total(), int)
        marked, err = database.mark_reprint(lpn, by_user="smoke-gui-api")
        assert marked, err
        deleted, err = database.soft_delete(lpn, by_user="smoke-gui-api")
        assert deleted, err
        print("Carton insert/history/count/reprint/delete used API facade")

        mw = MainWindow()
        mw._counts_timer.stop()
        mw._db_check_timer.stop()
        mw._refresh_counts(PrinterStatus(connected=False, counter=428))
        assert int(mw.dash_today_value.text()) == 0
        assert int(mw.dash_total_value.text()) == 0
        mw._refresh_counts(PrinterStatus(connected=True, counter=428))
        assert int(mw.dash_today_value.text()) == 0
        assert int(mw.dash_total_value.text()) == 428
        print("MainWindow constructed in API mode and printer counters refresh correctly")

    finally:
        try:
            from core import printer_counts

            if old_count_state is not None:
                printer_counts._write_state(old_count_state)
            else:
                try:
                    os.remove(printer_counts._state_path())
                except OSError:
                    pass
        except Exception:
            pass
        if mw is not None:
            try:
                if mw._db_thread is not None and mw._db_thread.isRunning():
                    mw._db_thread.wait(5000)
            except RuntimeError:
                pass
            mw.printer.stop_polling()
            mw.printer.disconnect()
            mw.close()
        qt_app.processEvents()
        server.should_exit = True
        thread.join(timeout=5)

    print("GUI API INTEGRATION SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
