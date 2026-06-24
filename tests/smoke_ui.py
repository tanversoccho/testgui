"""Headless smoke test — verifies the UI changes without needing a display.

Run via:    PYTHONPATH=. desktop_app/.venv/Scripts/python.exe tests/smoke_ui.py

Covers:
  * FormPanel constructs, big fields land, auto-caps fires
  * collect() returns CartonLabel with plant_code + sn populated
  * MainWindow constructs, preview hidden top-level, dashboard tiles wired
  * Dashboard status strip drives jet state / INK / SOL / faults
  * _refresh_counts uses the printer main counter with a daily baseline
  * _apply_connection_gate toggles dashboard jet tiles
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication([])

    from config import config_app
    config_app.USE_MOCK_DB = True   # don't touch Oracle
    config_app.USE_SERVER_API = False

    # ===== FormPanel =====
    from ui.form_panel import FormPanel
    form = FormPanel()
    print("FormPanel constructed")

    expected_h = form.lot_edit.maximumHeight()
    assert 26 <= expected_h <= 40, f"unexpected field height {expected_h}"
    assert form.sn_edit.maximumHeight() == expected_h
    assert hasattr(form, "total_planned_qty_edit"), "TOTAL_PLANNED_QTY field missing"
    assert not hasattr(form, "batch_status_cb"), "batch_status_cb should be removed"
    print(f"Field heights {expected_h} px + TOTAL_PLANNED_QTY replaces batch_status")

    # Auto-uppercase via the textEdited slot
    form.lot_edit.setText("l-v-test")
    form.lot_edit.textEdited.emit("l-v-test")
    assert form.lot_edit.text() == "L-V-TEST", form.lot_edit.text()
    print(f"Auto-caps lot_edit:  'l-v-test' -> {form.lot_edit.text()!r}")

    # SN is now numeric — auto-caps wiring removed, QIntValidator added.
    from PySide6.QtGui import QIntValidator
    assert isinstance(form.sn_edit.validator(), QIntValidator), \
        f"sn_edit needs an integer validator, got {form.sn_edit.validator()!r}"
    form.sn_edit.setText("42")
    assert form.sn_edit.text() == "42"
    print(f"SN integer validator: setText('42') -> {form.sn_edit.text()!r}")

    form.item_cb.lineEdit().setText("aas60601")
    form.item_cb.lineEdit().textEdited.emit("aas60601")
    assert form.item_cb.lineEdit().text() == "AAS60601", form.item_cb.lineEdit().text()
    print(f"Auto-caps item_cb:   'aas60601' -> {form.item_cb.lineEdit().text()!r}")

    # collect() returns CartonLabel with plant_code + numeric sn + total_planned_qty
    form.brand_cb.setCurrentIndex(2)   # 'Alexander' → org_code=064, inv_code=370
    form.total_planned_qty_edit.setText("1500")
    c = form.collect()
    print(
        f"collect(): brand={c.brand!r} org_id={c.organization_id!r} "
        f"plant_code={c.plant_code!r} sn={c.sn!r} lot={c.lot_number!r} "
        f"total_planned_qty={c.total_planned_qty!r}"
    )
    assert c.brand == "Alexander"
    assert c.plant_code == "064", c.plant_code
    assert c.organization_id == "370", c.organization_id
    assert c.sn == "42"
    assert c.lot_number == "L-V-TEST"
    assert c.total_planned_qty == 1500.0, c.total_planned_qty
    assert not hasattr(c, "batch_status"), "CartonLabel.batch_status should be gone"
    # as_db_row carries the new TOTAL_PLANNED_QTY column
    row = c.as_db_row()
    assert row.get("TOTAL_PLANNED_QTY") == 1500.0, row.get("TOTAL_PLANNED_QTY")
    assert "BATCH_STATUS" not in row
    print(f"as_db_row['TOTAL_PLANNED_QTY'] = {row['TOTAL_PLANNED_QTY']!r}")

    # PCS/CTN caption stays "PCS/CTN" regardless of pcs_type.
    form.pcs_type_cb.setCurrentIndex(0)   # Regular
    form._apply_pcs_type_view()
    assert form._field_caption[form.pcs_per_ctn_edit] == "PCS/CTN"
    form.pcs_type_cb.setCurrentIndex(1)   # Sample
    form._apply_pcs_type_view()
    assert form._field_caption[form.pcs_per_ctn_edit] == "PCS/CTN"
    print("PCS/CTN caption stays the same across Regular / Sample")

    # ===== UOM-based PCS/CTN for Regular, auto-CTN for Sample =====
    # Simulate the result of fetch_normal_pcs_per_ctn(): 25 pcs per ctn.
    form._uom_normal_pcs = 25
    form._sample_cfg = {"normal_pcs": 25, "sample_pcs": 10, "conversion": 0.40}

    # Regular: PCS/CTN comes from the UOM view (25), CTN = 1.00, read-only.
    form.pcs_type_cb.setCurrentIndex(0)
    form._apply_pcs_type_view()
    assert form.pcs_per_ctn_edit.text() == "25", form.pcs_per_ctn_edit.text()
    assert form.ctn_edit.text() == "1.00"
    assert form.pcs_per_ctn_edit.isReadOnly()
    assert form.ctn_edit.isReadOnly()
    print(f"Regular: PCS/CTN={form.pcs_per_ctn_edit.text()} (from UOM), "
          f"CTN={form.ctn_edit.text()} (both read-only)")

    # Sample: PCS/CTN defaults to sample_pcs=10 and is EDITABLE;
    # CTN = 10 / 25 = 0.40, derived live.
    form.pcs_type_cb.setCurrentIndex(1)
    form._apply_pcs_type_view()
    assert form.pcs_per_ctn_edit.text() == "10", form.pcs_per_ctn_edit.text()
    assert form.ctn_edit.text() == "0.40", form.ctn_edit.text()
    assert not form.pcs_per_ctn_edit.isReadOnly()   # editable
    assert form.ctn_edit.isReadOnly()                # derived, read-only
    print(f"Sample default: PCS/CTN={form.pcs_per_ctn_edit.text()} "
          f"(editable), CTN={form.ctn_edit.text()} (derived 10/25)")

    # Sample mode: operator overrides PCS/CTN to 15 -> CTN auto-recomputes
    # to 15/25 = 0.60.
    form.pcs_per_ctn_edit.setText("15")
    form.pcs_per_ctn_edit.textEdited.emit("15")
    assert form.ctn_edit.text() == "0.60", form.ctn_edit.text()
    print(f"Sample override: PCS/CTN=15 -> CTN auto-updated to "
          f"{form.ctn_edit.text()}")

    # Sample mode: operator types 5 -> CTN = 5/25 = 0.20.
    form.pcs_per_ctn_edit.setText("5")
    form.pcs_per_ctn_edit.textEdited.emit("5")
    assert form.ctn_edit.text() == "0.20", form.ctn_edit.text()
    print(f"Sample override: PCS/CTN=5  -> CTN auto-updated to "
          f"{form.ctn_edit.text()}")

    # Sample mode without UOM (DB offline): CTN falls back to
    # sample_cfg.conversion at first paint.
    form._uom_normal_pcs = None
    form._apply_pcs_type_view()
    assert form.ctn_edit.text() == "0.40"
    print(f"Sample fallback (no UOM): CTN = sample_cfg.conversion = "
          f"{form.ctn_edit.text()}")

    qr = c.build_qr_payload()
    assert qr.startswith("AAS60601|L-V-TEST|") and qr.endswith("|42"), qr
    print(f"QR payload: {qr}")

    # ===== MainWindow =====
    from ui.main_window import MainWindow
    mw = MainWindow()
    print("MainWindow constructed")

    # Preview window: hidden top-level, parented to mw
    assert mw.preview.isHidden(), "Preview must start hidden"
    assert mw.preview.parent() is mw
    # Preview no longer exposes the old status / counter methods.
    for gone in ("update_status", "set_counts", "set_operational",
                 "jet_start_clicked", "jet_stop_clicked"):
        assert not hasattr(mw.preview, gone), \
            f"Preview should no longer expose {gone}"
    print("Preview is label-only (no status/buttons/counters)")

    # Dashboard tiles + status strip widgets are wired on MainWindow
    for attr in ("jet_start_btn", "jet_stop_btn", "preview_btn",
                 "info_btn", "view_btn", "settings_btn",
                 "dash_jet_state_lbl", "dash_ink_chip", "dash_sol_chip",
                 "dash_faults_host", "dash_today_value", "dash_total_value"):
        assert hasattr(mw, attr), f"MainWindow missing {attr}"
    print("Dashboard exposes: jet tiles, preview tile, jet state pill, "
          "INK/SOL chips, fault chips host, TODAY/ALL counters")

    # DB + Printer top-bar indicators are now click-to-toggle QPushButtons.
    from PySide6.QtWidgets import QPushButton
    assert isinstance(mw.db_status_pill, QPushButton), type(mw.db_status_pill)
    assert isinstance(mw.conn_chip,      QPushButton), type(mw.conn_chip)
    # `clicked` signal is connected; use test doubles so the smoke run
    # does not open network sockets or leave a worker thread alive.
    db_clicks = []
    printer_clicks = []
    real_start_db_check = mw._start_db_check
    real_printer_connect = mw.printer.connect
    try:
        mw._start_db_check = lambda force=False: db_clicks.append(force)
        mw.printer.connect = lambda: printer_clicks.append(True) or True
        mw.db_status_pill.click()
        mw.conn_chip.click()
    finally:
        mw._start_db_check = real_start_db_check
        mw.printer.connect = real_printer_connect
    assert db_clicks, "DB pill click did not reach _start_db_check"
    assert printer_clicks, "Printer pill click did not reach printer.connect"
    print("Top-bar DB + Printer pills are QPushButtons and clickable")

    # ===== Daewoo 1024×600 budget check =====
    # The form is the elastic part of the dashboard. After top bar,
    # status strip, action bar and bottom status bar take their fixed
    # heights, the form must still want less vertical space than the
    # remaining slot — otherwise Qt squeezes rows and labels visibly
    # collide (the bug the operator hit on the LattePanda).
    mw.resize(1024, 530)   # ~usable area on the Daewoo
    mw.show()
    app.processEvents()
    from PySide6.QtWidgets import QFrame
    action_bar = mw.findChild(QFrame, "actionBar")
    strip      = mw.findChild(QFrame, "dashStatus")
    statusbar  = mw.findChild(QFrame, "statusbar")
    topbar     = mw.findChild(QFrame, "topbar")
    chrome_h = (
        topbar.sizeHint().height()
        + strip.sizeHint().height()
        + action_bar.sizeHint().height()
        + statusbar.sizeHint().height()
    )
    slot_h = 530 - chrome_h
    form_h = mw.form.sizeHint().height()
    print(f"Chrome: top={topbar.sizeHint().height()} strip={strip.sizeHint().height()} "
          f"action={action_bar.sizeHint().height()} status={statusbar.sizeHint().height()}")
    print(f"Form sizeHint: {form_h} px,  available slot: {slot_h} px")
    assert form_h <= slot_h, (
        f"Form needs {form_h} px but slot is {slot_h} px — Qt will squeeze rows"
    )
    print("Form fits inside the 1024×600 dashboard slot")
    mw.hide()

    # ===== Dashboard status strip behaviour =====
    from core.printer_link import PrinterStatus, JetState

    # Disconnected → "Printer Offline", chips hidden
    mw._update_dash_status(PrinterStatus(connected=False))
    assert "Offline" in mw.dash_jet_state_lbl.text(), mw.dash_jet_state_lbl.text()
    assert mw.dash_ink_chip.isHidden()
    assert mw.dash_sol_chip.isHidden()
    assert mw.dash_faults_host.isHidden()
    print(f"Offline strip: jet_state={mw.dash_jet_state_lbl.text()!r}, chips hidden")

    # Connected + jet READY + print enabled → "Printing"
    st_ready = PrinterStatus(
        connected=True, jet=JetState.READY, print_enabled=True,
        ink_ok=True, sol_ok=True, faults=[],
    )
    mw._update_dash_status(st_ready)
    assert "Printing" in mw.dash_jet_state_lbl.text(), mw.dash_jet_state_lbl.text()
    assert mw.dash_ink_chip.isHidden()
    assert mw.dash_sol_chip.isHidden()
    print(f"Ready+printing strip: {mw.dash_jet_state_lbl.text()!r}")

    # Connected + INK low → chip visible
    st_inklow = PrinterStatus(
        connected=True, jet=JetState.READY, print_enabled=False,
        ink_ok=False, sol_ok=True, faults=[],
    )
    mw._update_dash_status(st_inklow)
    assert not mw.dash_ink_chip.isHidden()
    assert mw.dash_sol_chip.isHidden()
    print("INK low → INK chip visible")

    # Faults render as chips
    st_fault = PrinterStatus(
        connected=True, jet=JetState.FAULT,
        ink_ok=True, sol_ok=True,
        faults=["Lid/hood removed", "Encoder too fast"],
    )
    mw._update_dash_status(st_fault)
    assert not mw.dash_faults_host.isHidden()
    assert len(mw._dash_fault_chips) == 2, len(mw._dash_fault_chips)
    print(f"2 faults → {len(mw._dash_fault_chips)} chips rendered")

    # Faults clear when next snapshot is clean
    mw._update_dash_status(st_ready)
    assert mw.dash_faults_host.isHidden()
    assert len(mw._dash_fault_chips) == 0
    print("Faults clear when next snapshot is clean")

    # batch_master writes are gone — DB trigger handles them now.
    for fn in ("batch_master_insert_initial", "batch_master_finalize",
               "batch_master_is_finalized", "batch_master_has_row"):
        import core.database as _db
        assert not hasattr(_db, fn), f"core.database.{fn} should be removed"
    print("batch_master write functions removed (DB trigger does the work)")

    # ===== Counts use the printer main counter =====
    from core import printer_counts
    old_count_state = printer_counts._read_state()
    try:
        printer_counts._write_state({"date": "1999-01-01", "baseline": 999})

        mw._refresh_counts(PrinterStatus(connected=False, counter=428))
        assert int(mw.dash_today_value.text()) == 0
        assert int(mw.dash_total_value.text()) == 0
        print("Disconnected counters default to TODAY=0, ALL=0")

        mw._refresh_counts(PrinterStatus(connected=True, counter=428))
        assert int(mw.dash_today_value.text()) == 0
        assert int(mw.dash_total_value.text()) == 428
        print("First connected count 428 -> TODAY=0, ALL=428")

        mw._refresh_counts(PrinterStatus(connected=True, counter=431))
        assert int(mw.dash_today_value.text()) == 3
        assert int(mw.dash_total_value.text()) == 431
        print("Printer count 431 after baseline 428 -> TODAY=3, ALL=431")

        printer_counts._write_state({"date": "1999-01-01", "baseline": 999})
        mw._refresh_counts(PrinterStatus(connected=True, counter=450))
        assert int(mw.dash_today_value.text()) == 0
        assert int(mw.dash_total_value.text()) == 450
        print("System-day rollover re-baselines TODAY to 0")
    finally:
        if old_count_state:
            printer_counts._write_state(old_count_state)
        else:
            try:
                os.remove(printer_counts._state_path())
            except OSError:
                pass

    # ===== Connection gate =====
    mw._apply_connection_gate(False)
    assert not mw.jet_start_btn.isEnabled()
    assert not mw.jet_stop_btn.isEnabled()
    print("Offline gate disables dashboard jet tiles")

    mw._apply_connection_gate(True)
    assert mw.jet_start_btn.isEnabled()
    assert mw.jet_stop_btn.isEnabled()
    print("Connected gate enables dashboard jet tiles")

    # ===== Settings dialog fits without scrolling on 1024×600 =====
    from ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(mw.printer, mw)
    dlg.show()
    app.processEvents()
    print(f"Settings dialog size: {dlg.width()}×{dlg.height()}")
    # Each tab's content must fit inside the dialog (so QTabWidget
    # doesn't show a vertical scrollbar).
    for tab_attr in ("_db_tab", "_param_tab", "_sizing_tab"):
        tab = getattr(dlg, tab_attr)
        h = tab.sizeHint().height()
        print(f"  {tab_attr:<14} sizeHint h = {h} px")
        # Slot inside the tabwidget = dialog height - tab bar (~30) - footer (~40) - margins
        slot = dlg.height() - 80
        assert h <= slot, f"{tab_attr} sizeHint {h} > slot {slot}"
    print("Every settings tab fits without scrolling")
    dlg.hide()

    print()
    print("ALL UI SMOKE CHECKS PASSED")
    try:
        mw._counts_timer.stop()
        mw._db_check_timer.stop()
    except Exception:
        pass
    try:
        if mw._db_thread is not None and mw._db_thread.isRunning():
            mw._db_thread.wait(5000)
    except RuntimeError:
        pass
    mw.printer.stop_polling()
    mw.printer.disconnect()
    dlg.close()
    mw.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
