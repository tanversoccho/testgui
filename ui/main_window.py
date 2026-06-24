"""Main window.

Top bar  ─────────────────────────────────────────────────────────
Form (full width — bigger, readable fields for the 1024×600 Daewoo)
Action bar:  Jet Start │ Jet Stop │ System Info │ View │ Preview │ Settings
Status bar:  DB · Printer state · version

The Carton Label preview (QR artwork + status block + counters) lives
in its own top-level window, opened on demand via the Preview tile.
Operators see one thing at a time on the small panel — either the
form or the preview — instead of split-screening.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QMessageBox, QSizePolicy, QDialog,
    QApplication,
)

from config import config_app, config_loader
from core import database, lpn_generator
from core.carton_model import CartonLabel
from core import printer_counts
from core.printer_link import PrinterLink, PrinterStatus, JetState
from core.db_worker import DBCheckThread
from ui.form_panel import FormPanel
from ui.preview_panel import PreviewPanel
from ui.history_dialog import HistoryDialog
from ui.settings_dialog import SettingsDialog
from ui.connection_dialog import ConnectionDialog
from ui.notifications import show_error, show_warning, show_info, toast
from ui.theme import GREEN, RED, AMBER, ORANGE


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config_app.APP_NAME)
        # Target panel: 1024x600 Daewoo 21". Windows chrome eats
        # ~30 px (title bar) + ~40 px (taskbar) so the actual usable
        # area is ~1024x530. Open at the platform's reported available
        # geometry — fits the Daewoo exactly and grows on bigger
        # monitors. Capped at a sane upper bound for 4k displays.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None:
            self.resize(min(avail.width(), 1600), min(avail.height(), 1000))
        else:
            self.resize(1024, 560)
        self.setMinimumSize(900, 500)

        # ===== Top bar =====
        topbar = QFrame(); topbar.setObjectName("topbar"); topbar.setFixedHeight(40)
        tb = QHBoxLayout(topbar); tb.setContentsMargins(10, 3, 10, 3); tb.setSpacing(8)
        brand_box = QVBoxLayout(); brand_box.setSpacing(0)
        brand = QLabel("INKJET"); brand.setObjectName("brand")
        brand_sub = QLabel("PRINTER"); brand_sub.setObjectName("brandSub")
        brand_box.addWidget(brand); brand_box.addWidget(brand_sub)
        tb.addLayout(brand_box, 0)
        sep = QFrame(); sep.setFrameShape(QFrame.VLine); sep.setStyleSheet("color: #3D5895;")
        tb.addWidget(sep, 0)
        # Title removed per spec — top bar is just brand badge + pills + Connect.
        # A horizontal spacer keeps the right-side widgets pinned to the right.
        tb.addStretch(1)
        # Prominent DB status pill — clickable. Tap toggles:
        # offline → trigger a fresh DB check / connect; online → drop
        # every thread's cached connection so the next call reopens.
        self.db_status_pill = QPushButton("●  DB: ―")
        self.db_status_pill.setObjectName("statusPill")
        self.db_status_pill.setFlat(True)
        self.db_status_pill.setCursor(Qt.PointingHandCursor)
        self.db_status_pill.setStyleSheet(
            f"background: #2C2418; color: {AMBER}; font-weight: 800; "
            f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A4A2A; border-radius: 10px;"
        )
        self.db_status_pill.setToolTip("Click to connect / disconnect the database.")
        tb.addWidget(self.db_status_pill, 0)

        # Prominent printer status pill — clickable. Tap toggles:
        # disconnected → printer.connect() with current network
        # settings; connected → printer.disconnect().
        self.conn_chip = QPushButton("●  Disconnected")
        self.conn_chip.setFlat(True)
        self.conn_chip.setCursor(Qt.PointingHandCursor)
        self.conn_chip.setStyleSheet(
            f"background: #2A1818; color: {RED}; font-weight: 800; "
            f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A2A2A; border-radius: 10px;"
        )
        self.conn_chip.setToolTip("Click to connect / disconnect the printer.")
        tb.addWidget(self.conn_chip, 0)

        # Tiny "push activity" dot — flashes briefly when a label payload
        # has been pushed to the printer. Lives next to the conn chip so
        # the conn chip itself stays stable (no flicker between "Online"
        # and "Pushed").
        self.push_dot = QLabel("○")
        self.push_dot.setStyleSheet("color: #4A5A7A; font-weight: 800;")
        self.push_dot.setToolTip("Push activity")
        tb.addWidget(self.push_dot, 0)

        self.connect_top_btn = QPushButton("🔌  Connect")
        self.connect_top_btn.setObjectName("connectBtn")
        self.connect_top_btn.setCursor(Qt.PointingHandCursor)
        tb.addWidget(self.connect_top_btn, 0)

        # ===== Center: form takes the full width =====
        # The label preview is moved to a separate window (opened via
        # the Preview action tile) so the carton-input form gets the
        # whole dashboard. Bigger fields, less squinting.
        self.form = FormPanel()

        # Preview window — top-level (Qt.Window) but parented to the
        # main window so it shares lifetime and stays on top reliably.
        # Created hidden; the Preview tile shows it.
        self.preview = PreviewPanel(self)
        self.preview.setWindowFlags(Qt.Window)
        self.preview.setWindowTitle("Carton Label Preview")
        self.preview.resize(440, 620)
        self.preview.hide()

        # ===== Dashboard status strip =====
        # Lives between the form and the action bar. Carries the jet
        # state pill, INK / SOL warning chips, fault chips, and the
        # TODAY / ALL counters. These were previously inside the
        # preview panel — operators need them visible at all times,
        # not just when the preview window is open.
        status_strip = QFrame(); status_strip.setObjectName("dashStatus")
        status_strip.setStyleSheet(
            "QFrame#dashStatus { background: #1A2747; "
            "border-top: 1px solid #3D5895; border-bottom: 1px solid #3D5895; }"
        )
        # Compact — every pixel saved here is a pixel the form can use.
        status_strip.setFixedHeight(44)
        ss = QHBoxLayout(status_strip)
        ss.setContentsMargins(10, 2, 10, 2)
        ss.setSpacing(8)

        # Jet state — colored dot + label, leftmost.
        self.dash_jet_state_lbl = QLabel("●  Printer Offline")
        self.dash_jet_state_lbl.setStyleSheet(
            f"color: {RED}; font-size: 10pt; font-weight: 800;"
        )
        ss.addWidget(self.dash_jet_state_lbl, 0)

        # INK / SOL warning chips — hidden when OK, red when low.
        _CHIP_QSS = (
            "background: #7A2B2B; color: #FFD2D2; "
            "border: 1px solid #B05050; border-radius: 6px; "
            "padding: 4px 10px; font-weight: 800; font-size: 9pt;"
        )
        self.dash_ink_chip = QLabel("⚠  INK LOW")
        self.dash_ink_chip.setStyleSheet(_CHIP_QSS)
        self.dash_ink_chip.setVisible(False)
        ss.addWidget(self.dash_ink_chip, 0)

        self.dash_sol_chip = QLabel("⚠  SOL LOW")
        self.dash_sol_chip.setStyleSheet(_CHIP_QSS)
        self.dash_sol_chip.setVisible(False)
        ss.addWidget(self.dash_sol_chip, 0)

        # Fault chips host — one chip per active fault bit, hidden when
        # nothing is wrong. Built dynamically by _render_dashboard_faults.
        self.dash_faults_host = QWidget()
        self.dash_faults_layout = QHBoxLayout(self.dash_faults_host)
        self.dash_faults_layout.setContentsMargins(0, 0, 0, 0)
        self.dash_faults_layout.setSpacing(6)
        self.dash_faults_host.setVisible(False)
        ss.addWidget(self.dash_faults_host, 0)
        self._dash_fault_chips: list[QLabel] = []

        # Take all remaining horizontal space — pushes counters right.
        ss.addStretch(1)

        # TODAY / ALL counters — caption-above-value pair. Compact so
        # the strip stays at 44 px on the 1024×600 panel.
        def _counter_block(caption_text: str, value_color: str) -> tuple[QWidget, QLabel]:
            box = QWidget()
            v_box = QVBoxLayout(box); v_box.setContentsMargins(0, 0, 0, 0); v_box.setSpacing(0)
            cap = QLabel(caption_text)
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet("color: #B6C2DC; font-size: 7pt; letter-spacing: 1px;")
            val = QLabel("0")
            val.setAlignment(Qt.AlignCenter)
            val.setStyleSheet(f"color: {value_color}; font-size: 14pt; font-weight: 800;")
            val.setMinimumWidth(50)
            v_box.addWidget(cap)
            v_box.addWidget(val)
            return box, val

        today_box, self.dash_today_value = _counter_block("TODAY", ORANGE)
        total_box, self.dash_total_value = _counter_block("ALL", "white")
        ss.addWidget(today_box, 0)
        ss.addWidget(total_box, 0)

        # ===== Bottom action bar — Jet controls + app actions =====
        # Jet Start / Jet Stop tiles sit on the dashboard so the
        # operator always has them, even with the preview window
        # closed. Same handlers as the preview window's copy.
        action_bar = QFrame(); action_bar.setObjectName("actionBar")
        action_bar.setStyleSheet(
            "QFrame#actionBar { background: #1A2747; border-top: 1px solid #3D5895; }"
        )
        ab = QHBoxLayout(action_bar); ab.setContentsMargins(8, 3, 8, 3); ab.setSpacing(6)

        # ----- Printer-control tiles (left) -----
        # Jet ON/OFF + Print ON/OFF live as a cluster on the LEFT of the
        # action bar so the operator can find every "send to printer"
        # action in one place. The four app tiles (System Info, View,
        # Preview, Settings) sit on the right, separated by a thin
        # vertical divider for visual clarity.
        self.jet_start_btn  = QPushButton("▶\nJet Start");    self.jet_start_btn.setProperty("class", "tileGreen")
        self.jet_stop_btn   = QPushButton("■\nJet Stop");     self.jet_stop_btn.setProperty("class", "tileRed")
        self.print_on_btn   = QPushButton("●\nPrint ON");     self.print_on_btn.setProperty("class", "tileGreen")
        self.print_off_btn  = QPushButton("○\nPrint OFF");    self.print_off_btn.setProperty("class", "tileRed")
        # ----- App tiles (right) -----
        self.info_btn       = QPushButton("ℹ\nSystem Info");  self.info_btn.setProperty("class", "tileOrange")
        self.view_btn       = QPushButton("📋\nView");         self.view_btn.setProperty("class", "tileOrange")
        self.preview_btn    = QPushButton("👁\nPreview");      self.preview_btn.setProperty("class", "tileOrange")
        self.settings_btn   = QPushButton("⚙\nSettings");     self.settings_btn.setProperty("class", "tileOrange")

        _LEFT_TILES  = (self.jet_start_btn, self.jet_stop_btn,
                        self.print_on_btn,  self.print_off_btn)
        _RIGHT_TILES = (self.info_btn, self.view_btn,
                        self.preview_btn, self.settings_btn)
        for w in _LEFT_TILES + _RIGHT_TILES:
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        for w in _LEFT_TILES:
            ab.addWidget(w, 1)
        # Thin vertical divider between the printer-control cluster and
        # the app tiles — purely visual, no width stretch.
        _ab_sep = QFrame()
        _ab_sep.setFrameShape(QFrame.VLine)
        _ab_sep.setStyleSheet("color: #3D5895; background: #3D5895; max-width: 1px;")
        ab.addWidget(_ab_sep, 0)
        for w in _RIGHT_TILES:
            ab.addWidget(w, 1)

        # ===== Bottom status bar =====
        statusbar = QFrame(); statusbar.setObjectName("statusbar"); statusbar.setFixedHeight(22)
        sb = QHBoxLayout(statusbar); sb.setContentsMargins(10, 0, 10, 0); sb.setSpacing(14)
        self.db_chip       = QLabel("DB: ―")
        self.printer_chip  = QLabel("Printer: ―")
        self.message_chip  = QLabel("")
        self.version_chip  = QLabel(f"v{config_app.APP_VERSION}")
        for w in (self.db_chip, self.printer_chip, self.message_chip):
            w.setStyleSheet("color: #B6C2DC;"); sb.addWidget(w, 0)
        sb.addStretch(1)
        self.version_chip.setStyleSheet("color: #6F7FA0;"); sb.addWidget(self.version_chip, 0)

        # ===== Compose =====
        central = QWidget()
        v = QVBoxLayout(central); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(3)
        v.addWidget(topbar, 0)
        v.addWidget(self.form, 1)
        v.addWidget(status_strip, 0)
        v.addWidget(action_bar, 0)
        v.addWidget(statusbar, 0)
        self.setCentralWidget(central)

        # ===== Printer link =====
        self.printer = PrinterLink(self)
        self.printer.status_changed.connect(self._on_printer_status)
        self.printer.print_event.connect(self._on_print_event)
        self.printer.pushed.connect(self._on_pushed)
        self.printer.log.connect(self._on_log)

        # ===== Wire signals =====
        self.form.data_changed.connect(self._on_form_changed)
        self.form.clear_clicked.connect(self._on_form_clear)
        # Jet + Print controls live on the dashboard only — the preview
        # window is purely the label artwork now.
        self.jet_start_btn.clicked.connect(self._on_jet_start)
        self.jet_stop_btn.clicked.connect(self._on_jet_stop)
        self.print_on_btn.clicked.connect(self._on_print_on)
        self.print_off_btn.clicked.connect(self._on_print_off)
        self.info_btn.clicked.connect(self._show_printer_info)
        self.view_btn.clicked.connect(self._open_history)
        self.preview_btn.clicked.connect(self._open_preview)
        self.settings_btn.clicked.connect(self._open_settings)
        self.connect_top_btn.clicked.connect(self._open_connection_dialog)
        # The two top-bar pills double as toggle buttons.
        self.db_status_pill.clicked.connect(self._on_db_pill_clicked)
        self.conn_chip.clicked.connect(self._on_printer_pill_clicked)

        # Reprint state — when set, the very next print event marks the
        # original row as reprinted (STATUS = …-RPn) instead of inserting
        # a fresh row. Cleared on Clear, after the reprint fires, or when
        # the operator picks a different brand.
        self._reprint_lpn: str | None = None

        # Printer counter display. Defaults are 0/0 while disconnected;
        # after connect, ALL comes from the printer main counter and
        # TODAY is current minus the persisted baseline for this day.
        self._last_today_count: int = 0
        self._last_total_count: int = 0

        # First render — start gated until the user connects via the dialog
        self._apply_connection_gate(False)
        QTimer.singleShot(0, self.form._emit)
        # Counts start at 0/0 and are refreshed from the printer status.
        # The timer catches system-day rollover while the app stays open.
        self._db_known_online = False
        self._refresh_counts()
        self._counts_timer = QTimer(self); self._counts_timer.timeout.connect(self._refresh_counts); self._counts_timer.start(2500)

        # DB check — never block startup. Show "Checking…" immediately,
        # kick the worker off the main thread after the window is shown.
        self._db_thread: DBCheckThread | None = None
        self._db_pill_pending()
        QTimer.singleShot(150, self._start_db_check)
        # Re-check every 15 s. _start_db_check is a no-op if a previous
        # check is still running, so this is safe even when the VPN is
        # slow to respond.
        self._db_check_timer = QTimer(self)
        self._db_check_timer.timeout.connect(self._start_db_check)
        self._db_check_timer.start(15_000)

        # Start polling thread now so connection changes are picked up live.
        # No auto-connect — the user opens the Connect dialog to do that.
        self.printer.start_polling()

    # ---------- handlers ----------
    def _open_connection_dialog(self):
        dlg = ConnectionDialog(self.printer, self)
        dlg.exec()

    def _on_db_pill_clicked(self):
        """Click toggle on the DB status pill.

        Online   → reset all thread-local connections (next call reopens).
        Offline  → kick a fresh background DB check that opens a connection.
        """
        if self._db_known_online:
            database.reset_connections()
            self._db_known_online = False
            self.db_status_pill.setText("●  DB: Disconnected")
            self.db_status_pill.setStyleSheet(
                f"background: #2A1818; color: {RED}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A2A2A; border-radius: 12px;"
            )
            self.db_chip.setText("DB: disconnected")
            self.db_chip.setStyleSheet(f"color: {RED};")
            toast(self, "Database disconnected.", level="warn")
        else:
            self._db_pill_pending()
            self._start_db_check(force=True)

    def _on_printer_pill_clicked(self):
        """Click toggle on the Printer status pill.

        Connected    → tear down the link.
        Disconnected → printer.connect() with current network settings.
        """
        if self.printer.status.connected:
            self.printer.disconnect()
        else:
            ok = self.printer.connect()
            if not ok:
                # Status pill already shows the error via _on_printer_status.
                toast(self, "Printer connect failed — check Network Settings.",
                      level="error", duration_ms=4500)

    def _open_preview(self):
        """Surface the (top-level) preview window. Pushes a fresh draw
        of the current form state in case the operator opened it for
        the first time this session."""
        try:
            self.preview.update_preview(self.form.collect())
        except Exception:
            pass
        self.preview.show()
        self.preview.raise_()
        self.preview.activateWindow()

    def _on_form_clear(self):
        """Operator hit Clear — also cancel any pending reprint."""
        if self._reprint_lpn is not None:
            self._reprint_lpn = None
            toast(self, "Reprint cancelled — back to normal printing.",
                  level="info")

    def _on_form_changed(self, carton: CartonLabel):
        self.preview.update_preview(carton)
        # The form re-emits every second when the clock ticks (batch
        # time is auto-updated). Pushing on every tick spams the printer
        # and makes the "Pushed" dot flicker, so skip pushes when the
        # *operator-controlled* portion of the label hasn't changed.
        # Time-only ticks still update the preview — they just don't
        # re-push to the printer.
        sig = (
            carton.brand, carton.item_code, carton.item_desc,
            carton.lot_number, carton.sn, carton.grade, carton.shift,
            carton.size_code, carton.pcs_type, carton.pcs_per_ctn,
            carton.lpn_id, carton.carton_code,
        )
        if sig == getattr(self, "_last_push_sig", None):
            return
        self._last_push_sig = sig
        self.printer.push_label(text_payload=carton.brand, qr_payload=carton.build_qr_payload())

    def _on_printer_status(self, st: PrinterStatus):
        # Drive the dashboard strip: jet state, warnings, faults, and
        # the printer-main TODAY / ALL counters.
        self._update_dash_status(st)
        self._refresh_counts(st)

        # Connection chip
        if not st.connected:
            self.conn_chip.setText("●  Printer: Offline")
            self.conn_chip.setStyleSheet(
                f"background: #2A1818; color: {RED}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A2A2A; border-radius: 12px;"
            )
        elif st.simulated:
            self.conn_chip.setText("●  Printer: Simulator")
            self.conn_chip.setStyleSheet(
                f"background: #2C2418; color: {AMBER}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A4A2A; border-radius: 12px;"
            )
        else:
            # Distinguish two-way responding link from a one-way printer
            # (UDP printer that accepts writes but never replies). The
            # connection still counts as Online either way — the badge
            # just tells the operator whether reads are reliable.
            if st.two_way:
                self.conn_chip.setText("●  Printer: Online · responding")
                border = "#2A5A3D"
                bg     = "#182A21"
                color  = GREEN
            else:
                self.conn_chip.setText("●  Printer: Online · 1-way")
                border = "#5A4A2A"
                bg     = "#2C2418"
                color  = AMBER
            self.conn_chip.setStyleSheet(
                f"background: {bg}; color: {color}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid {border}; border-radius: 12px;"
            )

        # Printer chip — show jet state, fault, or print-on
        if not st.connected:
            self.printer_chip.setText("Printer: Disconnected")
            self.printer_chip.setStyleSheet(f"color: {RED};")
        elif st.faults:
            # Show human-readable fault NAMES (not the raw "0xNNNNNNNN"
            # word). First fault headlines the chip; if there are
            # more, append "(+N)" so the operator knows.
            names = list(st.faults)
            head = names[0]
            tail = f"  (+{len(names) - 1} more)" if len(names) > 1 else ""
            self.printer_chip.setText(f"Printer: {head}{tail}")
            self.printer_chip.setStyleSheet(f"color: {RED};")
            self.printer_chip.setToolTip(", ".join(names))
        elif st.fault:
            # Fallback — fault word set but no decoded names (shouldn't
            # normally happen with the current FAULT_BITS table).
            self.printer_chip.setText(f"Printer: {st.fault}"); self.printer_chip.setStyleSheet(f"color: {RED};")
        elif st.print_enabled and st.jet == JetState.READY:
            self.printer_chip.setText("Printer: Printing"); self.printer_chip.setStyleSheet(f"color: {GREEN};")
        else:
            self.printer_chip.setText(f"Printer: {st.jet.label}")
            self.printer_chip.setStyleSheet(
                f"color: {AMBER if st.jet.is_starting or st.jet == JetState.STOPPING else GREEN if st.jet == JetState.READY else '#B6C2DC'};"
            )

        # Lock down every printer-touching control if not operational
        self._apply_connection_gate(st.connected)

        # Top-right button label flips based on state
        if st.connected:
            self.connect_top_btn.setText("🔌  Connection")
        else:
            self.connect_top_btn.setText("🔌  Connect")

    # ----- Mandatory form-field gate -----
    # Required carton fields. Jet Start is blocked until every one of
    # these has a non-empty value. (CTN Qty and PCS/CTN are also
    # required; the form auto-fills them from the sample config but
    # falls back to operator-typed values when the lookup is empty.)
    REQUIRED_FIELDS = [
        ("brand",       "Brand"),
        ("item_code",   "Item Code"),
        ("size_code",   "Size"),
        ("grade",       "Grade"),
        ("shift",       "Shift"),
        ("lot_number",  "Lot Number"),
        ("pcs_type",    "CTN Type"),
        ("carton_qty",  "CTN Qty"),
        ("pcs_per_ctn", "PCS / CTN"),
    ]

    def _missing_form_fields(self) -> list:
        """Return human-readable labels of empty mandatory form fields."""
        try:
            c = self.form.collect()
        except Exception:
            return ["(form not ready)"]
        missing = []
        for attr, label in self.REQUIRED_FIELDS:
            v = getattr(c, attr, None)
            # Empty string, None, or numeric 0 (for qty/pcs) all count as missing.
            if v is None or (isinstance(v, str) and not v.strip()) \
               or (isinstance(v, (int, float)) and v == 0):
                missing.append(label)
        return missing

    def _on_jet_start(self):
        """Jet Start — gated on mandatory form fields. Fires pep's
        Control 'C' start_jet=True via PrinterLink.start_jet()."""
        missing = self._missing_form_fields()
        if missing:
            show_warning(
                self,
                "Fill required fields first",
                "Jet cannot start until every required field on the form "
                "has a value:\n\n  • " + "\n  • ".join(missing) +
                "\n\nFill them and try again.",
            )
            return
        self.printer.start_jet()

    def _on_jet_stop(self):
        """Jet Stop — always allowed. Fires pep's Control 'C' stop_jet=True."""
        self.printer.stop_jet()

    def _on_print_on(self):
        """Print ON — fires the Edit System Parameters 'E' packet with
        PTE=1 and the cached MOD/VSC/PRS/PTT/CHG setpoints so the DC800
        firmware accepts the packet (see PrinterLink.set_print_enabled
        for the long form of why the cached numerics are required)."""
        if not self.printer.set_print_enabled(True):
            return
        toast(self, "Print ON sent.", level="info", duration_ms=1500)

    def _on_print_off(self):
        """Print OFF — same shape as Print ON, PTE=0."""
        if not self.printer.set_print_enabled(False):
            return
        toast(self, "Print OFF sent.", level="info", duration_ms=1500)

    def _on_pushed(self, payload: str):
        """Visible feedback for a successful label push — flashes the
        small `push_dot` next to the conn chip. We intentionally do NOT
        touch the conn chip itself, because pushes can fire on every
        field change and the chip would visibly flicker between its
        steady state and "Pushed"."""
        if not self.printer.status.connected:
            return
        self.push_dot.setText("●")
        self.push_dot.setStyleSheet(f"color: {GREEN}; font-weight: 800;")
        self.push_dot.setToolTip(f"Pushed: {payload[:80]}")
        QTimer.singleShot(350, lambda: (
            self.push_dot.setText("○"),
            self.push_dot.setStyleSheet("color: #4A5A7A; font-weight: 800;"),
        ))

    def _on_print_event(self, counter: int):
        """Printer just printed a carton — record it.

        Two paths only:
          • Reprint mode (`_reprint_lpn` set): operator picked Reprint
            from the History view. Update the original row's STATUS to
            '…-RPn'. No new LPN, no new row, no batch_master change.
          • Normal: every print is a UNIQUE carton because the LPN
            generator emits a fresh LPN per print. Insert into
            XXFG_CARTON_MASTER, then run the batch_master workflow
            (initial N for new batches; Y finalise when the form's
            Batch Status is Y).

        There is NO automatic "duplicate detection". The earlier code
        treated same-content prints as reprints, but every print already
        has a unique LPN so they can never collide on the PK.
        """
        # ----- Reprint path (operator picked from History) -----
        if self._reprint_lpn:
            original_lpn = self._reprint_lpn
            ok, msg = database.mark_reprint(original_lpn)
            if ok:
                toast(self, f"Reprinted {original_lpn}", level="info")
            else:
                self._on_log(msg)
                toast(self, f"Reprint mark failed — {msg}",
                      level="error", duration_ms=4500)
            self._reprint_lpn = None
            # Resume live clock on the form (it was frozen by fill_from
            # so the reprinted label carried the original batch_date).
            try:
                self.form.exit_reprint_mode()
            except Exception:
                pass
            self._refresh_counts()
            return

        # ----- Fresh insert (every print) -----
        try:
            carton = self.form.collect()
            carton.lpn_id = lpn_generator.consume_next()
            carton.carton_code = carton.lpn_id
            carton.qr_code = carton.build_qr_payload()
        except Exception as e:
            show_error(self, "Print recorded — form error",
                       "Carton was printed but the app could not build the data row.",
                       details=e)
            return

        db_row = carton.as_db_row()
        ok, msg = database.insert_carton(db_row)
        if not ok:
            # Don't block the operator — auto-print fires fast. Surface
            # the failure but keep operating.
            self._on_log(msg)
            toast(self, f"DB insert failed — {msg}", level="error", duration_ms=4500)
        # No batch_master writes from the app — a DB trigger on
        # XXFG_CARTON_MASTER now maintains XXFG_CARTON_BATCH_MASTER
        # automatically after each carton row lands. The History
        # dialog's Batches view still reads that table, but the app
        # never inserts / updates it.
        self.form.refresh_lpn()
        self._refresh_counts()

    def _on_log(self, msg: str):
        self.message_chip.setText(msg)
        QTimer.singleShot(4000, lambda: self.message_chip.setText(""))

    def _open_history(self):
        # Pass the current DB-online state so the dialog can skip the
        # query entirely (and the oracledb native call that goes with
        # it) when we already know the DB is offline.
        dlg = HistoryDialog(self, db_online=self._db_known_online)
        dlg.reprint_requested.connect(self._on_reprint_request)
        dlg.exec()
        self._refresh_counts()

    def _on_reprint_request(self, row):
        """Operator picked a row from History → Reprint. Stash the
        original LPN so the next print event marks that row as reprinted
        instead of creating a new one."""
        self.form.fill_from(row)
        self._reprint_lpn = row.lpn_id
        QMessageBox.information(
            self, "Reprint loaded",
            f"Loaded {row.lpn_id} into the form.\n\n"
            f"On the next print, this row's STATUS will be updated to "
            f"'…-RP1/2/…' instead of inserting a new carton.\n\n"
            f"Make sure the jet is running and Print Start is on — the "
            f"printer will print on the next carton trigger.\n\n"
            f"Press Clear to cancel the reprint and resume normal printing.")

    def _open_settings(self):
        dlg = SettingsDialog(self.printer, self)
        # DB save uses an independent button in the DB tab; whenever it
        # fires, refresh the dashboard pill immediately (even while the
        # dialog stays open so the operator can save other tabs too).
        dlg.db_saved.connect(self._refresh_db_chip)
        dlg.exec()

    def _show_printer_info(self):
        st = self.printer.status
        if not st.connected:
            QMessageBox.warning(
                self, "Printer · System Info",
                f"Printer at {config_app.PRINTER_IP}:{config_app.PRINTER_PORT} is offline.\n"
                f"Connect the printer to read system info.")
            return
        # Refresh from the printer *before* opening the dialog so the
        # operator sees the live jet state, counter, fluid warnings,
        # and setpoints — not the last cached snapshot.
        self.printer.request_status()
        params = self.printer.read_system_parameters()
        st = self.printer.status   # may have been updated by request_status
        params_block = (
            f"Modulation:    {params['modulation']}\n"
            f"Viscosity:     {params['viscosity']}\n"
            f"Ink Pressure:  {params['ink_pressure']}\n"
            f"Nozzle Temp:   {params['nozzle_temp']}\n"
            f"Charge Value:  {params['charge_value']}\n"
            f"Print Enable:  {'Enable' if params['print_enable'] else 'Disable'}\n"
            if params else "(not available — printer did not reply)\n"
        )
        ink_line = (
            f"Ink:            LOW\n"
            if not st.ink_ok else "Ink:            OK\n"
        )
        sol_line = (
            f"Solvent:        LOW\n"
            if not st.sol_ok else "Solvent:        OK\n"
        )
        QMessageBox.information(
            self, "Printer · System Info",
            f"Address:        {config_app.PRINTER_IP}:{config_app.PRINTER_PORT} ({config_app.PRINTER_PROTO})\n"
            f"Connected:      {st.connected} {'(simulator)' if st.simulated else ''}\n"
            f"Jet state:      {st.jet.label}\n"
            f"Stage progress: {st.stage_progress}%\n"
            f"Print enabled:  {'Yes' if st.print_enabled else 'No'}\n"
            + ink_line + sol_line +
            f"Counter:        {st.counter}\n"
            f"Fault:          {st.fault or 'none'}\n"
            f"\n"
            f"— System parameters —\n"
            + params_block
        )

    def _refresh_counts(self, st: PrinterStatus | None = None):
        """Update TODAY / ALL from the printer's main print counter.

        Defaults are 0/0 while disconnected. On the first connected
        reading each system day, the current printer counter becomes the
        daily baseline. TODAY is current minus baseline; ALL is current.
        """
        st = st or self.printer.status
        today_count, total_count = printer_counts.counts_from_printer(
            getattr(st, "counter", None),
            bool(getattr(st, "connected", False)),
        )
        self._last_today_count = today_count
        self._last_total_count = total_count
        self.dash_today_value.setText(f"{today_count}")
        self.dash_total_value.setText(f"{total_count}")

    # ----- Dashboard status strip -----
    def _update_dash_status(self, st: PrinterStatus):
        """Repaint the dashboard status strip from a printer snapshot.

        Drives:
          * jet state pill (text + colour)
          * INK / SOL warning chips (hidden when OK)
          * fault chips (one per active ESWD bit, hidden when none)
        """
        # Jet state pill
        if not st.connected:
            self.dash_jet_state_lbl.setText("●  Printer Offline")
            self.dash_jet_state_lbl.setStyleSheet(
                f"color: {RED}; font-size: 10pt; font-weight: 800;"
            )
        else:
            label = st.jet.label
            if st.jet == JetState.READY and st.print_enabled:
                label = "Printing"
                color = GREEN
            elif st.jet == JetState.READY:
                color = GREEN
            elif st.jet.is_starting or st.jet == JetState.STOPPING:
                color = AMBER
            elif st.jet == JetState.FAULT:
                color = RED
            else:
                color = "#B6C2DC"
            self.dash_jet_state_lbl.setText(f"●  {label}")
            self.dash_jet_state_lbl.setStyleSheet(
                f"color: {color}; font-size: 10pt; font-weight: 800;"
            )

        # INK / SOL — show chips only when the printer is connected AND
        # the level is low (don't surface stale warnings when offline).
        self.dash_ink_chip.setVisible(st.connected and not st.ink_ok)
        self.dash_sol_chip.setVisible(st.connected and not st.sol_ok)

        # Fault chips — one per bit name. When offline, suppress them
        # all (last snapshot may be stale).
        faults = list(getattr(st, "faults", []) or []) if st.connected else []
        self._render_dashboard_faults(faults)

    def _render_dashboard_faults(self, faults: list):
        """Rebuild the fault-chip strip. Each fault name → one red
        chip with a warning icon. Hidden when there are no faults."""
        # Wipe the previous batch first.
        for chip in self._dash_fault_chips:
            self.dash_faults_layout.removeWidget(chip)
            chip.deleteLater()
        self._dash_fault_chips.clear()
        if not faults:
            self.dash_faults_host.setVisible(False)
            return
        self.dash_faults_host.setVisible(True)
        for name in faults:
            chip = QLabel(f"⚠  {name}")
            chip.setStyleSheet(
                "background: #7A2B2B; color: #FFD2D2; "
                "border: 1px solid #B05050; border-radius: 6px; "
                "padding: 4px 10px; font-weight: 700; font-size: 9pt;"
            )
            chip.setAlignment(Qt.AlignCenter)
            self.dash_faults_layout.addWidget(chip)
            self._dash_fault_chips.append(chip)

    # ----- DB indicator (driven by background worker, never blocks UI) -----
    def _db_pill_pending(self):
        """'Checking…' state — shown while the background worker is busy
        or before the very first check has completed."""
        self.db_status_pill.setText("●  DB: Checking…")
        self.db_status_pill.setStyleSheet(
            f"background: #2C2418; color: {AMBER}; font-weight: 800; "
            f"padding: 4px 12px; border: 1px solid #5A4A2A; border-radius: 12px;"
        )
        self.db_chip.setText("DB: checking…")
        self.db_chip.setStyleSheet(f"color: {AMBER};")

    def _start_db_check(self, force: bool = False):
        """Kick a background DB check. No-op if one is still in flight,
        unless force=True (e.g. just after Settings Save) in which case
        we abandon the previous worker — its result will be ignored
        because `_db_thread` has been rebound by then."""
        # Probe the previous thread carefully — after `deleteLater` runs,
        # the Python attribute still points at a destroyed C++ object,
        # and any method call (including isRunning()) raises RuntimeError.
        try:
            still_running = (
                self._db_thread is not None and self._db_thread.isRunning()
            )
        except RuntimeError:
            # Previous worker's C++ object was deleted — treat as not running.
            self._db_thread = None
            still_running = False

        if still_running:
            if not force:
                return
            try:
                self._db_thread.result.disconnect(self._on_db_check_done)
            except Exception:
                pass

        self._db_thread = DBCheckThread(self)
        self._db_thread.result.connect(self._on_db_check_done)
        self._db_thread.finished.connect(self._db_thread.deleteLater)
        self._db_thread.start()

    def _on_db_check_done(self, ok: bool, msg: str):
        """Marshalled back onto the GUI thread by Qt's signal/slot mechanism."""
        was_online = self._db_known_online
        # Counters can now hit the live DB safely (or stay on the mock store).
        self._db_known_online = ok and not config_app.USE_MOCK_DB
        # First-confirmed-online or recovered-from-offline → pull a
        # fresh count straight away so the operator sees the real
        # number instead of waiting for the next 2.5 s tick.
        if self._db_known_online and not was_online:
            self._refresh_counts()
        if ok:
            self.db_status_pill.setText("●  DB: Online")
            self.db_status_pill.setStyleSheet(
                f"background: #182A21; color: {GREEN}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #2A5A3D; border-radius: 12px;"
            )
            self.db_chip.setText("DB: connected"); self.db_chip.setStyleSheet(f"color: {GREEN};")
        elif config_app.USE_MOCK_DB:
            self.db_status_pill.setText("●  DB: Offline")
            self.db_status_pill.setStyleSheet(
                f"background: #2C2418; color: {AMBER}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A4A2A; border-radius: 12px;"
            )
            self.db_chip.setText("DB: offline"); self.db_chip.setStyleSheet(f"color: {AMBER};")
        else:
            self.db_status_pill.setText("●  DB: Error")
            self.db_status_pill.setStyleSheet(
                f"background: #2A1818; color: {RED}; font-weight: 800; "
                f"font-size: 9pt; padding: 2px 8px; border: 1px solid #5A2A2A; border-radius: 12px;"
            )
            self.db_chip.setText("DB: error"); self.db_chip.setStyleSheet(f"color: {RED};")
        self.db_status_pill.setToolTip(msg)
        self.db_chip.setToolTip(msg)

    def _refresh_db_chip(self):
        """Force a fresh DB check (after Settings Save).
        Abandons any in-flight check so the pill updates promptly."""
        self._db_pill_pending()
        self._start_db_check(force=True)

    def _apply_connection_gate(self, connected: bool):
        """Disable / enable every printer-touching widget based on connection.

        Settings and View remain available so the operator can edit DB
        credentials or browse history while the printer is offline.
        """
        self.info_btn.setEnabled(connected)
        # Dashboard printer tiles — only allowed while connected.
        self.jet_start_btn.setEnabled(connected)
        self.jet_stop_btn.setEnabled(connected)
        self.print_on_btn.setEnabled(connected)
        self.print_off_btn.setEnabled(connected)
        # Form input itself stays editable so operators can prepare data,
        # but the form pushes nothing to the printer when offline.

    def closeEvent(self, ev):
        try:
            self.printer.stop_polling(); self.printer.disconnect()
        except Exception:
            pass
        super().closeEvent(ev)
