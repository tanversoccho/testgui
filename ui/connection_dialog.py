"""Network Settings dialog — just connection params + Connect/Disconnect.

Two rows of inputs and a prominent live connection state indicator.
Settings auto-save whenever a Connect succeeds.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QLabel,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QPushButton
)

from config import config_app, config_loader
from core.printer_link import PrinterLink
from ui.theme import GREEN, RED, AMBER, ORANGE, fit_to_screen


class ConnectionDialog(QDialog):
    connection_changed = Signal()

    def __init__(self, printer: PrinterLink, parent=None):
        super().__init__(parent)
        self._printer = printer

        self.setWindowTitle("Network Settings")
        self.setMinimumWidth(640)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        wrap = QFrame(); wrap.setObjectName("netGroup")
        wrap.setStyleSheet(
            "QFrame#netGroup { background: #1A2747; border: 1px solid #3D5895; border-radius: 10px; }"
        )
        wl = QVBoxLayout(wrap); wl.setContentsMargins(14, 14, 14, 14); wl.setSpacing(12)

        wl.addWidget(self._heading("Network Settings"))

        # ---------- Row 1: Mode | Printer IP | Port | live state chip ----------
        row1 = QGridLayout(); row1.setHorizontalSpacing(8); row1.setVerticalSpacing(6)

        # Mode is locked to TCP — this printer firmware (LT800/DC80
        # family) does not reply on UDP, so offering UDP would just
        # set up failures. The widget is still a combo for visual
        # consistency, but only TCP is listed.
        row1.addWidget(self._lbl("Mode"), 0, 0)
        self.mode_cb = QComboBox()
        self.mode_cb.addItems(["TCP"])
        self.mode_cb.setCurrentText("TCP")
        self.mode_cb.setEnabled(False)
        row1.addWidget(self.mode_cb, 0, 1)

        row1.addWidget(self._lbl("Printer IP"), 0, 2)
        self.ip_edit = QLineEdit(config_app.PRINTER_IP)
        row1.addWidget(self.ip_edit, 0, 3)

        row1.addWidget(self._lbl("Port"), 0, 4)
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(config_app.PRINTER_PORT))
        row1.addWidget(self.port_spin, 0, 5)

        # Big visual connection state indicator
        self.conn_state_lbl = QLabel("●  Disconnected")
        self.conn_state_lbl.setStyleSheet(
            f"color: {RED}; font-weight: 800; font-size: 13pt; "
            f"padding: 6px 14px; background: #2A1818; border: 1px solid #5A2A2A; "
            f"border-radius: 8px;"
        )
        self.conn_state_lbl.setAlignment(Qt.AlignCenter)
        row1.addWidget(self.conn_state_lbl, 0, 6)

        row1.setColumnStretch(3, 2)
        row1.setColumnStretch(1, 1)

        # ---------- Row 2: Timeout | PKGID | Auto-incr | Connect | Disconnect ----------
        row2 = QGridLayout(); row2.setHorizontalSpacing(8); row2.setVerticalSpacing(6)

        row2.addWidget(self._lbl("Timeout"), 0, 0)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 60.0); self.timeout_spin.setSingleStep(0.5)
        self.timeout_spin.setSuffix(" s"); self.timeout_spin.setDecimals(1)
        self.timeout_spin.setValue(float(config_app.PRINTER_TIMEOUT))
        row2.addWidget(self.timeout_spin, 0, 1)

        row2.addWidget(self._lbl("PKGID"), 0, 2)
        self.pkgid_spin = QSpinBox(); self.pkgid_spin.setRange(1, 255)
        self.pkgid_spin.setValue(int(config_app.PRINTER_PKGID))
        row2.addWidget(self.pkgid_spin, 0, 3)

        self.auto_incr_cb = QCheckBox("Auto increment PKGID")
        self.auto_incr_cb.setChecked(bool(config_app.PRINTER_AUTO_INCREMENT_PKGID))
        row2.addWidget(self.auto_incr_cb, 0, 4)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("connectPrimary")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("connectSecondary")
        row2.addWidget(self.connect_btn, 0, 5)
        row2.addWidget(self.disconnect_btn, 0, 6)

        row2.setColumnStretch(3, 2)

        wl.addLayout(row1)
        wl.addLayout(row2)

        outer.addWidget(wrap)

        close_row = QHBoxLayout(); close_row.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("actionSecondary")
        close_row.addWidget(self.close_btn)
        outer.addLayout(close_row)

        # ---------- Wire ----------
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.close_btn.clicked.connect(self.accept)
        self._printer.status_changed.connect(self._on_printer_status)

        # Render current state once
        self._on_printer_status(self._printer.status)

        # Fit to whatever screen we're on (capped at 92% of available area)
        fit_to_screen(self, target_w=900, target_h=280)

        # Style overrides for Connect/Disconnect
        self.setStyleSheet(self.styleSheet() + f"""
            QPushButton#connectPrimary {{
                background: {GREEN}; color: white; border: none;
                border-radius: 8px; padding: 10px 22px;
                font-weight: 800; font-size: 12pt; min-height: 42px;
            }}
            QPushButton#connectPrimary:hover {{ background: #28C57E; }}
            QPushButton#connectPrimary:disabled {{ background: #2C4956; color: #6B8090; }}
            QPushButton#connectSecondary {{
                background: {RED}; color: white; border: none;
                border-radius: 8px; padding: 10px 22px;
                font-weight: 800; font-size: 12pt; min-height: 42px;
            }}
            QPushButton#connectSecondary:hover {{ background: #F25555; }}
            QPushButton#connectSecondary:disabled {{ background: #56342C; color: #908070; }}
        """)

    # ---------- helpers ----------
    def _lbl(self, t: str) -> QLabel:
        lb = QLabel(t)
        lb.setStyleSheet("color: white; font-weight: 600;")
        return lb

    def _heading(self, t: str) -> QLabel:
        lb = QLabel(t)
        lb.setStyleSheet(f"color: {ORANGE}; font-weight: 800; font-size: 12pt;")
        return lb

    # ---------- handlers ----------
    def _on_connect(self):
        ok = self._printer.apply_network_settings(
            mode="TCP",
            ip=self.ip_edit.text(),
            port=int(self.port_spin.value()),
            timeout_s=float(self.timeout_spin.value()),
            pkgid=int(self.pkgid_spin.value()),
            auto_increment=self.auto_incr_cb.isChecked(),
        )
        if ok:
            # Auto-persist whenever a connect succeeds
            saved, save_msg = config_loader.save()
            if not saved:
                from ui.notifications import show_warning
                show_warning(self, "Settings not saved to disk",
                             "Connected OK, but the network settings could "
                             "not be written to dikai_config.json:\n\n" + save_msg)
        self.connection_changed.emit()

    def _on_disconnect(self):
        self._printer.disconnect()
        self.connection_changed.emit()

    def _on_printer_status(self, st):
        if st.connected and st.simulated:
            self.conn_state_lbl.setText("●  Simulator")
            self.conn_state_lbl.setStyleSheet(
                f"color: {AMBER}; font-weight: 800; font-size: 13pt; "
                f"padding: 6px 14px; background: #2A2418; border: 1px solid #5A4A2A; "
                f"border-radius: 8px;"
            )
        elif st.connected:
            self.conn_state_lbl.setText(f"●  Connected")
            self.conn_state_lbl.setStyleSheet(
                f"color: {GREEN}; font-weight: 800; font-size: 13pt; "
                f"padding: 6px 14px; background: #182A21; border: 1px solid #2A5A3D; "
                f"border-radius: 8px;"
            )
        else:
            self.conn_state_lbl.setText("●  Disconnected")
            self.conn_state_lbl.setStyleSheet(
                f"color: {RED}; font-weight: 800; font-size: 13pt; "
                f"padding: 6px 14px; background: #2A1818; border: 1px solid #5A2A2A; "
                f"border-radius: 8px;"
            )

        # PKGID may have advanced on the wire — reflect it
        self.pkgid_spin.blockSignals(True)
        self.pkgid_spin.setValue(self._printer.get_pkgid())
        self.pkgid_spin.blockSignals(False)

        # Enable / disable Connect & Disconnect appropriately
        self.connect_btn.setEnabled(not st.connected)
        self.disconnect_btn.setEnabled(st.connected)

    def closeEvent(self, ev):
        try:
            self._printer.status_changed.disconnect(self._on_printer_status)
        except Exception:
            pass
        super().closeEvent(ev)
