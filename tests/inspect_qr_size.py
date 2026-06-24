"""Inspect the QR code that gets pushed to the printer.

Shows for each sample payload:
  * matrix dimension (NxN dots — this is what the 'L' Net Logo packet carries)
  * QR version (1=21x21, 2=25x25, 3=29x29, …)
  * border (quiet-zone) actually used after shrink-to-fit
  * total bits in the bitmap
  * the 'L' packet's data size in bytes
  * matrix dump (preview of what the printer sees)

Reflects the LIVE values from dikai_config.json — same QR_MAX_DOTS /
QR_BORDER / QR_ERROR_CORRECTION the printer is currently being told to use.

Run via:
    PYTHONPATH=. PYTHONIOENCODING=utf-8 \
        desktop_app\\.venv\\Scripts\\python.exe \
        tests\\inspect_qr_size.py
"""
from __future__ import annotations

import sys


# Approximate QR "version -> dot count" lookup so we can report which
# QR version each payload lands on. version v -> 17 + 4*v dots per side.
def _version_for(n: int) -> int:
    return (n - 17) // 4 if n >= 21 and (n - 17) % 4 == 0 else -1


def _print_matrix(bits: str, n: int) -> None:
    """ASCII-print the matrix so you can eyeball the actual QR."""
    for r in range(n):
        row = bits[r * n:(r + 1) * n]
        print("    " + row.replace("1", "##").replace("0", "  "))


def _dump(label: str, payload: str, max_dots: int, border: int, ecc: str) -> None:
    from core.qr_builder import build_printer_bitmap

    print(f"\n>>> {label}")
    print(f"    payload ({len(payload)} chars): {payload!r}")
    try:
        n, h, bits = build_printer_bitmap(
            payload, max_size=max_dots, border=border, error_correction=ecc
        )
    except ValueError as e:
        print(f"    FAIL  {e}")
        return

    version = _version_for(n)
    # Reconstruct the actual border used — build_printer_bitmap shrinks
    # the border down from the configured value until the QR fits.
    actual_border = None
    import qrcode
    from core.qr_builder import _resolve_ecc
    for b in range(max(border, 0), -1, -1):
        qr = qrcode.QRCode(
            version=None, error_correction=_resolve_ecc(ecc),
            box_size=1, border=b,
        )
        qr.add_data(payload or " ")
        qr.make(fit=True)
        if len(qr.get_matrix()) == n:
            actual_border = b
            break

    # The 'L' Net Logo packet wire size (per printer_link.build_net_logo_packet):
    #   header bytes: STX(1) + PKGID(1) + 'L'(1) + logo_id(1)
    #                 + width(4 ASCII) + height(2 ASCII)
    #   bits         : 1 ASCII char per dot
    #   trailer      : ETX(1)
    pkt_bytes = 1 + 1 + 1 + 1 + 4 + 2 + len(bits) + 1

    print(f"    matrix:        {n} x {h}  dots  (QR version {version})")
    print(f"    cap (config):  {max_dots} dots/side  (printhead ceiling is 34)")
    print(f"    border tried:  {border}, actually used: {actual_border}")
    print(f"    error corr.:   {ecc}")
    print(f"    bits in QR:    {len(bits)}")
    print(f"    'L' pkt bytes: {pkt_bytes}  (the bytes sent on the wire)")


def main() -> int:
    from config import config_loader, config_app
    config_loader.load()

    qr_max  = int(getattr(config_app, "QR_MAX_DOTS", 34))
    qr_brd  = int(getattr(config_app, "QR_BORDER", 2))
    qr_ecc  = str(getattr(config_app, "QR_ERROR_CORRECTION", "L"))
    msg_dots = int(getattr(config_app, "MSG_PRINTED_DOTS", 34))

    # This mirrors what core/printer_link.py push_label now uses.
    effective_cap = min(34, qr_max, msg_dots if msg_dots > 0 else 34)

    print("=" * 72)
    print("Live QR settings (from dikai_config.json):")
    print("=" * 72)
    print(f"  QR_MAX_DOTS           = {qr_max}")
    print(f"  MSG_PRINTED_DOTS      = {msg_dots}    (printer's vertical print area)")
    print(f"  QR_BORDER             = {qr_brd}")
    print(f"  QR_ERROR_CORRECTION   = {qr_ecc!r}")
    print(f"  Printer firmware cap  = 34 dots/side (DC81 printhead)")
    print(f"  -> effective cap used = {effective_cap} dots")
    if effective_cap == msg_dots and msg_dots < qr_max:
        print(f"     (capped by Printed Dots so the firmware doesn't squish the QR)")
    print()
    qr_max = effective_cap   # use the effective cap below so the dump matches what's actually sent

    # Build payloads exactly the way CartonLabel.build_qr_payload does:
    # f"{item_code}|{lot}|{shift}|{date_part} {batch_time}|{sn}"
    samples = [
        ("Typical short payload",
         "AAS60601|L-V|E|17 Jun 26 02:48 PM|42"),
        ("Empty fields (form just opened)",
         "||M|19 Jun 26 12:24 PM|"),
        ("Long item code + 4-digit SN",
         "MHDW5304HL2|L-12|N|19 Jun 26 02:48 PM|9999"),
        ("Long lot + long SN",
         "AAS60601|LOT-2025-XYZ|E|17 Jun 26 02:48 PM|123456"),
        ("Maximum-ish payload",
         "MHDW5304HL2|LOT-2025-XYZ|E|31 DEC 99 11:59 PM|999999"),
    ]
    for label, payload in samples:
        _dump(label, payload, qr_max, qr_brd, qr_ecc)

    # Show one matrix dump so the operator can see exactly what the
    # printer is being told to spray.
    print("\n" + "=" * 72)
    print("Matrix dump for the typical payload (## = printed dot):")
    print("=" * 72)
    from core.qr_builder import build_printer_bitmap
    n, _, bits = build_printer_bitmap(
        "AAS60601|L-V|E|17 Jun 26 02:48 PM|42",
        max_size=qr_max, border=qr_brd, error_correction=qr_ecc,
    )
    _print_matrix(bits, n)

    return 0


if __name__ == "__main__":
    sys.exit(main())
