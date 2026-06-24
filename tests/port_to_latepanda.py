"""One-shot porter: copy a mainline source file into latepanda_app/ with
the mechanical rewrites the LattePanda build needs.

Usage:
    python tests/port_to_latepanda.py <relpath> [<relpath> ...]

Rewrites applied:
  * PySide6 -> PySide2 (every import form)
  * .exec()  -> .exec_()  (Qt 5 wheels alias this; safe everywhere
                          because none of the touched files call
                          non-Qt exec()).
  * Preserves UTF-8 BOM at start of file if the existing latepanda
    copy uses one (keeps editor-encoding behaviour consistent).

LattePanda-specific patches (open/close renames, theme colour bumps,
QTableWidget row sizing, brightened muted text, _form_lbl helper) are
NOT applied here — re-apply those by hand after running this script.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEPANDA = ROOT / "latepanda_app"

REWRITES = [
    (re.compile(r"\bPySide6\b"), "PySide2"),
    # .exec() -> .exec_() — applied unconditionally; the only .exec()
    # in the touched files is on QDialog / QApplication / QMessageBox.
    (re.compile(r"\.exec\(\)"), ".exec_()"),
]


def port(rel: str) -> None:
    src = ROOT / rel
    dst = LATEPANDA / rel
    if not src.exists():
        sys.exit(f"src missing: {src}")
    raw_bytes = src.read_bytes()

    # Detect mainline encoding (BOM or plain UTF-8)
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        text = raw_bytes.decode("utf-8-sig")
        src_had_bom = True
    else:
        text = raw_bytes.decode("utf-8")
        src_had_bom = False

    for pat, repl in REWRITES:
        text = pat.sub(repl, text)

    # Preserve BOM if the existing latepanda copy used one (LattePanda
    # files are checked in with BOM as convention).
    write_bom = False
    if dst.exists():
        prev = dst.read_bytes()
        if prev.startswith(b"\xef\xbb\xbf"):
            write_bom = True
    elif src_had_bom:
        write_bom = True

    dst.parent.mkdir(parents=True, exist_ok=True)
    payload = ("﻿" + text).encode("utf-8") if write_bom else text.encode("utf-8")
    dst.write_bytes(payload)
    print(f"PORTED  {rel}  ({'with' if write_bom else 'no'} BOM)")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: port_to_latepanda.py <relpath> [<relpath> ...]")
        return 2
    for rel in sys.argv[1:]:
        port(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
