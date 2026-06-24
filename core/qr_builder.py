"""QR code helpers — produce a QImage for the preview and a monochrome
bit-matrix for the Dikai 'Net Logo' upload command."""
from typing import List
import qrcode
from PIL import Image
from PySide6.QtGui import QImage

# Map the user-visible error correction names to the qrcode lib's enums.
_ECC_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}


def _resolve_ecc(name):
    """Convert a config string ('L'/'M'/'Q'/'H') to a qrcode constant."""
    return _ECC_MAP.get((name or "L").upper(), qrcode.constants.ERROR_CORRECT_L)


def build_qimage(payload: str, target_px: int = 220) -> QImage:
    """Render a QR code into a QImage suitable for QLabel display."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(payload or " ")
    qr.make(fit=True)
    pil_img: Image.Image = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    pil_img = pil_img.resize((target_px, target_px), Image.NEAREST)
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888).copy()
    return qimg


def build_bit_matrix(payload: str) -> List[List[int]]:
    """Square 0/1 matrix used when sending to the printer."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=0)
    qr.add_data(payload or " ")
    qr.make(fit=True)
    return [[1 if cell else 0 for cell in row] for row in qr.get_matrix()]


def build_printer_bitmap(payload: str, max_size: int = 34, border: int = 2,
                         error_correction: str = "L"):
    """Square monochrome QR sized for the inkjet print head.

    Returns (width, height, bits) where bits is a '0'/'1' string laid out
    row-major (length = width * height). Mirrors pep.qr_to_printer_bitmap.
    Raises ValueError if the QR cannot fit inside max_size dots.

    max_size  — printhead column ceiling (DC81 → 34)
    border    — quiet-zone padding tried inside max_size; we shrink it
                progressively if needed to make the QR fit
    error_correction — "L" / "M" / "Q" / "H". Higher = denser matrix.
    """
    ecc = _resolve_ecc(error_correction)
    last_n = None
    for b in range(max(border, 0), -1, -1):
        qr = qrcode.QRCode(
            version=None,
            error_correction=ecc,
            box_size=1,
            border=b,
        )
        qr.add_data(payload or " ")
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix); last_n = n
        if n <= max_size:
            bits = "".join("1" if c else "0" for row in matrix for c in row)
            return n, n, bits
    raise ValueError(
        f"QR for {len(payload)} chars needs {last_n}x{last_n} dots, exceeds {max_size}."
    )
