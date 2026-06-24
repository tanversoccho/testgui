"""Carton input form.

Layout (top to bottom, multi-column rows):
    Brand           |  LPN ID
    Item Code       |  Size
    Grade  | Shift  | Batch Date | Batch Time
    Carton Qty (0..1)            |  Sample Size (editable when qty < 1)
    Lot Number
    Batch No   (auto)
    QR Label   (auto)
"""
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal, QDate, QTime, QTimer, QDateTime
from PySide6.QtGui import QWheelEvent, QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCompleter, QPushButton, QSizePolicy, QWidget,
    QSpacerItem
)

from config import config_app
from core import database, lpn_generator
from core.carton_model import CartonLabel, current_shift
from core.db_worker import ItemFetchThread


# ===== Wheel-scrollable widgets =====
class WheelComboBox(QComboBox):
    def wheelEvent(self, e: QWheelEvent):
        delta = e.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(e)
        step = -1 if delta > 0 else 1
        new_idx = max(0, min(self.count() - 1, self.currentIndex() + step))
        if new_idx != self.currentIndex():
            self.setCurrentIndex(new_idx)
        e.accept()


# ===== Auto-uppercase helper =====
def _wire_uppercase(line_edit: QLineEdit) -> None:
    """Force-uppercase whatever the operator types into `line_edit`.

    Hooked on textEdited (NOT textChanged) so programmatic setText() —
    fill_from() during reprint, refresh_lpn() etc. — is not affected;
    only direct keystrokes get uppercased. setText() fires textChanged
    so downstream consumers still see the new value, but does not re-
    enter this slot."""
    def _on_edited(text: str) -> None:
        upper = text.upper()
        if text != upper:
            pos = line_edit.cursorPosition()
            line_edit.setText(upper)
            line_edit.setCursorPosition(pos)
    line_edit.textEdited.connect(_on_edited)


