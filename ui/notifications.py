"""Consistent user-facing messages — keep error / warning / info handling
out of every dialog body so the wording stays uniform.

Patterns:
  show_error(parent, "Print failed", "DB insert was rejected", details=str(e))
  show_warning(parent, "Skipped", "Row already deleted.")
  show_info(parent, "Saved", "Connection settings updated.")
  toast(parent, "Pushed to printer", level="info")

`toast()` is non-blocking — overlays a colored pill near the top of the
window for a few seconds. Use it for high-frequency events (push fired,
ink low) where a modal dialog would be disruptive.
"""
from __future__ import annotations
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QLabel, QWidget

from ui.theme import GREEN, RED, AMBER


# ---------- Modal dialogs ----------
def show_error(parent: QWidget | None, title: str, message: str,
               details: str | object = "") -> None:
    """Critical error — modal, blocks until acknowledged."""
    box = QMessageBox(QMessageBox.Critical, title, message, QMessageBox.Ok, parent)
    if details:
        box.setDetailedText(str(details))
    box.exec()


def show_warning(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def confirm(parent: QWidget | None, title: str, message: str,
            default_no: bool = True) -> bool:
    """Yes/No confirmation. Returns True only on explicit Yes."""
    default = QMessageBox.No if default_no else QMessageBox.Yes
    ans = QMessageBox.question(
        parent, title, message,
        QMessageBox.Yes | QMessageBox.No, default,
    )
    return ans == QMessageBox.Yes


# ---------- Non-blocking toast ----------
def toast(parent: QWidget, message: str, level: str = "info",
          duration_ms: int = 3000) -> None:
    """Overlay a non-modal coloured pill near the top of the parent.

    Use level = 'info' | 'warn' | 'error' to pick a colour.
    """
    color = {"info": GREEN, "warn": AMBER, "error": RED}.get(level, AMBER)
    lbl = QLabel(message, parent)
    lbl.setStyleSheet(
        f"QLabel {{ background: #1A2747; color: {color}; "
        f"padding: 8px 18px; border: 1px solid {color}; "
        f"border-radius: 10px; font-weight: 800; font-size: 11pt; }}"
    )
    lbl.setAlignment(Qt.AlignCenter)
    lbl.adjustSize()

    # Place near the top centre of the parent
    pw = parent.width()
    lbl.move(max(10, (pw - lbl.width()) // 2), 72)
    lbl.show()
    lbl.raise_()

    QTimer.singleShot(duration_ms, lbl.deleteLater)
