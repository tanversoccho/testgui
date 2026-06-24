"""Entry point — launches the Dikai Carton Label Printing GUI.

We deliberately do NOT touch the database or the printer during boot —
that way the window paints immediately even if the VPN is down or the
DB host is unreachable. Both are kicked off by MainWindow after the
window is shown, in background threads, so the operator sees a
responsive UI from the first frame.
"""
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from config import config_loader
from ui.main_window import MainWindow
from ui.theme import stylesheet


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Dikai Carton GUI")
    app.setStyleSheet(stylesheet())

    # Load saved settings — pure JSON read, instant.
    config_loader.load()

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
