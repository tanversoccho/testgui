"""Audit the requirement list (items 1, 2, 3, 4, 7, 8, 10, 11, 12, 13)
against the current code. No code changes — pure inspection."""
import re
from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8-sig")


form  = read("ui/form_panel.py")
mwin  = read("ui/main_window.py")
plink = read("core/printer_link.py")
cm    = read("core/carton_model.py")
db    = read("core/database.py")


def check(label, ok):
    mark = "✓" if ok else "✗"
    print(f"   [{mark}] {label}")


print("1. Shift number from clock, not editable")
check("shift_edit.setReadOnly(True) is set",
      "shift_edit.setReadOnly(True)" in form)
check("current_shift() drives shift_edit",
      "shift_edit.setText(current_shift" in form)

print()
print("2. Regular pcs from system table; Sample from sample table")
check("fetch_normal_pcs_per_ctn() exists",
      "def fetch_normal_pcs_per_ctn" in db)
check("XXFG_UOM_CONVERSIONS_V queried (system primary/secondary)",
      "XXFG_UOM_CONVERSIONS_V" in db)
check("fetch_sample_config() exists",
      "def fetch_sample_config" in db)
check("XXFG_SAMPLE_CARTON_CONFIG queried",
      "XXFG_SAMPLE_CARTON_CONFIG" in db)
check("form caches _uom_normal_pcs",
      "_uom_normal_pcs" in form)

print()
print("3. Batch No auto from (date, item, shift); batch_master = DB trigger")
check("build_batch_no() exists in CartonLabel",
      "def build_batch_no" in cm)
check("DB trigger comment present in database.py",
      "DB trigger" in db or "trigger" in db.lower())
check("All batch_master WRITE helpers removed",
      "def batch_master_insert_initial" not in db
      and "def batch_master_finalize" not in db
      and "def batch_master_is_finalized" not in db
      and "def batch_master_has_row" not in db)
check("batch_no field is read-only (auto-built)",
      "batch_no_edit.setReadOnly(True)" in form)

print()
print("4. QR visual not on dashboard — only via Preview button")
check("PreviewPanel created hidden (top-level window)",
      "self.preview.hide()" in mwin)
check("preview_btn wired to _open_preview",
      "preview_btn.clicked.connect(self._open_preview)" in mwin)
check("QSplitter (old form|preview split) removed",
      "QSplitter" not in mwin)

print()
print("7. SN field numeric only (1, 2, 3...)")
check("sn_edit has QIntValidator",
      "sn_edit.setValidator(QIntValidator" in form)
check("placeholder is '1, 2, 3...'",
      "1, 2, 3" in form)
check("no auto-uppercase wired on sn_edit",
      "_wire_uppercase(self.sn_edit)" not in form)

print()
print("8. DB + Printer status are click-to-toggle buttons")
check("db_status_pill is QPushButton",
      "db_status_pill = QPushButton" in mwin)
check("conn_chip is QPushButton",
      "conn_chip = QPushButton" in mwin)
check("_on_db_pill_clicked handler exists",
      "def _on_db_pill_clicked" in mwin)
check("_on_printer_pill_clicked handler exists",
      "def _on_printer_pill_clicked" in mwin)
check("DB pill: tap=reset_connections when online",
      "reset_connections" in mwin)

print()
print("10. Print Start / Stop buttons on the dashboard")
have_print_start = any(s in mwin for s in (
    "print_start_btn", "print_enable_btn", "print_on_btn",
    "set_print_enabled", "Print Start", "Print Stop", "Print Enable",
))
have_print_method_in_link = "def set_print_enabled" in plink
check("Dashboard has a Print Start / Stop tile",
      have_print_start)
check("PrinterLink.set_print_enabled() exists (the underlying call)",
      have_print_method_in_link)

print()
print("11. LPN not in QR payload printed on carton")
m = re.search(
    r"def build_qr_payload[^\n]*\n\s+\"\"\"[^\"]*\"\"\"\s*.*?return\s+f\"([^\"]+)\"",
    cm, re.S,
)
fmt = m.group(1) if m else ""
print(f"   QR payload format: {fmt!r}")
check("'lpn_id' NOT in QR payload format string", "lpn_id" not in fmt)
check("'item_code' IS in QR payload",            "item_code" in fmt)

print()
print("12. PCS/CTN label (no Normal/Sample prefix)")
check("Row 3 caption set to 'PCS/CTN'",
      '"PCS/CTN"' in form)
check("_apply_pcs_type_view sets caption to PCS/CTN",
      'new_caption = "PCS/CTN"' in form)
check("Old 'Normal pcs/ctn' / 'Sample pcs/ctn' captions gone",
      "Normal pcs/ctn" not in form and "Sample pcs/ctn" not in form)

print()
print("13. Sample-mode PCS typed → CTN auto-recompute from UOM rate")
check("_on_pcs_per_ctn_text_edited slot exists",
      "def _on_pcs_per_ctn_text_edited" in form)
check("Uses uom_normal_pcs as the divisor",
      "self._uom_normal_pcs" in form
      and "entered_pcs / float" in form)
check("Gated on Sample mode",
      'pcs_type_cb.currentText() != "Sample"' in form)
check("textEdited (not textChanged) wires recompute",
      "textEdited.connect(self._on_pcs_per_ctn_text_edited" in form)
