"""Two-tab Settings dialog.

Tab 1 — Database
   Host / Port / Service / User / Password
   Test connection button. Toggle: keep records offline.

Tab 2 — Printer Parameters
   Print Enable + Modulation / Viscosity / Ink Pressure / Nozzle Temp /
   Charge Value, with their valid ranges, plus Read Current + Apply.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QTabWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton, QComboBox, QFrame,
    QDialogButtonBox, QSizePolicy, QMessageBox, QWidget, QFileDialog,
    QStackedWidget, QButtonGroup, QRadioButton, QScrollArea
)

from config import config_app, config_loader
from core import database
from core.db_worker import DBCheckThread
from core.printer_link import PrinterLink
from ui.theme import GREEN, RED, AMBER, ORANGE, fit_to_screen


# ---------------- Wheel-deaf widgets ----------------
# Operators routinely scroll the page while the cursor happens to be
# over a spinbox; default Qt behaviour treats that as a value change,
# which silently mutates print parameters. These subclasses ignore the
# wheel event so the parent scroll-area gets it instead.
class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, e):
        e.ignore()


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, e):
        e.ignore()


# ---------------- Database tab ----------------
class _DatabaseTab(QWidget):
    # Emitted after a successful save so the dashboard can refresh its
    # DB pill while the dialog stays open.
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(8)

        intro = QLabel(
            "Oracle connection. Empty credentials (or 'Keep offline') keep records in memory."
        )
        intro.setStyleSheet("color: #B6C2DC;")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # The two top sections (Instant Client, Connect-via + creds)
        # sit side-by-side so the dialog uses the 1024 px width and
        # avoids needing a vertical scrollbar on the 1024×600 panel.
        cols = QHBoxLayout(); cols.setSpacing(10)

        # ----- Oracle Instant Client section -----
        ic_box = QFrame(); ic_box.setObjectName("subBox")
        ic_box.setStyleSheet(
            "QFrame#subBox { background: #1A2747; border: 1px solid #3D5895; border-radius: 8px; }"
        )
        icl = QVBoxLayout(ic_box); icl.setContentsMargins(8, 6, 8, 8); icl.setSpacing(6)

        ic_title = QLabel("Oracle Instant Client (Thick mode)")
        ic_title.setStyleSheet(f"color: {ORANGE}; font-weight: 800;")
        icl.addWidget(ic_title)

        self.thick_cb = QCheckBox("Use Oracle Instant Client (Thick mode)")
        self.thick_cb.setChecked(bool(config_app.USE_THICK_MODE))
        icl.addWidget(self.thick_cb)

        # Instant Client path + browse
        ic_grid = QGridLayout(); ic_grid.setHorizontalSpacing(6); ic_grid.setVerticalSpacing(6)
        ic_grid.addWidget(self._lbl("Instant Client path:"), 0, 0)
        self.ic_path = QLineEdit(config_app.ORACLE_INSTANT_CLIENT_PATH)
        self.ic_path.setPlaceholderText("(auto-detect bundled instantclient_* folder)")
        ic_grid.addWidget(self.ic_path, 0, 1)
        ic_browse = QPushButton("Browse…"); ic_browse.setObjectName("actionSecondary")
        ic_browse.clicked.connect(lambda: self._pick_dir(self.ic_path, "Select Instant Client folder"))
        ic_grid.addWidget(ic_browse, 0, 2)

        # TNS_ADMIN is only relevant when using a TNS alias. The row is
        # hidden when Host/Port/Service mode is active so the operator
        # doesn't see fields they don't need (mirrors the pallet app's
        # EZConnect-only flow).
        self._tns_path_lbl = self._lbl("TNS_ADMIN path:")
        ic_grid.addWidget(self._tns_path_lbl, 1, 0)
        self.tns_path = QLineEdit(config_app.TNS_ADMIN_PATH)
        self.tns_path.setPlaceholderText("(default: <instant client>/network/admin)")
        ic_grid.addWidget(self.tns_path, 1, 1)
        self._tns_browse_btn = QPushButton("Browse…")
        self._tns_browse_btn.setObjectName("actionSecondary")
        self._tns_browse_btn.clicked.connect(lambda: self._pick_dir(self.tns_path, "Select TNS_ADMIN folder"))
        ic_grid.addWidget(self._tns_browse_btn, 1, 2)

        ic_grid.setColumnStretch(1, 1)
        icl.addLayout(ic_grid)

        # Live mode hint
        self.mode_lbl = QLabel(self._mode_text())
        self.mode_lbl.setStyleSheet("color: #B6C2DC; font-style: italic;")
        self.mode_lbl.setWordWrap(True)
        icl.addWidget(self.mode_lbl)
        icl.addStretch(1)

        cols.addWidget(ic_box, 1)

        # ----- Connect-via choice + credentials -----
        conn_box = QFrame(); conn_box.setObjectName("subBox")
        conn_box.setStyleSheet(
            "QFrame#subBox { background: #1A2747; border: 1px solid #3D5895; border-radius: 8px; }"
        )
        cnl = QVBoxLayout(conn_box); cnl.setContentsMargins(8, 6, 8, 8); cnl.setSpacing(6)

        ct = QLabel("Connect via")
        ct.setStyleSheet(f"color: {ORANGE}; font-weight: 800;")
        cnl.addWidget(ct)

        radio_row = QHBoxLayout()
        self.radio_easy = QRadioButton("Host / Port / Service")
        self.radio_tns  = QRadioButton("TNS Alias")
        if config_app.DB_USE_TNS:
            self.radio_tns.setChecked(True)
        else:
            self.radio_easy.setChecked(True)
        self._radios = QButtonGroup(self)
        self._radios.addButton(self.radio_easy, 0)
        self._radios.addButton(self.radio_tns, 1)
        radio_row.addWidget(self.radio_easy, 0)
        radio_row.addWidget(self.radio_tns, 0)
        radio_row.addStretch(1)
        cnl.addLayout(radio_row)

        # Manual rows — each is its own QHBoxLayout wrapped in a tiny container
        # widget. Hiding the container removes it cleanly from layout flow.
        self.host    = QLineEdit(config_app.DB_HOST)
        self.port    = _NoWheelSpinBox(); self.port.setRange(1, 65535); self.port.setValue(config_app.DB_PORT)
        self.service = QLineEdit(config_app.DB_SERVICE)
        self.tns_alias = QLineEdit(config_app.DB_TNS_ALIAS)
        self.tns_alias.setPlaceholderText("e.g. PROD_DB (must exist in tnsnames.ora)")

        def _row(label_text: str, widget) -> QWidget:
            box = QWidget()
            box.setFixedHeight(30)
            h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            lbl = self._lbl(label_text)
            lbl.setFixedWidth(90)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            widget.setFixedHeight(28)
            h.addWidget(lbl, 0)
            h.addWidget(widget, 1)
            return box

        self._row_host    = _row("Host:",      self.host)
        self._row_port    = _row("Port:",      self.port)
        self._row_service = _row("Service:",   self.service)
        self._row_tns     = _row("TNS Alias:", self.tns_alias)

        for r in (self._row_host, self._row_port, self._row_service, self._row_tns):
            cnl.addWidget(r)

        self._easy_rows = (self._row_host, self._row_port, self._row_service)
        self._tns_rows  = (self._row_tns,)
        # Initial visibility — also hides TNS_ADMIN when not using TNS alias
        self._on_target_changed()

        # Creds — same row pattern as above
        self.user = QLineEdit(config_app.DB_USER)
        self.pwd  = QLineEdit(config_app.DB_PASSWORD); self.pwd.setEchoMode(QLineEdit.Password)
        cnl.addWidget(_row("User:",     self.user))
        cnl.addWidget(_row("Password:", self.pwd))

        self.offline = QCheckBox("Keep DB offline (records held in memory)")
        self.offline.setChecked(config_app.USE_MOCK_DB)
        cnl.addWidget(self.offline)
        cnl.addStretch(1)   # absorb extra vertical space so form rows don't stretch

        cols.addWidget(conn_box, 1)
        v.addLayout(cols)

        # ----- Test connection -----
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setObjectName("actionSecondary")
        self.test_btn.clicked.connect(self._test)
        self.test_result = QLabel("")
        self.test_result.setStyleSheet("color: #B6C2DC;")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_btn, 0)
        test_row.addWidget(self.test_result, 1)
        v.addLayout(test_row)

        # ----- Per-tab Save (independent of Printer Parameters tab) -----
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save Database Settings")
        self.save_btn.setObjectName("paramsApply")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn, 0)
        v.addLayout(save_row)

        v.addStretch(1)

        # Wire
        self.radio_easy.toggled.connect(self._on_target_changed)
        self.radio_tns.toggled.connect(self._on_target_changed)

    def _on_target_changed(self):
        use_tns = self.radio_tns.isChecked()
        for r in self._easy_rows: r.setVisible(not use_tns)
        for r in self._tns_rows:  r.setVisible(use_tns)
        # TNS_ADMIN path only matters when using a TNS alias
        for w in (self._tns_path_lbl, self.tns_path, self._tns_browse_btn):
            w.setVisible(use_tns)

    # ----- helpers -----
    def _lbl(self, text: str) -> QLabel:
        lb = QLabel(text); lb.setStyleSheet("color: white; font-weight: 600;")
        return lb

    def _pick_dir(self, edit: QLineEdit, caption: str):
        path = QFileDialog.getExistingDirectory(self, caption, edit.text() or "")
        if path:
            edit.setText(path)

    def _mode_text(self) -> str:
        mode = database.current_mode()
        return f"Driver mode: {mode}"

    def _test(self):
        """Run the test on a background thread so the dialog stays
        responsive even if the DB host is unreachable (TCP timeout)."""
        # Snapshot — restored in _on_test_done so the operator's
        # previously-saved values aren't overwritten until they actually
        # click Save.
        self._test_prev = {k: getattr(config_app, k) for k in (
            "USE_THICK_MODE", "ORACLE_INSTANT_CLIENT_PATH", "TNS_ADMIN_PATH",
            "DB_USE_TNS", "DB_TNS_ALIAS",
            "DB_HOST", "DB_PORT", "DB_SERVICE", "DB_USER", "DB_PASSWORD",
            "USE_MOCK_DB",
        )}
        try:
            self.apply()
        except Exception as e:
            from ui.notifications import show_error
            for k, val in self._test_prev.items():
                setattr(config_app, k, val)
            show_error(self, "Settings — invalid form",
                       "Could not read values from the form.", details=e)
            return

        # Don't even spawn a worker for offline mode
        if config_app.USE_MOCK_DB:
            for k, val in self._test_prev.items():
                setattr(config_app, k, val)
            self.test_result.setText("DB: offline (records held in memory).")
            self.test_result.setStyleSheet(f"color: {AMBER};")
            return

        # Update UI for "testing in progress"
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        self.test_result.setText("Testing connection…")
        self.test_result.setStyleSheet(f"color: {AMBER};")

        # Run on background thread
        self._test_thread = DBCheckThread(self)
        self._test_thread.result.connect(self._on_test_done)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.start()

    def _on_test_done(self, ok: bool, msg: str):
        """Background test finished — restore the snapshot + render result."""
        # Restore the form snapshot (apply was only for the test run).
        for k, val in getattr(self, "_test_prev", {}).items():
            setattr(config_app, k, val)

        # Render result
        messages = []
        if config_app.USE_THICK_MODE:
            ic_ok, ic_msg = database._ensure_thick_initialized()
            if ic_ok:
                messages.append(f"Instant Client: OK  [{database.current_mode()}]")
            else:
                messages.append(f"Instant Client: FAIL  ({ic_msg})")
        messages.append(("DB: " if ok else "DB FAIL: ") + msg)
        self.test_result.setText("\n".join(messages))
        self.test_result.setStyleSheet(f"color: {GREEN if ok else RED};")
        # Refresh mode label (may have flipped on first successful init)
        self.mode_lbl.setText(self._mode_text())

        # Restore button
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Test Connection")

    def apply(self):
        config_app.USE_THICK_MODE             = self.thick_cb.isChecked()
        config_app.ORACLE_INSTANT_CLIENT_PATH = self.ic_path.text().strip()
        config_app.TNS_ADMIN_PATH             = self.tns_path.text().strip()
        config_app.DB_USE_TNS                 = self.radio_tns.isChecked()
        config_app.DB_TNS_ALIAS               = self.tns_alias.text().strip()
        config_app.DB_HOST                    = self.host.text().strip()
        config_app.DB_PORT                    = int(self.port.value())
        config_app.DB_SERVICE                 = self.service.text().strip()
        config_app.DB_USER                    = self.user.text().strip()
        config_app.DB_PASSWORD                = self.pwd.text()
        config_app.USE_MOCK_DB                = self.offline.isChecked()

    def _save(self):
        """Save only the Database tab values. Independent of the
        Printer Parameters tab so the operator can save DB settings
        even when the printer is offline."""
        from ui.notifications import show_error, toast
        try:
            self.apply()
        except Exception as e:
            show_error(self, "Database — invalid form",
                       "Could not read DB values from the form.", details=e)
            return
        # Connection settings may have changed — drop every thread's
        # cached connection so the next test/use opens a fresh one.
        database.reset_connections()

        ok, msg = config_loader.save()
        if not ok:
            show_error(self, "Database — could not save to disk",
                       "Values are active for this session but were not "
                       "written to dikai_config.json:\n\n" + msg)
            # Still emit saved so the dashboard re-checks
        toast(self.window(), "Database settings saved.", level="info")
        self.saved.emit()


# ---------------- Printer Parameters tab ----------------
class _PrinterParamsTab(QWidget):
    # Emitted after a successful save.
    saved = Signal()

    def __init__(self, printer: PrinterLink, parent=None):
        super().__init__(parent)
        self._printer = printer

        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(8)

        intro = QLabel(
            "Adjust the printer's runtime settings (ink, viscosity, "
            "temperature, charge). Press <b>Read Current</b> to load the "
            "printer's values, edit, then <b>Apply Changes</b> to send them."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #B6C2DC;")
        v.addWidget(intro)

        f = QFormLayout(); f.setSpacing(10); f.setLabelAlignment(Qt.AlignRight)

        self.print_enable = QComboBox()
        self.print_enable.addItem("Enable", True)
        self.print_enable.addItem("Disable", False)
        self.print_enable.setCurrentIndex(0 if config_app.PRINT_ENABLE else 1)

        self.modulation   = self._spin(5, 99,   config_app.MODULATION_SET)
        self.viscosity    = self._spin(150, 600, config_app.VISCOSITY_SET)
        self.ink_pressure = self._spin(100, 500, config_app.INK_PRESSURE)
        self.nozzle_temp  = self._spin(10, 60,  config_app.NOZZLE_TEMP)
        self.charge_value = self._spin(100, 200, config_app.CHARGE_VALUE)

        f.addRow("Print Enable",          self.print_enable)
        f.addRow("Modulation Set (5–99)", self.modulation)
        f.addRow("Viscosity Set (150–600)", self.viscosity)
        f.addRow("Ink Pressure (100–500)", self.ink_pressure)
        f.addRow("Nozzle Temp (10–60)",   self.nozzle_temp)
        f.addRow("Charge Value (100–200)", self.charge_value)
        v.addLayout(f)

        # Read / Apply buttons
        btn_row = QHBoxLayout()
        self.read_btn = QPushButton("Read Current")
        self.read_btn.setObjectName("actionSecondary")
        self.apply_btn = QPushButton("Apply Changes")
        self.apply_btn.setObjectName("paramsApply")
        btn_row.addWidget(self.read_btn, 1)
        btn_row.addWidget(self.apply_btn, 1)
        v.addLayout(btn_row)

        self.status_lbl = QLabel("Current setpoints: (press 'Read Current')")
        self.status_lbl.setStyleSheet(f"color: {GREEN};")
        v.addWidget(self.status_lbl)

        # ----- Per-tab Save (independent of Database tab) -----
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save Printer Parameters")
        self.save_btn.setObjectName("paramsApply")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn, 0)
        v.addLayout(save_row)

        v.addStretch(1)

        self.read_btn.clicked.connect(self._read)
        self.apply_btn.clicked.connect(self._apply)

        # Gate the printer-touching buttons on connection state
        connected = self._printer.is_operational()
        self.read_btn.setEnabled(connected)
        self.apply_btn.setEnabled(connected)
        if not connected:
            self.status_lbl.setText("Printer offline — Read/Apply disabled.")
            self.status_lbl.setStyleSheet(f"color: {AMBER};")

    def _spin(self, lo: int, hi: int, val: int) -> QSpinBox:
        s = _NoWheelSpinBox()
        s.setRange(lo, hi); s.setValue(val); s.setSingleStep(1)
        return s

    def _read(self):
        p = self._printer.read_system_parameters()
        if p is None:
            # Could be offline, no reply, or malformed reply — either
            # way, do NOT populate the spinners with cached config
            # values pretending it succeeded. Leave the existing values
            # in the UI alone and tell the operator explicitly.
            if not self._printer.is_operational():
                msg = "Read failed — printer offline."
            else:
                msg = "Read failed — printer did not reply. Try Reconnect."
            self.status_lbl.setText(msg)
            self.status_lbl.setStyleSheet(f"color: {RED};")
            return
        self.print_enable.setCurrentIndex(0 if p["print_enable"] else 1)
        self.modulation.setValue(p["modulation"])
        self.viscosity.setValue(p["viscosity"])
        self.ink_pressure.setValue(p["ink_pressure"])
        self.nozzle_temp.setValue(p["nozzle_temp"])
        self.charge_value.setValue(p["charge_value"])
        self.status_lbl.setText(
            f"Read: mod={p['modulation']}  visc={p['viscosity']}  "
            f"ink_p={p['ink_pressure']}  noz_t={p['nozzle_temp']}  "
            f"charge={p['charge_value']}  print={'Enable' if p['print_enable'] else 'Disable'}"
        )
        self.status_lbl.setStyleSheet(f"color: {GREEN};")

    def _apply(self):
        ok = self._printer.apply_system_parameters(
            print_enable = bool(self.print_enable.currentData()),
            modulation   = int(self.modulation.value()),
            viscosity    = int(self.viscosity.value()),
            ink_pressure = int(self.ink_pressure.value()),
            nozzle_temp  = int(self.nozzle_temp.value()),
            charge_value = int(self.charge_value.value()),
        )
        if ok:
            self.status_lbl.setText("Parameters applied.")
            self.status_lbl.setStyleSheet(f"color: {GREEN};")
        else:
            self.status_lbl.setText("Apply failed — check printer connection.")
            self.status_lbl.setStyleSheet(f"color: {RED};")

    def apply(self):
        # Persist printer-parameter values too (so they survive restart)
        config_app.PRINT_ENABLE   = bool(self.print_enable.currentData())
        config_app.MODULATION_SET = int(self.modulation.value())
        config_app.VISCOSITY_SET  = int(self.viscosity.value())
        config_app.INK_PRESSURE   = int(self.ink_pressure.value())
        config_app.NOZZLE_TEMP    = int(self.nozzle_temp.value())
        config_app.CHARGE_VALUE   = int(self.charge_value.value())

    def _save(self):
        """Save only the Printer Parameters tab values. Never contacts
        the printer — that's what the Apply Changes button is for. The
        operator can save these settings even when the printer is offline."""
        from ui.notifications import show_error, toast
        try:
            self.apply()
        except Exception as e:
            show_error(self, "Printer Parameters — invalid form",
                       "Could not read parameter values from the form.",
                       details=e)
            return
        ok, msg = config_loader.save()
        if not ok:
            show_error(self, "Printer Parameters — could not save to disk",
                       "Values are active for this session but were not "
                       "written to dikai_config.json:\n\n" + msg)
        toast(self.window(), "Printer parameters saved.", level="info")
        self.saved.emit()


# ---------------- Print Sizing tab ----------------
class _PrintSizingTab(QWidget):
    """QR + text sizing knobs.

    Top section — QR: max dots / quiet-zone border / error correction.
    Applied on the *next* push_label automatically (no network call).

    Bottom section — Message Params ('P'): width / height / dots /
    column repeats / character space etc. Sent as the 'P' Modify
    Message Params packet to scale the whole active message.
    """
    saved = Signal()

    def __init__(self, printer: PrinterLink, parent=None):
        super().__init__(parent)
        self._printer = printer

        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(8)

        intro = QLabel(
            "Control how big the QR code and text print on the carton. "
            "Apply sends the change to the printer; Save remembers it."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #B6C2DC;")
        v.addWidget(intro)

        # Fixed widths so spinboxes never resize after Apply — Qt's
        # default size policy adjusts to content/font, which is why the
        # boxes "shape-shift" on focus / value change.
        # Spin / label sizing for the Print Sizing grid. LABEL_W needs
        # ~150 px at 11pt to fit "Character Width" / "Character Height"
        # without clipping — the LattePanda screenshot showed
        # "Charact" being chopped because the previous 4-pair-per-row
        # layout couldn't give each label its share of the 1024 px
        # width. We now use 2 pairs per row (4 rows total) so each
        # label gets plenty of room.
        SPIN_W  = 130
        LABEL_W = 160
        COMBO_W = 220

        def _hdr(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setStyleSheet(f"color: {ORANGE}; font-weight: 800;")
            return lb

        def _lbl(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setStyleSheet("color: white; font-weight: 600; font-size: 10pt;")
            lb.setFixedWidth(LABEL_W)
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lb

        def _add_pair(grid: QGridLayout, row: int, col: int, label: str, widget):
            """Place [label][widget] at (row, col*2). Each pair uses 2 grid
            cols so a 2-pair-wide grid has 4 cols. Fixed widths prevent
            reflow when values change."""
            grid.addWidget(_lbl(label), row, col * 2)
            if isinstance(widget, _NoWheelSpinBox):
                widget.setFixedWidth(SPIN_W)
            elif isinstance(widget, _NoWheelComboBox):
                widget.setFixedWidth(COMBO_W)
            grid.addWidget(widget, row, col * 2 + 1)

        # ----- QR section -----
        qr_box = QFrame(); qr_box.setObjectName("subBox")
        qr_box.setStyleSheet(
            "QFrame#subBox { background: #1A2747; border: 1px solid #3D5895; border-radius: 8px; }"
        )
        ql = QVBoxLayout(qr_box); ql.setContentsMargins(6, 4, 6, 6); ql.setSpacing(4)
        ql.addWidget(_hdr("QR Code"))

        self.qr_max_dots = _NoWheelSpinBox()
        self.qr_max_dots.setRange(1, 34)
        self.qr_max_dots.setValue(int(getattr(config_app, "QR_MAX_DOTS", 34)))
        self.qr_max_dots.setToolTip("Smaller value → smaller QR on the carton.")

        self.qr_border = _NoWheelSpinBox()
        self.qr_border.setRange(0, 4)
        self.qr_border.setValue(int(getattr(config_app, "QR_BORDER", 2)))
        self.qr_border.setToolTip("White padding around the QR.")

        self.qr_ecc = _NoWheelComboBox()
        for letter, label in [("L", "L — Low"), ("M", "M — Medium"),
                              ("Q", "Q — High"), ("H", "H — Highest")]:
            self.qr_ecc.addItem(label, letter)
        idx = max(0, self.qr_ecc.findData(getattr(config_app, "QR_ERROR_CORRECTION", "L")))
        self.qr_ecc.setCurrentIndex(idx)
        self.qr_ecc.setToolTip("Higher = more robust scans but bigger QR.")

        qr_grid = QGridLayout()
        qr_grid.setHorizontalSpacing(10); qr_grid.setVerticalSpacing(8)
        _add_pair(qr_grid, 0, 0, "Max dots (1–34)",  self.qr_max_dots)
        _add_pair(qr_grid, 0, 1, "Border (0–4)",     self.qr_border)
        _add_pair(qr_grid, 1, 0, "Error correction", self.qr_ecc)
        qr_grid.setColumnStretch(4, 1)   # absorb extra space on the right
        ql.addLayout(qr_grid)

        # QR preview line — shows the resulting size live so the operator
        # can verify the QR fits before they trust the print.
        self.qr_preview_lbl = QLabel("")
        self.qr_preview_lbl.setStyleSheet("color: #B6C2DC; font-style: italic;")
        self.qr_preview_lbl.setWordWrap(True)
        ql.addWidget(self.qr_preview_lbl)

        v.addWidget(qr_box)

        # ----- Print Parameters section -----
        mp_box = QFrame(); mp_box.setObjectName("subBox")
        mp_box.setStyleSheet(
            "QFrame#subBox { background: #1A2747; border: 1px solid #3D5895; border-radius: 8px; }"
        )
        ml = QVBoxLayout(mp_box); ml.setContentsMargins(6, 4, 6, 6); ml.setSpacing(4)
        ml.addWidget(_hdr("Print Parameters"))

        self.msg_width   = self._spin(0, 1000, int(config_app.MSG_WIDTH))
        self.msg_width.setToolTip("Wider value → wider print on the carton.")
        self.msg_height  = self._spin(0, 10, int(config_app.MSG_HEIGHT))
        self.msg_height.setToolTip("Taller value → taller print.")
        self.msg_dots    = self._spin(1, 34, int(config_app.MSG_PRINTED_DOTS))
        self.msg_dots.setToolTip("Max vertical dot count.")
        self.msg_col_rep = self._spin(0, 10, int(config_app.MSG_COL_REPEATS))
        self.msg_col_rep.setToolTip("Higher = bolder, heavier print.")
        self.msg_char_sp = self._spin(0, 9, int(config_app.MSG_CHAR_SPACE))
        self.msg_char_sp.setToolTip("Space between characters.")
        self.msg_delay   = self._spin(0, 10000, int(config_app.MSG_DELAY))
        self.msg_delay.setToolTip("Wait time after sensor trigger before printing.")
        self.msg_trig    = self._spin(1, 99, int(config_app.MSG_TRIG_TIMES))
        self.msg_trig.setToolTip("Prints fired per single sensor trigger.")
        self.msg_gap     = self._spin(0, 10000, int(config_app.MSG_GAP))
        self.msg_gap.setToolTip("Gap between repeated prints.")

        # 4 rows × 2 pairs — gives each label enough room at 11pt so
        # captions like "Character Width" / "Multi-Print Gap" render
        # in full on the LattePanda. The Print Sizing tab is inside a
        # QScrollArea, so the extra two rows just scroll if the dialog
        # is taller than the LattePanda's usable area.
        # 2 rows × 4 pairs of (label, spinbox). Uses the dialog's
        # ~1000 px width so the Print Sizing tab fits the 1024×600
        # LattePanda without a scrollbar.
        mp_grid = QGridLayout()
        mp_grid.setHorizontalSpacing(8); mp_grid.setVerticalSpacing(4)
        _add_pair(mp_grid, 0, 0, "Character Width",  self.msg_width)
        _add_pair(mp_grid, 0, 1, "Character Height", self.msg_height)
        _add_pair(mp_grid, 0, 2, "Printed Dots",     self.msg_dots)
        _add_pair(mp_grid, 0, 3, "Column Repeats",   self.msg_col_rep)
        _add_pair(mp_grid, 1, 0, "Character Space",  self.msg_char_sp)
        _add_pair(mp_grid, 1, 1, "Print Delay",      self.msg_delay)
        _add_pair(mp_grid, 1, 2, "Prints / Trigger", self.msg_trig)
        _add_pair(mp_grid, 1, 3, "Multi-Print Gap",  self.msg_gap)
        mp_grid.setColumnStretch(8, 1)
        ml.addLayout(mp_grid)

        # Flips row — checkboxes side by side, also fixed-width labels so
        # the row aligns with the spinbox grid above.
        self.msg_reverse = QCheckBox("Reverse (mirror)")
        self.msg_reverse.setChecked(bool(config_app.MSG_REVERSE))
        self.msg_invert  = QCheckBox("Invert (upside-down)")
        self.msg_invert.setChecked(bool(config_app.MSG_INVERT))
        for cb in (self.msg_reverse, self.msg_invert):
            cb.setStyleSheet("color: white;")
        flip_row = QHBoxLayout(); flip_row.setSpacing(20)
        flip_lbl = _lbl("Flips")
        flip_row.addWidget(flip_lbl, 0)
        flip_row.addWidget(self.msg_reverse, 0)
        flip_row.addWidget(self.msg_invert, 0)
        flip_row.addStretch(1)
        ml.addLayout(flip_row)

        # Apply button — sends the 'P' packet RIGHT NOW and re-pushes
        # the label so the new sizing takes effect immediately.
        self.apply_btn = QPushButton("Apply Print Parameters")
        self.apply_btn.setObjectName("paramsApply")
        self.apply_btn.clicked.connect(self._apply_msg_params)
        ml.addWidget(self.apply_btn)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {GREEN};")
        # Single-line status — the offline / 'P sent' messages all fit
        # comfortably on the 1000-px dialog. Wrap-to-2-lines was eating
        # 20 px of vertical space and pushing the tab past the slot.
        self.status_lbl.setWordWrap(False)
        ml.addWidget(self.status_lbl)

        v.addWidget(mp_box)

        # ----- Per-tab Save (persists to dikai_config.json) -----
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save Print Sizing")
        self.save_btn.setObjectName("paramsApply")
        self.save_btn.clicked.connect(self._save)
        save_row.addWidget(self.save_btn, 0)
        v.addLayout(save_row)

        v.addStretch(1)

        # Disable network buttons when printer is offline (Save still works).
        connected = self._printer.is_operational()
        self.apply_btn.setEnabled(connected)
        if not connected:
            self.status_lbl.setText("Printer offline — 'P' apply disabled; Save still works.")
            self.status_lbl.setStyleSheet(f"color: {AMBER};")

        # Live QR preview
        self.qr_max_dots.valueChanged.connect(self._refresh_qr_preview)
        self.qr_border.valueChanged.connect(self._refresh_qr_preview)
        self.qr_ecc.currentIndexChanged.connect(self._refresh_qr_preview)
        # The effective QR cap is min(QR_MAX_DOTS, MSG_PRINTED_DOTS),
        # so changing Printed Dots also re-fires the preview.
        self.msg_dots.valueChanged.connect(self._refresh_qr_preview)
        self._refresh_qr_preview()

    def _spin(self, lo: int, hi: int, val: int) -> QSpinBox:
        s = _NoWheelSpinBox()
        s.setRange(lo, hi); s.setValue(val); s.setSingleStep(1)
        # Fixed width so the field doesn't reshape after Apply / focus.
        s.setFixedWidth(110)
        return s

    def _refresh_qr_preview(self):
        """Build a QR with the current knobs to show the operator the
        actual dot dimension that will be sent — INCLUDING the cap
        that comes from Printed Dots (the printer's vertical print
        area). When MSG_PRINTED_DOTS < QR_MAX_DOTS, the QR is limited
        by the printer's print area so the firmware doesn't scale it
        and squish the matrix."""
        from core.qr_builder import build_printer_bitmap
        # Probe with a representative payload — same shape as
        # carton_model.CartonLabel.build_qr_payload() output.
        probe = "AAS60601|L-V|E|17 Jun 26 02:48 PM|42"
        qr_cap   = int(self.qr_max_dots.value())
        dots_cap = int(self.msg_dots.value())
        effective = min(qr_cap, dots_cap) if dots_cap > 0 else qr_cap
        try:
            n, h, _bits = build_printer_bitmap(
                probe,
                max_size=effective,
                border=int(self.qr_border.value()),
                error_correction=self.qr_ecc.currentData() or "L",
            )
            tail = ""
            if effective == dots_cap and dots_cap < qr_cap:
                tail = f"  (capped by Printed Dots = {dots_cap}, not QR Max = {qr_cap})"
            self.qr_preview_lbl.setText(
                f"✓ Sample QR ({len(probe)} chars) → {n}×{h} dots — "
                f"fits inside effective cap {effective}.{tail}"
            )
            self.qr_preview_lbl.setStyleSheet(f"color: {GREEN}; font-style: italic;")
        except ValueError as e:
            self.qr_preview_lbl.setText(f"⚠  {e}")
            self.qr_preview_lbl.setStyleSheet(f"color: {RED}; font-style: italic;")

    def _apply_msg_params(self):
        ok = self._printer.apply_message_params(
            reverse        = self.msg_reverse.isChecked(),
            invert         = self.msg_invert.isChecked(),
            width          = int(self.msg_width.value()),
            delay          = int(self.msg_delay.value()),
            height         = int(self.msg_height.value()),
            dots           = int(self.msg_dots.value()),
            trigger_times  = int(self.msg_trig.value()),
            gap            = int(self.msg_gap.value()),
            column_repeats = int(self.msg_col_rep.value()),
            char_space     = int(self.msg_char_sp.value()),
        )
        if ok:
            self.status_lbl.setText(
                "'P' packet sent. Label re-pushed with new sizing. "
                "The new size takes effect on the next printed carton. "
                "Verify on the printer's Message Param menu."
            )
            self.status_lbl.setStyleSheet(f"color: {GREEN};")
        else:
            self.status_lbl.setText("Apply failed — check printer connection.")
            self.status_lbl.setStyleSheet(f"color: {RED};")

    def apply(self):
        config_app.QR_MAX_DOTS         = int(self.qr_max_dots.value())
        config_app.QR_BORDER           = int(self.qr_border.value())
        config_app.QR_ERROR_CORRECTION = self.qr_ecc.currentData() or "L"
        config_app.MSG_REVERSE         = bool(self.msg_reverse.isChecked())
        config_app.MSG_INVERT          = bool(self.msg_invert.isChecked())
        config_app.MSG_WIDTH           = int(self.msg_width.value())
        config_app.MSG_DELAY           = int(self.msg_delay.value())
        config_app.MSG_HEIGHT          = int(self.msg_height.value())
        config_app.MSG_PRINTED_DOTS    = int(self.msg_dots.value())
        config_app.MSG_TRIG_TIMES      = int(self.msg_trig.value())
        config_app.MSG_GAP             = int(self.msg_gap.value())
        config_app.MSG_COL_REPEATS     = int(self.msg_col_rep.value())
        config_app.MSG_CHAR_SPACE      = int(self.msg_char_sp.value())

    def _save(self):
        from ui.notifications import show_error, toast
        try:
            self.apply()
        except Exception as e:
            show_error(self, "Print Sizing — invalid form",
                       "Could not read sizing values from the form.",
                       details=e)
            return
        ok, msg = config_loader.save()
        if not ok:
            show_error(self, "Print Sizing — could not save to disk",
                       "Values are active for this session but were not "
                       "written to dikai_config.json:\n\n" + msg)
        # Force a fresh T+L push so the QR dot-dimensions on the printer
        # update right now — without this the printer keeps the old QR
        # size until the operator next edits a form field.
        repushed = self._printer.repush_pending_label()
        toast(
            self.window(),
            "Print sizing saved." + (
                "  Label re-pushed with new size." if repushed
                else "  (No label payload buffered yet — change a form field to push.)"
            ),
            level="info",
        )
        self.saved.emit()


# ---------------- Dialog ----------------
class SettingsDialog(QDialog):
    # Re-emitted from the DB tab so MainWindow can refresh its pill the
    # moment the operator saves DB settings — without the dialog needing
    # to close.
    db_saved      = Signal()
    params_saved  = Signal()
    sizing_saved  = Signal()

    def __init__(self, printer: PrinterLink, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 380)

        self._db_tab     = _DatabaseTab()
        self._param_tab  = _PrinterParamsTab(printer)
        self._sizing_tab = _PrintSizingTab(printer)

        # Bubble tab-level save signals up to the dialog.
        self._db_tab.saved.connect(self.db_saved.emit)
        self._param_tab.saved.connect(self.params_saved.emit)
        self._sizing_tab.saved.connect(self.sizing_saved.emit)

        # Wrap each tab in a QScrollArea so tall content (Print Sizing
        # has 4 grid rows + flip row + buttons that don't fit the
        # LattePanda's ~520 px usable height) scrolls cleanly instead
        # of being squashed by Qt's layout engine.
        def _scrolled(content: QWidget) -> QScrollArea:
            sa = QScrollArea()
            sa.setWidget(content)
            sa.setWidgetResizable(True)
            content.setMinimumSize(content.sizeHint())
            sa.setFrameShape(QFrame.NoFrame)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sa.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            return sa

        tabs = QTabWidget()
        tabs.addTab(_scrolled(self._db_tab),     "Database")
        tabs.addTab(_scrolled(self._param_tab),  "Printer Parameters")
        tabs.addTab(_scrolled(self._sizing_tab), "Print Sizing")

        # Footer: just Close. Each tab has its own Save button so the
        # operator can save DB and Printer Parameters independently —
        # printer being offline never blocks saving DB and vice versa.
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)

        v = QVBoxLayout(self); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(6)
        v.addWidget(tabs, 1); v.addWidget(bb, 0)

        # Fit-to-screen — fill the panel width so the 2-column layout
        # has the room it needs. fit_to_screen caps at 92 % of screen
        # in mainline (98 % in latepanda).
        fit_to_screen(self, target_w=1000, target_h=540)

        # Style the Apply Changes button in the Params tab as a teal accent
        self.setStyleSheet(self.styleSheet() + """
            QPushButton#paramsApply {
                background: #1FB6A0; color: white; border: none;
                border-radius: 10px; padding: 10px 16px;
                font-weight: 800; font-size: 11pt;
            }
            QPushButton#paramsApply:hover { background: #2BC9B3; }
            QPushButton#paramsApply:disabled { background: #2C4956; color: #6B8090; }
            QTabBar::tab {
                background: #243B6B; color: white; padding: 8px 18px;
                border-top-left-radius: 8px; border-top-right-radius: 8px;
            }
            QTabBar::tab:selected { background: #F37021; }
            QTabWidget::pane { border: 1px solid #3D5895; border-radius: 8px; top: -1px; }
        """)

