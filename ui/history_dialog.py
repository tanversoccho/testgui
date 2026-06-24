"""Separate window: filter + reprint + delete. Mirrors the pallet app's pattern."""
from datetime import datetime, time
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFrame, QSizePolicy,
    QRadioButton, QButtonGroup,
)

from config import config_app
from core import database
from core.database import CartonRow, BatchRow
from ui.notifications import show_error, show_warning, show_info, confirm, toast
from ui.theme import fit_to_screen


PAGE_SIZE = 30

CARTON_COLUMNS = [
    "LPN ID",
    "Carton ID", "Item Code", "Lot Number",
    "CTN Qty", "PCS", "CTN Type",
    "Batch No", "Lot No",
    "Brand", "Grade", "Shift",
    "Batch Date", "Batch Time",
    "Status", "Size",
    "Created Date",
]
_CARTON_STATUS_COL = CARTON_COLUMNS.index("Status")

BATCH_COLUMNS = [
    "Batch ID", "Batch No", "Production Date",
    "Item Code", "Product Type", "Size", "UOM",
    "Batch Date", "Lot No",
    "Brand", "Grade", "Shift", "Status",
    "Production Qty", "Produced CTN Qty",
]


class HistoryDialog(QDialog):
    reprint_requested = Signal(object)   # CartonRow

    def __init__(self, parent=None, db_online: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Carton History · Filter · Reprint · Delete")
        self.setMinimumSize(900, 520)
        # When False, we KNOW the DB is offline (the main window's
        # background DB-check just reported failure). The dialog then
        # opens with an empty table and a clear "DB offline" banner —
        # no oracledb call, no hang, no native-client crash risk.
        # Mock-mode counts as "online" because the in-memory store
        # always responds.
        self._db_online: bool = bool(db_online) or bool(config_app.USE_MOCK_DB)
        self._page_by_mode = {"cartons": 1, "batches": 1}
        self._total_pages_by_mode = {"cartons": 0, "batches": 0}
        self._total_rows_by_mode = {"cartons": 0, "batches": 0}
        self._total_loaded_by_mode = {"cartons": False, "batches": False}
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(5)

        # ---- Filter strip ----
        filt = QFrame(); filt.setObjectName("panel")
        fl = QGridLayout(filt); fl.setContentsMargins(8, 6, 8, 6); fl.setHorizontalSpacing(6); fl.setVerticalSpacing(4)
        outer.addWidget(filt, 0)

        self.date_from = QDateEdit(QDate.currentDate().addDays(-30)); self.date_from.setCalendarPopup(True); self.date_from.setDisplayFormat("dd/MM/yy")
        self.date_to   = QDateEdit(QDate.currentDate()); self.date_to.setCalendarPopup(True); self.date_to.setDisplayFormat("dd/MM/yy")

        self.brand_cb = QComboBox(); self.brand_cb.addItem("All", "")
        for b in database.fetch_brands():
            self.brand_cb.addItem(b["brand"], b["brand"])

        self.item_edit  = QLineEdit(); self.item_edit.setPlaceholderText("contains…")
        self.lot_edit   = QLineEdit(); self.lot_edit.setPlaceholderText("contains…")

        # Mode-specific identifier field. In Cartons mode this row holds
        # "LPN" with an "LPN-C-…" placeholder; in Batches mode it holds
        # "Batch No" with a "13JUN26..." placeholder. We keep both
        # QLineEdits and a shared label, and swap them in/out as the
        # operator flips the View radio.
        self.lpn_edit      = QLineEdit(); self.lpn_edit.setPlaceholderText("LPN-C-…")
        self.batch_no_edit = QLineEdit(); self.batch_no_edit.setPlaceholderText("13JUN26... contains...")

        self.shift_cb = QComboBox(); self.shift_cb.addItem("All", "");  self.shift_cb.addItems(config_app.SHIFT_OPTIONS)
        self.grade_cb = QComboBox(); self.grade_cb.addItem("All", "");  self.grade_cb.addItems(config_app.GRADE_OPTIONS)
        # Status filter — repopulated per view mode by _apply_filter_mode().
        # Each item's userData carries (status_substring, include_deleted)
        # for Cartons mode and just the literal status string ('N' / 'Y'
        # / '') for Batches mode.
        self.status_cb = QComboBox()

        def add(label, widget, r, c, colspan=1):
            lbl = QLabel(label); lbl.setStyleSheet("color: white; font-weight: 600;")
            fl.addWidget(lbl, r, c)
            fl.addWidget(widget, r, c + 1, 1, colspan)

        add("From",   self.date_from,  0, 0)
        add("To",     self.date_to,    0, 2)
        add("Brand",  self.brand_cb,   0, 4)
        add("Status", self.status_cb,  0, 6)

        add("Item",   self.item_edit,  1, 0)
        add("Lot",    self.lot_edit,   1, 2)
        add("Shift",  self.shift_cb,   1, 4)
        add("Grade",  self.grade_cb,   1, 6)

        # Row 2 — mode-specific identifier field. Both widgets live in
        # the same grid cell; only one is visible at a time. The label
        # text is also driven by _apply_filter_mode().
        self.ident_label = QLabel("LPN")
        self.ident_label.setStyleSheet("color: white; font-weight: 600;")
        fl.addWidget(self.ident_label, 2, 0)
        fl.addWidget(self.lpn_edit,      2, 1, 1, 3)
        fl.addWidget(self.batch_no_edit, 2, 1, 1, 3)
        self.batch_no_edit.setVisible(False)   # default — Cartons mode

        # Buttons in filter strip
        btn_row = QHBoxLayout()
        self.search_btn = QPushButton("🔍  Search")
        self.search_btn.setObjectName("actionPrimary")
        self.reset_btn  = QPushButton("Reset")
        self.reset_btn.setObjectName("actionSecondary")
        btn_row.addStretch(1)
        btn_row.addWidget(self.reset_btn)
        btn_row.addWidget(self.search_btn)
        fl.addLayout(btn_row, 2, 4, 1, 4)

        # ---- View mode switcher (radio buttons) ----
        # "Cartons" → xxfg_carton_master (one row per printed carton).
        # "Batches" → grouped aggregation matching the
        #             xxfg_carton_batch_master INSERT spec.
        mode_row = QHBoxLayout(); mode_row.setSpacing(10); mode_row.setContentsMargins(0, 0, 0, 0)
        mode_lbl = QLabel("View:"); mode_lbl.setStyleSheet("color: white; font-weight: 700;")
        self.radio_cartons = QRadioButton("Cartons (per-LPN)")
        self.radio_batches = QRadioButton("Batches (grouped)")
        self.radio_cartons.setChecked(True)
        for r in (self.radio_cartons, self.radio_batches):
            r.setStyleSheet("color: white; font-weight: 600;")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.radio_cartons, 0)
        self._mode_group.addButton(self.radio_batches, 1)
        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self.radio_cartons)
        mode_row.addWidget(self.radio_batches)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        # ---- Table ----
        # Constructed empty; columns are populated by _apply_view_mode
        # below so the same QTableWidget can host either dataset.
        self.table = QTableWidget(0, 0)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        # ALWAYS show the horizontal scrollbar — the column widths sum
        # to more than the dialog, so AsNeeded sometimes failed to
        # appear (Qt's scroll-policy check races with table layout
        # when columns are populated dynamically). AlwaysOn means the
        # bar is reliably visible at the bottom of the table.
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        # Compact row height — the 21" 1024×600 panel needs ≥10 visible
        # rows in the limited vertical space the table gets after the
        # filter strip and footer. 20 px per row gives ≥12 rows even
        # when the dialog opens at 520 px tall.
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setMinimumSectionSize(24)
        self.table.horizontalHeader().setFixedHeight(30)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Floor the table's visible area so it always shows ≥10 rows
        # (10 × 20 px + 22 px header + 18 px scrollbar + 2 px borders).
        self.table.setMinimumHeight(260)
        outer.addWidget(self.table, 1)

        page_row = QHBoxLayout()
        page_row.setSpacing(6)
        self.prev_page_btn = QPushButton("<")
        self.next_page_btn = QPushButton(">")
        for btn in (self.prev_page_btn, self.next_page_btn):
            btn.setObjectName("actionSecondary")
            btn.setFixedWidth(34)
            btn.setToolTip("Previous page" if btn is self.prev_page_btn else "Next page")
        self.page_lbl = QLabel("0 of 0")
        self.page_lbl.setMinimumWidth(70)
        self.page_lbl.setAlignment(Qt.AlignCenter)
        self.page_lbl.setStyleSheet("color: white; font-weight: 700;")
        page_row.addStretch(1)
        page_row.addWidget(self.prev_page_btn)
        page_row.addWidget(self.page_lbl)
        page_row.addWidget(self.next_page_btn)
        outer.addLayout(page_row, 0)

        # Apply initial column setup AND initial filter setup (Cartons mode).
        self._apply_view_mode()
        self._apply_filter_mode()

        # Banner row (status/count)
        self.banner = QLabel("Ready.")
        self.banner.setObjectName("muted")
        outer.addWidget(self.banner, 0)

        # ---- Footer actions ----
        foot = QHBoxLayout(); foot.setSpacing(6)
        self.reprint_btn = QPushButton("🔄  Reprint Selected")
        self.reprint_btn.setObjectName("actionPrimary")
        self.delete_btn  = QPushButton("🗑  Delete Selected")
        self.delete_btn.setObjectName("actionSecondary")
        self.close_btn   = QPushButton("Close")
        self.close_btn.setObjectName("actionSecondary")
        foot.addWidget(self.reprint_btn, 1)
        foot.addWidget(self.delete_btn, 1)
        foot.addStretch(1)
        foot.addWidget(self.close_btn, 0)
        outer.addLayout(foot)

        # Wire
        self.search_btn.clicked.connect(self._search_first_page)
        self.reset_btn.clicked.connect(self._reset)
        self.prev_page_btn.clicked.connect(self._previous_page)
        self.next_page_btn.clicked.connect(self._next_page)
        self.reprint_btn.clicked.connect(self._on_reprint)
        self.delete_btn.clicked.connect(self._on_delete)
        self.close_btn.clicked.connect(self.accept)
        # Switching view mode: rebuild columns AND re-query.
        self.radio_cartons.toggled.connect(self._on_mode_changed)
        self.radio_batches.toggled.connect(self._on_mode_changed)

        # Cached rows for action handlers — type depends on current view.
        self._rows: List = []
        # Initial load
        self._search()

        # Fit to whatever screen we're on. The 1024×600 Daewoo target
        # asks for 1010×580; fit_to_screen caps at 92% of the available
        # area so larger monitors still get a roomy dialog.
        fit_to_screen(self, target_w=1010, target_h=580)

    # ---------- paging ----------
    def _current_page(self) -> int:
        return max(1, int(self._page_by_mode.get(self._view_mode(), 1)))

    def _set_current_page(self, page: int) -> None:
        self._page_by_mode[self._view_mode()] = max(1, int(page))

    def _invalidate_total(self) -> None:
        mode = self._view_mode()
        self._total_loaded_by_mode[mode] = False
        self._total_rows_by_mode[mode] = 0
        self._total_pages_by_mode[mode] = 0

    def _page_offset(self) -> int:
        return (self._current_page() - 1) * PAGE_SIZE

    def _set_page_totals(self, total_rows: int) -> None:
        mode = self._view_mode()
        total_rows = max(0, int(total_rows or 0))
        total_pages = (total_rows + PAGE_SIZE - 1) // PAGE_SIZE if total_rows else 0
        if total_pages and self._current_page() > total_pages:
            self._page_by_mode[mode] = total_pages
        self._total_rows_by_mode[mode] = total_rows
        self._total_pages_by_mode[mode] = total_pages
        self._total_loaded_by_mode[mode] = True
        self._update_page_controls()

    def _update_page_controls(self) -> None:
        mode = self._view_mode()
        total_pages = int(self._total_pages_by_mode.get(mode, 0) or 0)
        page = self._current_page() if total_pages else 0
        self.page_lbl.setText(f"{page} of {total_pages}")
        self.prev_page_btn.setEnabled((not self._loading) and total_pages > 0 and page > 1)
        self.next_page_btn.setEnabled((not self._loading) and total_pages > 0 and page < total_pages)

    def _set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        self.search_btn.setEnabled(not self._loading)
        self.reset_btn.setEnabled(not self._loading)
        self.radio_cartons.setEnabled(not self._loading)
        self.radio_batches.setEnabled(not self._loading)
        self._update_page_controls()

    def _search_first_page(self) -> None:
        if self._loading:
            return
        self._invalidate_total()
        self._set_current_page(1)
        self._search()

    def _previous_page(self) -> None:
        if self._loading:
            return
        if self._current_page() <= 1:
            return
        self._set_current_page(self._current_page() - 1)
        self._search()

    def _next_page(self) -> None:
        if self._loading:
            return
        total_pages = int(self._total_pages_by_mode.get(self._view_mode(), 0) or 0)
        if self._current_page() >= total_pages:
            return
        self._set_current_page(self._current_page() + 1)
        self._search()

    # ---------- view mode ----------
    def _view_mode(self) -> str:
        return "batches" if self.radio_batches.isChecked() else "cartons"

    def _apply_filter_mode(self):
        """Rebuild the parts of the filter strip that differ between
        Cartons and Batches views: the Status options, and the bottom
        identifier row (LPN for Cartons, Batch No for Batches)."""
        mode = self._view_mode()

        # Repopulate Status — but preserve the operator's current pick
        # if the new mode happens to have a compatible label.
        prev_text = self.status_cb.currentText()
        self.status_cb.blockSignals(True)
        self.status_cb.clear()
        if mode == "cartons":
            # CARTON_MASTER.STATUS values — PRINTED / PRINTED-RPn / DELETED.
            # userData = (status_substring, include_deleted) — consumed
            # by query_history via the status= and include_deleted=
            # kwargs.
            self.status_cb.addItem("All (active)",   userData=("",        False))
            self.status_cb.addItem("Printed only",   userData=("PRINTED", False))
            self.status_cb.addItem("Reprinted only", userData=("RP",      False))
            self.status_cb.addItem("Deleted only",   userData=("DELETED", True))
            self.status_cb.addItem("All + Deleted",  userData=("",        True))
        else:
            # BATCH_MASTER.STATUS values — 'N' (open) or 'Y' (finalised).
            # userData = exact status string ('' = no filter).
            self.status_cb.addItem("All",            userData="")
            self.status_cb.addItem("Open (N)",       userData="N")
            self.status_cb.addItem("Finished (Y)",   userData="Y")
        # Try to keep the operator's last pick if the label still exists.
        idx = self.status_cb.findText(prev_text)
        self.status_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self.status_cb.blockSignals(False)

        # Swap the bottom-row identifier widget + label.
        if mode == "cartons":
            self.ident_label.setText("LPN")
            self.lpn_edit.setVisible(True)
            self.batch_no_edit.setVisible(False)
        else:
            self.ident_label.setText("Batch No")
            self.lpn_edit.setVisible(False)
            self.batch_no_edit.setVisible(True)

    def _apply_view_mode(self):
        """Rebuild the table's columns + widths for the current mode.
        Called from __init__ and whenever the radio selection flips."""
        mode = self._view_mode()
        if mode == "cartons":
            cols = CARTON_COLUMNS
            # Widths tuned for the LattePanda's 10 pt table font. Wider
            # than before because the old 7 pt sizing made the text
            # uncomfortable to read on a 10" 1024×600 panel.
            widths = [
                190,                      # LPN ID (full LPN-C-YYMMDD…)
                84,                       # Carton ID
                120, 100,                 # Item Code, Lot Number
                76, 60, 80,               # CTN Qty, PCS, CTN Type
                160, 76,                  # Batch No, Lot No
                100, 60, 60,              # Brand, Grade, Shift
                90, 86,                   # Batch Date, Batch Time
                100, 76,                  # Status, Size
                120,                      # Created Date
            ]
        else:
            cols = BATCH_COLUMNS
            widths = [
                64,                       # Batch ID
                150, 100,                 # Batch No, Production Date
                120, 96, 76, 56,          # Item Code, Product Type, Size, UOM
                100, 84,                  # Batch Date, Lot No
                100, 60, 60, 66,          # Brand, Grade, Shift, Status
                110, 120,                 # Production Qty, Produced CTN Qty
            ]
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        for c, w in enumerate(widths):
            self.table.setColumnWidth(c, w)

    def _on_mode_changed(self, _checked: bool):
        # toggled fires for both old (False) and new (True) — only run once.
        if not _checked:
            return
        # Reprint / Delete only make sense in Cartons view. In Batches
        # view we HIDE them entirely per spec, not just disable, so the
        # operator can't even try to act on grouped/aggregated rows.
        is_cartons = (self._view_mode() == "cartons")
        self.reprint_btn.setVisible(is_cartons)
        self.delete_btn.setVisible(is_cartons)
        self._apply_view_mode()
        self._apply_filter_mode()
        self._search_first_page()

    # ---------- actions ----------
    def _search(self):
        if self._loading:
            return
        self._set_loading(True)
        try:
            self._search_impl()
        finally:
            self._set_loading(False)

    def _search_impl(self):
        # Hard-bypass when DB is known offline. Open with empty fields
        # and a clear banner — no oracledb call at all.
        if not self._db_online:
            self._rows = []
            self.table.setRowCount(0)
            self._set_page_totals(0)
            self.banner.setText(
                "Database offline — connect to Oracle in Settings to load history. "
                "Form data is still kept in memory while you work."
            )
            self.banner.setStyleSheet("color: #FFAA66;")
            return
        if self._view_mode() == "batches":
            self._search_batches()
            return
        try:
            dfrom = datetime.combine(self.date_from.date().toPython(), time.min)
            dto   = datetime.combine(self.date_to.date().toPython(),   time.max)
            # Status filter: userData is (status_substring, include_deleted)
            status_data = self.status_cb.currentData() or ("", False)
            status_sub, include_del = status_data
            filters = {
                "date_from": dfrom,
                "date_to": dto,
                "brand": self.brand_cb.currentData() or None,
                "item_code_like": self.item_edit.text().strip() or None,
                "lot_like": self.lot_edit.text().strip() or None,
                "shift": self.shift_cb.currentText() if self.shift_cb.currentIndex() else None,
                "grade": self.grade_cb.currentText() if self.grade_cb.currentIndex() else None,
                "status": status_sub or None,
                "include_deleted": include_del,
                "lpn_like": self.lpn_edit.text().strip() or None,
            }
            if self._total_loaded_by_mode["cartons"]:
                rows = database.query_history(
                    **filters,
                    limit=PAGE_SIZE,
                    offset=self._page_offset(),
                )
                total = self._total_rows_by_mode["cartons"]
            else:
                rows, total = database.query_history_page(
                    **filters,
                    limit=PAGE_SIZE,
                    offset=self._page_offset(),
                )
        except Exception as e:
            show_error(self, "History — search failed",
                       "Could not run the history query.", details=e)
            rows = []
            total = 0

        self._rows = rows
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for r in rows:
                self._append_row(r)
        finally:
            self.table.setUpdatesEnabled(True)
        self._set_page_totals(total)

        # query_history records its own error message internally; surface it
        err = database.last_query_error()
        if err and not rows:
            self.banner.setText(err)
            self.banner.setStyleSheet("color: #FFAA66;")
        else:
            self.banner.setText(f"{len(rows)} of {total} row(s) shown.")
            self.banner.setStyleSheet("")

    def _append_row(self, r: CartonRow):
        i = self.table.rowCount()
        self.table.insertRow(i)
        def _fmt_qty(v):
            if v is None: return ""
            return f"{v:.2f}" if abs(v - int(v)) > 1e-9 else str(int(v))
        cells = [
            r.lpn_id,                                             # LPN ID (CARTON_CODE)
            "" if r.carton_id is None else str(r.carton_id),     # Carton ID
            r.item_code,                                          # Item Code
            r.lot_number,                                         # Lot Number
            _fmt_qty(r.carton_qty),                               # CTN Qty
            "" if r.no_pcs is None else str(r.no_pcs),            # PCS
            r.ctn_type,                                           # CTN Type
            r.batch_no,                                           # Batch No
            r.lot_no,                                             # Lot No
            r.brand,                                              # Brand
            r.grade,                                              # Grade
            r.shift,                                              # Shift
            r.batch_date.strftime("%d/%m/%y") if r.batch_date else "",  # Batch Date
            r.batch_time,                                         # Batch Time
            r.status,                                             # Status
            r.size_code,                                          # Size
            r.creation_date.strftime("%d/%m/%y %H:%M") if r.creation_date else "",  # Created Date
        ]
        for c, val in enumerate(cells):
            item = QTableWidgetItem(str(val))
            if c == _CARTON_STATUS_COL:
                if "DELETED" in r.status:
                    item.setForeground(Qt.red)
                elif "RP" in r.status:
                    item.setForeground(Qt.darkYellow)
            self.table.setItem(i, c, item)

    # ---------- batch (grouped) view ----------
    def _search_batches(self):
        try:
            dfrom = datetime.combine(self.date_from.date().toPython(), time.min)
            dto   = datetime.combine(self.date_to.date().toPython(),   time.max)
            # In Batches mode the Status combobox's userData is just the
            # literal status string ('N' / 'Y' / '').
            status_pick = self.status_cb.currentData() or ""
            filters = {
                "date_from": dfrom,
                "date_to": dto,
                "brand": self.brand_cb.currentData() or None,
                "shift": self.shift_cb.currentText() if self.shift_cb.currentIndex() else None,
                "grade": self.grade_cb.currentText() if self.grade_cb.currentIndex() else None,
                "status": status_pick or None,
                "item_code_like": self.item_edit.text().strip() or None,
                "lot_like": self.lot_edit.text().strip() or None,
                "batch_no_like": self.batch_no_edit.text().strip() or None,
            }
            if self._total_loaded_by_mode["batches"]:
                rows = database.query_batches(
                    **filters,
                    limit=PAGE_SIZE,
                    offset=self._page_offset(),
                )
                total = self._total_rows_by_mode["batches"]
            else:
                rows, total = database.query_batches_page(
                    **filters,
                    limit=PAGE_SIZE,
                    offset=self._page_offset(),
                )
        except Exception as e:
            show_error(self, "History — batch query failed",
                       "Could not run the batch aggregation.", details=e)
            rows = []
            total = 0
        self._rows = rows
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for r in rows:
                self._append_batch_row(r)
        finally:
            self.table.setUpdatesEnabled(True)
        self._set_page_totals(total)

        err = database.last_query_error()
        if err and not rows:
            self.banner.setText(err)
            self.banner.setStyleSheet("color: #FFAA66;")
        else:
            self.banner.setText(f"{len(rows)} of {total} batch row(s) shown.")
            self.banner.setStyleSheet("")

    def _append_batch_row(self, r: BatchRow):
        i = self.table.rowCount()
        self.table.insertRow(i)
        def _fmt(v):
            if v is None: return ""
            return f"{v:.2f}" if abs(v - int(v)) > 1e-9 else str(int(v))
        cells = [
            "" if r.batch_id is None else str(r.batch_id),
            r.batch_no,
            r.production_date.strftime("%d/%m/%y") if r.production_date else "",
            r.item_code,
            r.product_type,
            r.size_code,
            r.uom_code,
            r.batch_date.strftime("%d/%m/%y") if r.batch_date else "",
            r.lot_no,
            r.brand,
            r.grade,
            r.shift,
            r.status,
            _fmt(r.production_qty),
            _fmt(r.produced_carton_qty),
        ]
        for c, val in enumerate(cells):
            self.table.setItem(i, c, QTableWidgetItem(str(val)))

    def _reset(self):
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_to.setDate(QDate.currentDate())
        self.brand_cb.setCurrentIndex(0)
        self.item_edit.clear()
        self.lot_edit.clear()
        self.lpn_edit.clear()
        self.batch_no_edit.clear()
        self.shift_cb.setCurrentIndex(0)
        self.grade_cb.setCurrentIndex(0)
        self.status_cb.setCurrentIndex(0)
        self._search_first_page()

    def _selected_row(self) -> Optional[CartonRow]:
        row_idx = self.table.currentRow()
        if row_idx < 0 or row_idx >= len(self._rows):
            QMessageBox.information(self, "Select a row", "Please select a row first.")
            return None
        return self._rows[row_idx]

    def _on_reprint(self):
        r = self._selected_row()
        if not r:
            return
        if "DELETED" in r.status:
            show_warning(self, "Cannot reprint",
                         f"{r.lpn_id} is marked DELETED — restore it before reprinting.")
            return
        self.reprint_requested.emit(r)
        self.accept()

    def _on_delete(self):
        r = self._selected_row()
        if not r:
            return
        if "DELETED" in r.status:
            show_info(self, "Already deleted",
                      f"{r.lpn_id} is already marked DELETED.")
            return
        if not confirm(
            self, "Confirm delete",
            f"Soft-delete {r.lpn_id}?\n\n"
            f"It will be marked STATUS='DELETED' in {config_app.CARTON_TABLE}.",
            default_no=True,
        ):
            return
        ok, msg = database.soft_delete(r.lpn_id)
        if ok:
            show_info(self, "Deleted", f"{r.lpn_id} marked DELETED.")
            self._search_first_page()
        else:
            show_error(self, "Delete failed",
                       f"Could not soft-delete {r.lpn_id}.",
                       details=msg)
