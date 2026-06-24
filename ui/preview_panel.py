"""Carton-label preview window — just the QR + label-text artwork.

The dashboard owns printer status, faults, INK/SOL warnings, daily
counters, and the jet-control buttons; this popup is purely the
artwork the inkjet head will spray on the carton.

Opened from MainWindow via the Preview action tile.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QSizePolicy, QWidget,
)

from core import qr_builder
from core.carton_model import CartonLabel
from ui.theme import BG_LIGHT, TEXT_DARK


# =====================================================================
# Label artwork — QR + the QR payload string underneath, matching what
# the printer actually sprays on the carton.
# =====================================================================
class _LabelCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = CartonLabel()
        self._qr = None
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background: {BG_LIGHT}; border-radius: 8px;")

    def set_data(self, data: CartonLabel):
        self._data = data
        try:
            self._qr = qr_builder.build_qimage(data.build_qr_payload(), target_px=320)
        except Exception:
            self._qr = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin = 16

        # Sticker background
        p.fillRect(self.rect(), QColor(BG_LIGHT))
        pen = QPen(QColor("#9AA7C2")); pen.setWidth(1); p.setPen(pen)
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 10, 10)

        # QR centered, label text underneath
        label_text = self._data.build_qr_payload() if self._data.lpn_id else ""

        label_h = 34
        available_h = h - margin * 2 - label_h
        qr_size = max(120, min(available_h, w - margin * 2))
        qr_x = (w - qr_size) // 2
        qr_y = margin
        if self._qr is not None:
            pm = QPixmap.fromImage(self._qr).scaled(
                qr_size, qr_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            p.drawPixmap(qr_x, qr_y, pm)

        # QR payload string underneath
        p.setPen(QColor(TEXT_DARK))
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        text_rect_y = qr_y + qr_size + 6
        p.drawText(margin, text_rect_y, w - margin * 2, label_h - 8,
                   Qt.AlignHCenter | Qt.AlignVCenter, label_text)

        p.end()


# =====================================================================
# Preview window — minimal shell around the label canvas. Opened as a
# top-level window (Qt.Window flag is applied by MainWindow).
# =====================================================================
class PreviewPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        title = QLabel("Carton Label Preview")
        title.setObjectName("sectionTitle")
        outer.addWidget(title, 0)

        self.canvas = _LabelCanvas()
        outer.addWidget(self.canvas, 1)

    # ---------- public ----------
    def update_preview(self, data: CartonLabel):
        """Refresh the canvas. Called from MainWindow whenever the
        form's data_changed fires."""
        self.canvas.set_data(data)
