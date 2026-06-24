"""Carton label data model — maps 1:1 to APPS.XXFG_CARTON_MASTER columns."""
from dataclasses import dataclass, field
from datetime import datetime, time as _time
from typing import Optional


def shift_for_time(t: _time) -> str:
    """Return the shift letter for a given time-of-day.

    Boundaries (inclusive):
       06:01–14:00  → 'M'  (Morning)
       14:01–22:00  → 'E'  (Evening)
       22:01–06:00  → 'N'  (Night)
    """
    minutes = t.hour * 60 + t.minute
    if 361 <= minutes <= 840:        # 06:01 – 14:00
        return "M"
    if 841 <= minutes <= 1320:       # 14:01 – 22:00
        return "E"
    return "N"                        # 22:01 – 06:00 (wraps)


def current_shift() -> str:
    """Convenience — shift letter for *now*."""
    return shift_for_time(datetime.now().time())


@dataclass
class CartonLabel:
    # Auto / hidden
    carton_code: str = ""
    qr_code: str = ""
    organization_id: str = ""
    inventory_item_id: str = ""
    lpn_id: str = ""
    lpn_context: str = "CARTON"
    plant_code: str = ""           # 3-digit org code (e.g. "064", "089") — DB PLANT_CODE

    # User-driven
    brand: str = ""
    item_code: str = ""
    item_desc: str = ""
    lot_number: str = ""
    sn: str = ""                  # manual serial — printed on carton, NOT stored in DB
    grade: str = ""
    shift: str = ""
    size_code: str = ""           # tile size from XXFG_ORG_ITEMS.CAT5, e.g. "60X60"
    uom_code: str = "CTN"         # always CTN — sent in background
    # Sample-carton bookkeeping (from XXFG_SAMPLE_CARTON_CONFIG)
    pcs_type: str = "Normal"      # "Normal" or "Sample"
    carton_qty: float = 1.0       # CTN fraction: 1.0 for Normal, CONVERSION_CTN for Sample
    pcs_per_ctn: int = 0          # NORMAL_PCS_CTN or SAMPLE_PCS_CTN
    status: str = "PRINTED"

    # Batch / time
    batch_no: str = ""
    # TOTAL_PLANNED_QTY (XXFG_CARTON_MASTER) — operator's planned batch
    # quantity. Replaces the old batch_status field on the form.
    # batch_master_master rows are now created by a DB trigger off the
    # carton_master insert, so the form no longer needs N/Y plumbing.
    total_planned_qty: float = 0.0
    batch_date: Optional[datetime] = None
    batch_time: str = ""

    # Audit
    created_by: str = "DIKAI_GUI"
    creation_date: Optional[datetime] = None

    # ------------- helpers -------------
    def build_batch_no(self) -> str:
        """BATCH_NO = BATCH_DATE + ITEM_CODE + SHIFT, e.g. 24JUN26AGVT157M."""
        date_part = (self.batch_date or datetime.now()).strftime("%d%b%y").upper()
        return f"{date_part}{self.item_code or '?'}{self.shift or '?'}"

    def build_qr_payload(self) -> str:
        """QR contents: ITEM_CODE|LOT|SHIFT|DATE TIME|SN

        SN is the operator's manual serial — printed on the carton but
        never stored in the database (intentional: it's a free-text
        traceability mark, not a persisted identity)."""
        dt = (self.batch_date or datetime.now()).strftime("%d %b %y")
        return f"{self.item_code}|{self.lot_number}|{self.shift}|{dt} {self.batch_time}|{self.sn}"

    def _lpn_number(self) -> Optional[int]:
        """Extract the numeric tail from 'LPN-C-00000001' for the
        XXFG_CARTON_MASTER.LPN_ID NUMBER column."""
        if not self.lpn_id:
            return None
        import re
        m = re.search(r"(\d+)$", self.lpn_id)
        return int(m.group(1)) if m else None

    def as_db_row(self) -> dict:
        """Dict keyed by XXFG_CARTON_MASTER column names — ready for
        insert_carton(). Column types verified against the live schema:
            LPN_ID, CARTON_ID                    → NUMBER
            ORGANIZATION_ID, INVENTORY_ITEM_ID   → VARCHAR2 (not NUMBER!)
            NO_PCS                               → NUMBER (pcs/ctn count)
            CTN_TYPE                             → VARCHAR2 ("Regular" / "Sample")
        """
        return {
            "CARTON_CODE":       self.carton_code,
            "QR_CODE":           self.qr_code or self.build_qr_payload(),
            "ORGANIZATION_ID":   self.organization_id or None,
            "PLANT_CODE":        self.plant_code or None,
            "INVENTORY_ITEM_ID": self.inventory_item_id or None,
            "ITEM_CODE":         self.item_code or None,
            "ITEM_DESC":         self.item_desc or None,
            "LOT_NUMBER":        self.lot_number or None,
            "CARTON_QTY":        self.carton_qty,
            "UOM_CODE":          self.uom_code,           # always "CTN"
            "LPN_ID":            self._lpn_number(),
            "LPN_CONTEXT":       self.lpn_context,
            "BATCH_NO":          self.batch_no or self.build_batch_no(),
            "BATCH_DATE":        self.batch_date or datetime.now(),
            "LOT_NO":            self.lot_number or None,
            "BRAND":             self.brand or None,
            "GRADE":             self.grade or None,
            "SHIFT":             self.shift or None,
            "BATCH_TIME":        self.batch_time or None,
            "STATUS":            self.status,
            "SIZE_CODE":         self.size_code or None,
            "GRADE_CODE":        self.grade or None,
            # Sample / carton config — pcs_per_ctn → NO_PCS, pcs_type → CTN_TYPE
            "NO_PCS":            self.pcs_per_ctn or None,
            "CTN_TYPE":          self.pcs_type or None,
            # Operator's planned total quantity for the batch.
            "TOTAL_PLANNED_QTY": self.total_planned_qty or None,
            "CREATED_BY":        self.created_by,
            "CREATION_DATE":     self.creation_date or datetime.now(),
        }