# ===== Form panel =====
class FormPanel(QFrame):
    """Emits `data_changed` whenever any field changes."""
    data_changed   = Signal(object)   # CartonLabel
    clear_clicked  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._suppress = False

        outer = QVBoxLayout(self)
        # 5 rows × 4 fields layout. Tuned for the LattePanda 1024×600
        # panel — slightly bigger fields than the original compact pass
        # to fill the empty bottom space, but careful not to overflow
        # the ~380 px form slot.
        outer.setContentsMargins(8, 6, 8, 4)
        outer.setSpacing(5)

        # Header row: title + small Clear button
        head = QHBoxLayout()
        title = QLabel("Carton Inputs"); title.setObjectName("sectionTitle")
        head.addWidget(title, 1)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedWidth(80)
        self.clear_btn.clicked.connect(self._on_clear)
        head.addWidget(self.clear_btn, 0)
        outer.addLayout(head)

        # Brand metadata stays in the combo box userData for DB/API rows.
        # The operator-facing form no longer shows org_code / inv_code.
        self.brand_banner = QLabel("")
        self.brand_banner.setObjectName("brandBanner")
        self.brand_banner.setStyleSheet(
            "QLabel#brandBanner { background: #1A2747; color: #F37021; "
            "padding: 6px 12px; border: 1px solid #3D5895; border-radius: 8px; "
            "font-weight: 800; font-size: 12pt; letter-spacing: 1px; }"
        )
        self.brand_banner.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.brand_banner)
        self.brand_banner.setVisible(False)

        # Build fields
        self.lpn_edit = QLineEdit(lpn_generator.peek_next())
        self.lpn_edit.setReadOnly(True)

        self.brand_cb = WheelComboBox()

        # Item Code: editable so the operator can type to search, but
        # NoInsert means only existing items can be picked. The completer
        # filters as they type, with case-insensitive substring matching.
        self.item_cb = WheelComboBox()
        self.item_cb.setEditable(True)
        self.item_cb.setInsertPolicy(QComboBox.NoInsert)
        self.item_cb.lineEdit().setPlaceholderText("type to search…")
        # Tighter completer — substring, not just prefix
        comp = self.item_cb.completer()
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        # Don't let the model auto-complete to the first match while still typing
        self.item_cb.setAutoCompletion(False) if hasattr(self.item_cb, "setAutoCompletion") else None

        # Size: auto-filled from the picked item — never editable
        self.size_edit = QLineEdit()
        self.size_edit.setReadOnly(True)
        self.size_edit.setPlaceholderText("auto from Item Code")

        self.grade_cb = WheelComboBox()
        self.grade_cb.addItems(config_app.GRADE_OPTIONS)

        # Shift — derived from the system clock, not operator-editable.
        # M = 06:01–14:00 · E = 14:01–22:00 · N = 22:01–06:00
        self.shift_edit = QLineEdit(current_shift())
        self.shift_edit.setReadOnly(True)
        self.shift_edit.setAlignment(Qt.AlignCenter)

        # Live clock fields — read-only
        self.batch_date = QLineEdit(QDate.currentDate().toString("dd/MM/yy"))
        self.batch_date.setReadOnly(True)

        # Hour:minute only — seconds were noisy and triggered downstream
        # work every second. With hh:mm the displayed value only changes
        # 60 times per hour, and _tick_clock skips _emit on same-minute
        # ticks so the printer isn't bombarded with redundant pushes.
        self.batch_time = QLineEdit(QTime.currentTime().toString("hh:mm AP"))
        self.batch_time.setReadOnly(True)

        # CTN Type / CTN / Regular-or-Sample pcs/ctn — values pulled from
        # XXFG_SAMPLE_CARTON_CONFIG once Size is known.
        self.pcs_type_cb = WheelComboBox()
        self.pcs_type_cb.addItems(["Regular", "Sample"])
        self.pcs_type_cb.setCurrentIndex(0)   # Regular default

        # CTN Qty — 0.00 to 1.00 float. Auto-filled from
        # XXFG_SAMPLE_CARTON_CONFIG when the size lookup returns a row;
        # falls back to user-editable when the lookup is empty so the
        # operator isn't blocked offline / for new sizes.
        self.ctn_edit = QLineEdit("1.00")
        self.ctn_edit.setReadOnly(True)
        _ctn_validator = QDoubleValidator(0.0, 1.0, 2, self)
        _ctn_validator.setNotation(QDoubleValidator.StandardNotation)
        self.ctn_edit.setValidator(_ctn_validator)

        # PCS / CTN — positive integer. Same fall-back rule as CTN.
        self.pcs_per_ctn_edit = QLineEdit()
        self.pcs_per_ctn_edit.setReadOnly(True)
        self.pcs_per_ctn_edit.setPlaceholderText("—")
        self.pcs_per_ctn_edit.setValidator(QIntValidator(1, 999999, self))

        # Cache the most recent config row so PCS Type toggling doesn't
        # need to re-query Oracle every time.
        self._sample_cfg = None
        # PCS-per-CTN from XXFG_UOM_CONVERSIONS_V for the picked item.
        # Used for: (a) Regular mode's auto-filled PCS/CTN value;
        # (b) Sample mode's CTN auto-recompute when the operator
        # changes the PCS/CTN value. None when offline or not in view.
        self._uom_normal_pcs: float | None = None
        # Reference to the third field's label so we can flip its caption
        # between "Normal pcs/ctn" and "Sample pcs/ctn".
        self._pcs_label = None
        # When True, the live-clock _tick_clock leaves batch_date /
        # batch_time / shift_edit alone — they're holding the values of
        # a carton being reprinted. Toggled by fill_from / clear() /
        # exit_reprint_mode().
        self._reprint_active: bool = False

        self.lot_edit = QLineEdit()
        self.lot_edit.setPlaceholderText("e.g. L-12")

        # SN — manual serial. Printed on the carton (appended to the QR
        # payload) but NOT stored in the database. Free text; not required.
        # SN — numeric serial (1, 2, 3 …). Printed on the carton but
        # NOT stored in the database. Bounded integer; not required.
        self.sn_edit = QLineEdit()
        self.sn_edit.setPlaceholderText("1, 2, 3…")
        self.sn_edit.setValidator(QIntValidator(0, 999999, self))

        self.batch_no_edit = QLineEdit()
        self.batch_no_edit.setReadOnly(True)

        # TOTAL_PLANNED_QTY — operator's planned total quantity for the
        # batch (column on XXFG_CARTON_MASTER). Replaces the old N/Y
        # Batch Status combo on the form; batch_master rows are now
        # built by a DB trigger so the app no longer needs that flag.
        self.total_planned_qty_edit = QLineEdit()
        self.total_planned_qty_edit.setPlaceholderText("planned qty…")
        self.total_planned_qty_edit.setValidator(QIntValidator(0, 999999, self))

        self.qr_str_edit = QLineEdit()
        self.qr_str_edit.setReadOnly(True)

        # Consistent height + size policy. 38 px tall, 11pt font — fills
        # the LattePanda's dashboard area without overflowing into the
        # action bar.
        _BIG_INPUT_QSS = "font-size: 11pt; padding: 3px 8px;"
        for w in (self.lpn_edit, self.brand_cb, self.item_cb, self.size_edit,
                  self.grade_cb, self.shift_edit, self.batch_date, self.batch_time,
                  self.pcs_type_cb, self.ctn_edit, self.pcs_per_ctn_edit,
                  self.lot_edit, self.sn_edit, self.batch_no_edit,
                  self.total_planned_qty_edit, self.qr_str_edit):
            w.setFixedHeight(38)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            w.setStyleSheet(w.styleSheet() + _BIG_INPUT_QSS)

        # Auto-uppercase the operator's typed text fields. Combo items
        # from BRANDS / DB stay as-is (changing those would mismatch the
        # DB queries that key on the exact strings). SN is numeric, so
        # no uppercase needed there.
        _wire_uppercase(self.lot_edit)
        _wire_uppercase(self.item_cb.lineEdit())

        # ---- Build the rows — 5 rows, each up to 4 fields, designed
        # to use the 1024 px width without wasted horizontal space. ----
        outer.addLayout(self._row4(
            "Brand",      self.brand_cb,
            "LPN ID",     self.lpn_edit,
            "Batch Date", self.batch_date,
            "Batch Time", self.batch_time,
        ))
        outer.addLayout(self._row4(
            "Item Code", self.item_cb,
            "Size",      self.size_edit,
            "Grade",     self.grade_cb,
            "Shift",     self.shift_edit,
        ))
        # Row 3 — CTN Type | CTN | PCS/CTN | Total Planned Qty.
        # The third field's value depends on the pcs_type selector
        # (Normal vs Sample) but the caption is always just PCS/CTN.
        outer.addLayout(self._row4_with_flipping_third(
            "CTN Type",    self.pcs_type_cb,
            "CTN",         self.ctn_edit,
            "PCS/CTN",     self.pcs_per_ctn_edit,
            "Total Planned Qty", self.total_planned_qty_edit,
        ))
        # Row 4 — Lot Number | SN | Batch No (Batch No takes 2× weight
        # so the long auto-generated string fits without clipping).
        lt_row = QHBoxLayout(); lt_row.setSpacing(5)
        lt_row.addWidget(self._field_box("Lot Number", self.lot_edit), 1)
        lt_row.addWidget(self._field_box("SN", self.sn_edit), 1)
        lt_row.addWidget(self._field_box("Batch No", self.batch_no_edit), 2)
        outer.addLayout(lt_row)
        outer.addLayout(self._row1("QR Label", self.qr_str_edit))

        outer.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # ---- Wire signals ----
        self.brand_cb.currentTextChanged.connect(self._on_brand_changed)
        self.item_cb.currentTextChanged.connect(self._on_item_changed)
        self.lot_edit.textChanged.connect(self._emit)
        self.sn_edit.textChanged.connect(self._emit)
        self.size_edit.textChanged.connect(self._on_size_changed)
        self.grade_cb.currentTextChanged.connect(self._emit)
        # Shift is auto-derived from the clock — no user subscription needed.
        self.pcs_type_cb.currentTextChanged.connect(self._on_pcs_type_changed)
        self.total_planned_qty_edit.textChanged.connect(self._emit)
        # Editable CTN qty / PCS — re-emit when operator types.
        # CTN also goes through a hard clamp slot so values > 1 can
        # never sit in the field (Qt's QDoubleValidator alone is too
        # lenient with "intermediate" out-of-range typing).
        self.ctn_edit.textChanged.connect(self._on_ctn_text_changed)
        self.pcs_per_ctn_edit.textChanged.connect(self._emit)
        # User keystrokes only — recompute CTN from PCS in Sample mode.
        self.pcs_per_ctn_edit.textEdited.connect(self._on_pcs_per_ctn_text_edited)

        self._reload_brands()

        # Live clock — tick every second so the display stays current,
        # but only re-emit data_changed on the minute boundary so the
        # downstream push/printer traffic is once-per-minute instead of
        # once-per-second.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()
        self._last_emitted_minute: int = -1

    # ---------- row builders ----------
    def _label(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet("color: white; font-weight: 600; font-size: 10pt;")
        return lb

    # Per-widget label registry — used by _refresh_null_flags to flip
    # an inline icon on/off in the field's caption.
    def _ensure_label_registry(self):
        if not hasattr(self, "_field_labels"):
            self._field_labels: dict = {}
            self._field_caption: dict = {}

    def _field_box(self, label_text: str, widget: QWidget) -> QWidget:
        """Stack label-above-field in a tight vertical box. Registers
        the label widget so the null indicator can update its text."""
        self._ensure_label_registry()
        box = QWidget()
        v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        lbl = self._label(label_text)
        v.addWidget(lbl)
        v.addWidget(widget)
        self._field_labels[widget] = lbl
        self._field_caption[widget] = label_text
        return box

    def _row1(self, label: str, widget: QWidget) -> QHBoxLayout:
        h = QHBoxLayout(); h.setSpacing(5)
        h.addWidget(self._field_box(label, widget), 1)
        return h

    def _row2(self, l1: str, w1: QWidget, l2: str, w2: QWidget) -> QHBoxLayout:
        h = QHBoxLayout(); h.setSpacing(5)
        h.addWidget(self._field_box(l1, w1), 1)
        h.addWidget(self._field_box(l2, w2), 1)
        return h

    def _row3(self, l1, w1, l2, w2, l3, w3,
              keep_label_ref_for_third: bool = False) -> QHBoxLayout:
        h = QHBoxLayout(); h.setSpacing(5)
        h.addWidget(self._field_box(l1, w1), 1)
        h.addWidget(self._field_box(l2, w2), 1)
        if keep_label_ref_for_third:
            # Build the third field box manually so we can hold a reference
            # to its label and flip its caption at runtime.
            self._ensure_label_registry()
            self._pcs_label = self._label(l3)
            box = QWidget()
            v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
            v.addWidget(self._pcs_label)
            v.addWidget(w3)
            h.addWidget(box, 1)
            # Register the pcs/ctn field's label too — the caption gets
            # flipped between "Normal pcs/ctn" and "Sample pcs/ctn", but
            # we still want the null icon to track its required state.
            self._field_labels[w3] = self._pcs_label
            self._field_caption[w3] = l3
        else:
            h.addWidget(self._field_box(l3, w3), 1)
        return h

    def _row4_with_flipping_third(self, l1, w1, l2, w2, l3, w3, l4, w4) -> QHBoxLayout:
        """4-column row where the third field's label is held in
        `self._pcs_label` so _apply_pcs_type_view() can flip it between
        'Normal pcs/ctn' and 'Sample pcs/ctn' at runtime."""
        self._ensure_label_registry()
        h = QHBoxLayout(); h.setSpacing(5)
        h.addWidget(self._field_box(l1, w1), 1)
        h.addWidget(self._field_box(l2, w2), 1)
        # Third field — manual build so we keep a reference to the label.
        self._pcs_label = self._label(l3)
        box = QWidget()
        v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        v.addWidget(self._pcs_label)
        v.addWidget(w3)
        h.addWidget(box, 1)
        self._field_labels[w3] = self._pcs_label
        self._field_caption[w3] = l3
        h.addWidget(self._field_box(l4, w4), 1)
        return h

    def _row4(self, l1, w1, l2, w2, l3, w3, l4, w4) -> QHBoxLayout:
        h = QHBoxLayout(); h.setSpacing(4)
        h.addWidget(self._field_box(l1, w1), 1)
        h.addWidget(self._field_box(l2, w2), 1)
        h.addWidget(self._field_box(l3, w3), 1)
        h.addWidget(self._field_box(l4, w4), 1)
        return h

    # ---------- dropdown reloads ----------
    def _reload_brands(self):
        self._suppress = True
        self.brand_cb.clear()
        for b in database.fetch_brands():
            # Stash the whole dict so we have org_code + inv_code together.
            self.brand_cb.addItem(b["brand"], userData=b)
        self._suppress = False
        if self.brand_cb.count():
            self._on_brand_changed(self.brand_cb.currentText())

    def _update_brand_banner(self):
        self.brand_banner.clear()

    def _on_brand_changed(self, brand: str):
        if self._suppress:
            return
        # Keep the hidden banner clear; org/inv codes remain stored as data.
        self._update_brand_banner()
        # Clear the dropdown immediately so the operator sees a fresh state
        self._suppress = True
        self.item_cb.clear()
        self.size_edit.clear()
        self._suppress = False

        # In mock mode fetch is instant (returns [] or fallback) — do
        # synchronously to avoid the overhead of a thread.
        if config_app.USE_MOCK_DB:
            for it in database.fetch_item_codes(brand):
                self.item_cb.addItem(it["code"], userData=it)
            self._on_item_changed(self.item_cb.currentText())
            return

        # Live DB — fetch on a worker thread so VPN-down / slow Oracle
        # can't freeze the UI for up to 30 s.
        self.item_cb.lineEdit().setPlaceholderText("Loading items…")
        self._latest_brand = brand
        thread = ItemFetchThread(brand, self)
        thread.result.connect(self._on_items_fetched)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_items_fetched(self, brand: str, items: list):
        """Worker thread reported back. Ignore stale results if the user
        already moved on to a different brand."""
        if brand != getattr(self, "_latest_brand", brand):
            return
        self._suppress = True
        self.item_cb.clear()
        for it in items:
            self.item_cb.addItem(it["code"], userData=it)
        self.item_cb.lineEdit().setPlaceholderText("type to search…")
        self._suppress = False
        self._on_item_changed(self.item_cb.currentText())

    def _on_item_changed(self, code: str):
        # Only auto-fill Size when the typed text actually matches a real
        # item in the dropdown — otherwise we'd clobber Size while the
        # operator is still typing their search.
        data = None
        idx = self.item_cb.findText(code)
        if idx >= 0:
            data = self.item_cb.itemData(idx)
        if isinstance(data, dict) and data.get("size"):
            self.size_edit.setText(data["size"])
        elif idx < 0:
            # No match — clear Size so the operator sees that the cascade
            # is incomplete and the PCS Type row will go to "—".
            self.size_edit.clear()
        self._emit()

    def _on_size_changed(self, size: str):
        """Size or item has changed — refresh the sample-config row AND
        the UOM-based normal PCS/CTN, then drive the CTN + PCS/CTN
        fields off whichever CTN type the operator currently has."""
        self._sample_cfg = database.fetch_sample_config(size.strip())
        # XXFG_UOM_CONVERSIONS_V is keyed by (ORGANIZATION_ID, ITEM_CODE).
        brand_data = self.brand_cb.currentData() or {}
        inv_code = brand_data.get("inv_code") if isinstance(brand_data, dict) else None
        item_code = (self.item_cb.currentText() or "").strip()
        if inv_code and item_code:
            self._uom_normal_pcs = database.fetch_normal_pcs_per_ctn(inv_code, item_code)
        else:
            self._uom_normal_pcs = None
        self._apply_pcs_type_view()
        self._emit()

    def _on_pcs_type_changed(self, _txt: str):
        """User flipped Regular ↔ Sample — refresh CTN + pcs/ctn display."""
        self._apply_pcs_type_view()
        self._emit()

    def _apply_pcs_type_view(self):
        """Drive CTN + PCS/CTN from the current CTN Type + the two data
        sources for this item.

        Regular  PCS/CTN ← XXFG_UOM_CONVERSIONS_V (fallback: sample_cfg.normal_pcs)
                 CTN     ← 1.00
        Sample   PCS/CTN ← sample_cfg.sample_pcs (EDITABLE)
                 CTN     ← PCS / uom_normal_pcs (fallback: sample_cfg.conversion)

        When the operator edits PCS/CTN in Sample mode, CTN is
        recomputed live via _on_pcs_per_ctn_text_edited."""
        is_sample = self.pcs_type_cb.currentText() == "Sample"
        cfg = self._sample_cfg
        uom_pcs = self._uom_normal_pcs

        # Caption is always "PCS/CTN" — the Normal/Sample distinction is
        # the CTN Type combo to its left. Reset the stored caption every
        # pass so the null-indicator icon rebuilds against the correct
        # base text.
        if self._pcs_label is not None:
            new_caption = "PCS/CTN"
            self._pcs_label.setText(new_caption)
            if hasattr(self, "_field_caption"):
                self._field_caption[self.pcs_per_ctn_edit] = new_caption

        # ----- Resolve the source values for PCS and CTN -----
        normal_pcs = (
            int(uom_pcs) if uom_pcs is not None and uom_pcs > 0 else
            (int(cfg["normal_pcs"]) if cfg else 0)
        )

        if not is_sample:
            # Regular — PCS/CTN from UOM view (read-only); CTN = 1.00.
            self.ctn_edit.setReadOnly(True)
            self.ctn_edit.setText("1.00")
            if normal_pcs > 0:
                self.pcs_per_ctn_edit.setReadOnly(True)
                self.pcs_per_ctn_edit.setText(str(normal_pcs))
                self.pcs_per_ctn_edit.setPlaceholderText("—")
            else:
                # No data either way — let the operator type.
                self.pcs_per_ctn_edit.setReadOnly(False)
                self.pcs_per_ctn_edit.setText("0")
                self.pcs_per_ctn_edit.setPlaceholderText("type number…")
            self._refresh_null_flags()
            return

        # ----- Sample mode -----
        # PCS/CTN is editable so the operator can override the default;
        # CTN is read-only and derived from PCS / uom_normal_pcs.
        self.pcs_per_ctn_edit.setReadOnly(False)
        self.pcs_per_ctn_edit.setPlaceholderText("type number…")
        self.ctn_edit.setReadOnly(True)
        if cfg is not None:
            self.pcs_per_ctn_edit.setText(str(int(cfg["sample_pcs"])))
            if normal_pcs > 0:
                ctn = max(0.0, min(1.0, int(cfg["sample_pcs"]) / float(normal_pcs)))
                self.ctn_edit.setText(f"{ctn:.2f}")
            else:
                # No UOM baseline — keep the sample_cfg's stored conversion.
                self.ctn_edit.setText(f"{cfg['conversion']:.2f}")
        else:
            # No sample_cfg at all — let the operator type pcs; CTN
            # stays 0 until pcs is filled in.
            self.pcs_per_ctn_edit.setText("0")
            self.ctn_edit.setText("0.00")
        self._refresh_null_flags()

    def _on_pcs_per_ctn_text_edited(self, text: str):
        """Sample mode: when the operator types a custom PCS/CTN value,
        recompute CTN as PCS / uom_normal_pcs (clamped to 0..1).

        textEdited fires only on user keystrokes, NOT on the
        programmatic setText() inside _apply_pcs_type_view — so this
        slot is safe to wire without re-entrancy concerns."""
        if self.pcs_type_cb.currentText() != "Sample":
            return
        if not self._uom_normal_pcs or self._uom_normal_pcs <= 0:
            return
        try:
            entered_pcs = int((text or "0").strip() or "0")
        except ValueError:
            return
        if entered_pcs <= 0:
            return
        new_ctn = max(0.0, min(1.0, entered_pcs / float(self._uom_normal_pcs)))
        self.ctn_edit.blockSignals(True)
        self.ctn_edit.setText(f"{new_ctn:.2f}")
        self.ctn_edit.blockSignals(False)

    # ---------- clear / clock ----------
    def _on_clear(self):
        self.clear()
        self.clear_clicked.emit()

    def _tick_clock(self):
        # In reprint mode the date/time/shift fields hold the values
        # of the carton being reprinted — they MUST NOT be overwritten
        # by the live clock, otherwise the reprinted label would carry
        # today's date instead of the original print day.
        if getattr(self, "_reprint_active", False):
            return
        now = QDateTime.currentDateTime()
        self.batch_date.setText(now.date().toString("dd/MM/yy"))
        self.batch_time.setText(now.time().toString("hh:mm AP"))
        # Shift letter auto-rolls with the clock
        self.shift_edit.setText(current_shift())
        # Re-emit data_changed only when the minute boundary crosses —
        # avoids 60× redundant signals (and printer pushes) per minute.
        minute = now.time().hour() * 60 + now.time().minute()
        if minute != self._last_emitted_minute:
            self._last_emitted_minute = minute
            self._emit()

    def exit_reprint_mode(self):
        """Unfreeze the date/time/shift fields. Called by main_window
        after the operator finishes the reprint (or hits Clear)."""
        self._reprint_active = False
        # Force the next tick to repaint with the live values.
        self._last_emitted_minute = -1
        self._tick_clock()

    # ---------- public ----------
    def _read_total_planned_qty(self) -> float:
        """Defensive parse — empty or half-typed input becomes 0.0."""
        try:
            return float(self.total_planned_qty_edit.text().strip() or "0")
        except ValueError:
            return 0.0

    def collect(self) -> CartonLabel:
        item_data = self.item_cb.currentData() or {}
        now = datetime.now()
        size = self.size_edit.text().strip()
        pcs_type = self.pcs_type_cb.currentText()
        cfg = self._sample_cfg

        if pcs_type == "Sample" and cfg is not None:
            qty = float(cfg["conversion"])
            pcs_per_ctn = int(cfg["sample_pcs"])
        elif cfg is not None:
            qty = 1.0
            pcs_per_ctn = int(cfg["normal_pcs"])
        else:
            # No sample config — read whatever the operator typed into
            # the now-editable CTN Qty / PCS fields. Parse defensively
            # so a half-typed value (e.g. "0.") doesn't crash collect().
            try:
                qty = float(self.ctn_edit.text().strip() or "0")
            except ValueError:
                qty = 0.0
            # Clamp to spec: 0 ≤ CTN ≤ 1
            qty = max(0.0, min(1.0, qty))
            try:
                pcs_per_ctn = int(self.pcs_per_ctn_edit.text().strip() or "0")
            except ValueError:
                pcs_per_ctn = 0
            if pcs_per_ctn < 0:
                pcs_per_ctn = 0

        # Read batch_date / batch_time straight from the form fields.
        # In reprint mode they hold the original carton's values; in
        # normal mode they hold the live clock values. This makes the
        # field the single source of truth.
        try:
            bd = datetime.strptime(self.batch_date.text().strip(), "%d/%m/%y")
        except (ValueError, AttributeError):
            bd = now
        bt = (self.batch_time.text() or now.strftime("%I:%M %p")).strip()

        brand_data = self.brand_cb.currentData() or {}
        inv_code = brand_data.get("inv_code") if isinstance(brand_data, dict) else None
        # 3-digit org_code ("064", "089", …) is what goes into PLANT_CODE
        # in both XXFG_CARTON_MASTER and XXFG_CARTON_BATCH_MASTER.
        org_code = brand_data.get("org_code") if isinstance(brand_data, dict) else None
        c = CartonLabel(
            lpn_id=self.lpn_edit.text(),
            carton_code=self.lpn_edit.text(),
            brand=self.brand_cb.currentText(),
            organization_id=str(inv_code) if inv_code is not None else "",
            plant_code=str(org_code) if org_code is not None else "",
            item_code=self.item_cb.currentText(),
            item_desc=item_data.get("desc", "") if isinstance(item_data, dict) else "",
            inventory_item_id="",   # XXFG_CARTON_MASTER.INVENTORY_ITEM_ID is NUMBER —
                                    # we don't have the Oracle internal ID; leave NULL
            lot_number=self.lot_edit.text(),
            sn=self.sn_edit.text().strip(),
            grade=self.grade_cb.currentText(),
            shift=self.shift_edit.text(),
            size_code=size,
            uom_code="CTN",      # always CTN — never shown but always sent
            pcs_type=pcs_type,
            carton_qty=qty,
            pcs_per_ctn=pcs_per_ctn,
            batch_date=bd,
            batch_time=bt,
            total_planned_qty=self._read_total_planned_qty(),
        )
        c.batch_no = c.build_batch_no()
        c.qr_code = c.build_qr_payload()
        return c

    def refresh_lpn(self):
        self.lpn_edit.setText(lpn_generator.peek_next())
        self._emit()

    def clear(self):
        self._suppress = True
        self.item_cb.setEditText("")
        self.size_edit.clear()
        self.pcs_type_cb.setCurrentIndex(0)   # "Regular"
        self.ctn_edit.setText("1.00")
        self.pcs_per_ctn_edit.clear()
        self._sample_cfg = None
        self.lot_edit.clear()
        self.sn_edit.clear()
        self.total_planned_qty_edit.clear()
        self.grade_cb.setCurrentIndex(0)
        # Exit reprint mode if we were in it — the live clock takes
        # back over the date/time/shift fields immediately.
        self._reprint_active = False
        self._last_emitted_minute = -1
        # shift_edit will be repainted by the next _tick_clock pass
        self._suppress = False
        self.refresh_lpn()

    def fill_from(self, row):
        """Populate from a history row for reprint.

        Freezes the live clock — date / time / shift are taken from the
        original carton so the reprinted label keeps the original
        batch_date (and therefore the original batch_no). The freeze is
        released when the operator hits Clear or after the reprint
        actually fires (main_window.exit_reprint_mode())."""
        self._suppress = True
        self._reprint_active = True
        self.lpn_edit.setText(row.lpn_id)
        idx = self.brand_cb.findText(row.brand)
        if idx >= 0:
            self.brand_cb.setCurrentIndex(idx)
            self._on_brand_changed(row.brand)
        self.item_cb.setEditText(row.item_code)
        self.size_edit.setText(row.size_code)
        self.pcs_type_cb.setCurrentIndex(0)   # default Normal on reprint
        self.lot_edit.setText(row.lot_number)
        # SN is not persisted — start blank on reprint so the operator
        # can type a fresh serial (or leave empty).
        self.sn_edit.clear()
        idx = self.grade_cb.findText(row.grade); self.grade_cb.setCurrentIndex(idx if idx >= 0 else 0)
        # Frozen-historical date / time / shift — these MUST match the
        # original carton so build_batch_no produces the original
        # batch_no (e.g. "12JUN26..." when reprinting a 12 JUN carton,
        # not today's compact batch number).
        if row.batch_date:
            self.batch_date.setText(row.batch_date.strftime("%d/%m/%y"))
        if row.batch_time:
            self.batch_time.setText(row.batch_time)
        if row.shift:
            self.shift_edit.setText(row.shift)
        self._suppress = False
        self._emit()

    def _emit(self):
        if self._suppress:
            return
        c = self.collect()
        self.batch_no_edit.setText(c.build_batch_no())
        self.qr_str_edit.setText(c.build_qr_payload())
        self._refresh_null_flags()
        self.data_changed.emit(c)

    # ---------- null-value indicators ----------
    # Every mandatory carton field shows a small red icon next to its
    # caption when empty / zero. The field itself stays its normal
    # colour — only the caption changes. The Jet Start button uses the
    # same rule (see main_window._missing_form_fields).
    _NULL_ICON_HTML = " <span style='color:#E23B3B; font-size:9pt;'>●</span>"

    def _flag_label(self, widget, is_null: bool):
        """Toggle the null icon in the field's caption above `widget`.

        Reads the BASE caption from `_field_caption` — never from
        `QLabel.text()` — so the previously-appended icon HTML doesn't
        get re-appended every emit. That repeating-dots bug was caused
        by reading the live (already-HTMLified) text and trying to
        strip the plain-text icon, which never matched."""
        if widget not in self._field_labels:
            return
        lbl = self._field_labels[widget]
        base = self._field_caption.get(widget, "")
        if is_null:
            lbl.setText(base + self._NULL_ICON_HTML)
            lbl.setStyleSheet("color: white; font-weight: 600; font-size: 10pt;")
            widget.setToolTip("Required — please fill this field before starting the jet.")
        else:
            lbl.setText(base)
            lbl.setStyleSheet("color: white; font-weight: 600; font-size: 10pt;")
            widget.setToolTip("")

    def _on_ctn_text_changed(self, text: str):
        """CTN Qty hard clamp.

        Range: 0.00 – 1.00 inclusive. Anything that parses to a value
        above 1.0 is immediately overwritten with '1', and anything
        below 0.0 with '0'. Partial inputs like '', '0.', '.5' are
        passed through so the operator can type fractional values.
        Re-emits at the end so downstream consumers see the clamped
        value, not the raw keystroke."""
        text = text.strip()
        # Allow partial inputs the operator might be mid-typing.
        if text in ("", ".", "0", "0.", "0.0", "1", "1.", "1.0", "1.00"):
            self._emit()
            return
        try:
            v = float(text)
        except ValueError:
            # Not a number yet (e.g. typed a stray '.') — leave alone.
            self._emit()
            return
        if v > 1.0 or v < 0.0:
            clamped = "1" if v > 1.0 else "0"
            # blockSignals so we don't re-enter this slot recursively.
            self.ctn_edit.blockSignals(True)
            self.ctn_edit.setText(clamped)
            # Caret to the end so the operator's next key goes after
            # the digit instead of mid-string.
            self.ctn_edit.setCursorPosition(len(clamped))
            self.ctn_edit.blockSignals(False)
        self._emit()

    def _is_empty(self, text: str) -> bool:
        return not (text or "").strip()

    def _refresh_null_flags(self):
        """Recompute every required field's null indicator. Cheap —
        just text/index reads. Called from _emit so the indicators stay
        live as the operator types."""
        self._ensure_label_registry()
        self._flag_label(self.brand_cb,        self._is_empty(self.brand_cb.currentText()))
        self._flag_label(self.item_cb,         self._is_empty(self.item_cb.currentText()))
        self._flag_label(self.size_edit,       self._is_empty(self.size_edit.text()))
        self._flag_label(self.grade_cb,        self._is_empty(self.grade_cb.currentText()))
        self._flag_label(self.shift_edit,      self._is_empty(self.shift_edit.text()))
        self._flag_label(self.lot_edit,        self._is_empty(self.lot_edit.text()))
        self._flag_label(self.pcs_type_cb,     self._is_empty(self.pcs_type_cb.currentText()))
        # CTN Qty — empty OR "0"/"0.00" counts as null.
        ctn_txt = self.ctn_edit.text().strip()
        try:
            ctn_val = float(ctn_txt) if ctn_txt else 0.0
        except ValueError:
            ctn_val = 0.0
        self._flag_label(self.ctn_edit, ctn_val <= 0.0)
        # PCS/CTN — same rule.
        pcs_txt = self.pcs_per_ctn_edit.text().strip()
        try:
            pcs_val = int(pcs_txt) if pcs_txt else 0
        except ValueError:
            pcs_val = 0
        self._flag_label(self.pcs_per_ctn_edit, pcs_val <= 0)
