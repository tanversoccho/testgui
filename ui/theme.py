"""Dikai-inspired theme: deep blue background, orange accents, large readable widgets."""
from PySide6.QtWidgets import QApplication, QWidget


def fit_to_screen(widget: QWidget, target_w: int, target_h: int,
                  max_ratio: float = 0.92) -> None:
    """Resize the widget to (target_w, target_h) but never larger than
    `max_ratio` of the primary screen's available geometry. Then centre
    it on that screen.

    Lets every dialog declare its "natural" size while still fitting a
    10" 1280×800 panel — Qt picks whichever is smaller, and a QScrollArea
    inside the dialog will absorb the difference for content that's
    taller than the screen allows.
    """
    screen = QApplication.primaryScreen()
    avail = screen.availableGeometry() if screen is not None else None
    if avail is not None:
        max_w = int(avail.width()  * max_ratio)
        max_h = int(avail.height() * max_ratio)
    else:
        max_w, max_h = target_w, target_h

    w = min(target_w, max_w)
    h = min(target_h, max_h)
    widget.resize(w, h)
    widget.setMaximumSize(max_w, max_h)

    if avail is not None:
        x = avail.x() + (avail.width()  - w) // 2
        y = avail.y() + (avail.height() - h) // 2
        widget.move(x, y)


# Core palette (from DC81 panel photos)
BG_DEEP        = "#1B2A4E"   # main window background (deep navy)
BG_PANEL       = "#243B6B"   # raised panel
BG_PANEL_SOFT  = "#2C4680"   # softer fill
BG_LIGHT       = "#F4F6FB"   # form input fill / label preview
TEXT_LIGHT     = "#FFFFFF"
TEXT_MUTED     = "#B6C2DC"
TEXT_DARK      = "#1A2238"

ORANGE         = "#F37021"   # Dikai signature orange
ORANGE_HOVER   = "#FF8740"
ORANGE_PRESSED = "#D85F15"
BLUE_TILE      = "#2F8BD6"   # INK / SOL blue tiles
BLUE_HOVER     = "#4AA2EC"
RED            = "#E23B3B"   # Jet Stop / fault
RED_HOVER      = "#FF5A5A"
GREEN          = "#1FB36B"   # connected / OK
AMBER          = "#F5B53A"   # warning

BORDER         = "#3D5895"


def stylesheet() -> str:
    """Global QSS. All sizes use point/em-ish values so it scales with DPI."""
    return f"""
    /* ===== Window ===== */
    QMainWindow, QDialog {{
        background: {BG_DEEP};
        color: {TEXT_LIGHT};
    }}
    QWidget {{
        color: {TEXT_LIGHT};
        font-family: "Segoe UI", "Inter", Arial, sans-serif;
        font-size: 9pt;
    }}

    /* ===== Panels ===== */
    QFrame#panel {{
        background: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QFrame#topbar {{
        background: {BG_PANEL};
        border-bottom: 2px solid {ORANGE};
    }}
    QFrame#statusbar {{
        background: {BG_PANEL};
        border-top: 1px solid {BORDER};
    }}
    QLabel#brand {{
        color: {ORANGE};
        font-size: 13pt;
        font-weight: 800;
        letter-spacing: 1px;
    }}
    QLabel#brandSub {{
        color: {TEXT_MUTED};
        font-size: 7pt;
        letter-spacing: 4px;
    }}
    QLabel#sectionTitle {{
        color: {ORANGE};
        font-size: 10pt;
        font-weight: 700;
        padding: 2px 0 3px 2px;
    }}
    QLabel#h2 {{
        color: {TEXT_LIGHT};
        font-size: 10pt;
        font-weight: 700;
    }}
    QLabel#muted {{
        color: {TEXT_MUTED};
        font-size: 8pt;
    }}
    QLabel#counter {{
        color: {ORANGE};
        font-size: 14pt;
        font-weight: 800;
        letter-spacing: 1px;
    }}
    QLabel#counterCaption {{
        color: {TEXT_MUTED};
        font-size: 8pt;
        letter-spacing: 2px;
    }}

    /* ===== Form inputs ===== */
    QLabel.formLabel {{
        color: {TEXT_LIGHT};
        font-weight: 600;
        font-size: 9pt;
    }}
    QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
        background: {BG_LIGHT};
        color: {TEXT_DARK};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 3px 6px;
        min-height: 18px;
        selection-background-color: {ORANGE};
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus {{
        border: 2px solid {ORANGE};
    }}
    QLineEdit:read-only {{
        background: #DDE4F2;
        color: #45567A;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 28px;
    }}
    QComboBox QAbstractItemView {{
        background: {BG_LIGHT};
        color: {TEXT_DARK};
        selection-background-color: {ORANGE};
        selection-color: white;
        border: 1px solid {BORDER};
    }}

    /* ===== Big action buttons (PRINT / CLEAR / HISTORY) ===== */
    QPushButton#actionPrimary {{
        background: {ORANGE};
        color: white;
        border: none;
        border-radius: 8px;
        font-size: 10pt;
        font-weight: 800;
        padding: 6px 10px;
        min-height: 28px;
        letter-spacing: 1px;
    }}
    QPushButton#actionPrimary:hover {{ background: {ORANGE_HOVER}; }}
    QPushButton#actionPrimary:pressed {{ background: {ORANGE_PRESSED}; }}
    QPushButton#actionPrimary:disabled {{ background: #6B6055; color: #C7BFB7; }}

    QPushButton#actionSecondary {{
        background: {BG_PANEL_SOFT};
        color: white;
        border: 1px solid {BORDER};
        border-radius: 8px;
        font-size: 9pt;
        font-weight: 700;
        padding: 6px 10px;
        min-height: 28px;
        letter-spacing: 1px;
    }}
    QPushButton#actionSecondary:hover {{ background: #36569B; }}
    QPushButton#actionSecondary:pressed {{ background: #1F3877; }}
    QPushButton#actionSecondary:disabled {{ background: #2C3957; color: #6B7A99; }}

    QPushButton#connectBtn {{
        background: {ORANGE};
        color: white;
        border: none;
        border-radius: 6px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 9pt;
    }}
    QPushButton#connectBtn:hover {{ background: {ORANGE_HOVER}; }}
    QPushButton#connectBtn:pressed {{ background: {ORANGE_PRESSED}; }}

    /* ===== Dikai status tile buttons ===== */
    QPushButton#clearBtn {{
        background: {BG_PANEL_SOFT};
        color: white;
        border: 1px solid {BORDER};
        border-radius: 6px;
        font-size: 9pt;
        font-weight: 700;
        padding: 3px 10px;
    }}
    QPushButton#clearBtn:hover {{ background: #36569B; }}

    QPushButton.tileBlue {{
        background: {BLUE_TILE};
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        font-size: 9pt;
        padding: 4px 8px;
        min-width: 60px;
        min-height: 40px;
        letter-spacing: 1px;
    }}
    QPushButton.tileBlue:hover {{ background: {BLUE_HOVER}; }}
    QPushButton.tileBlueOff {{
        background: #44546F;
        color: #A8B5CC;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        font-size: 10pt;
        padding: 4px 10px;
        min-width: 60px;
        min-height: 34px;
        letter-spacing: 1px;
    }}
    QPushButton.tileOrange {{
        background: {ORANGE};
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        font-size: 9pt;
        padding: 4px 8px;
        min-height: 40px;
        letter-spacing: 1px;
    }}
    QPushButton.tileOrange:hover {{ background: {ORANGE_HOVER}; }}
    QPushButton.tileOrange:disabled {{ background: #44546F; color: #A8B5CC; }}
    QPushButton.tileGreen:disabled {{ background: #44546F; color: #A8B5CC; }}
    QPushButton.tileRed:disabled {{ background: #44546F; color: #A8B5CC; }}
    QPushButton.tileBlue:disabled {{ background: #44546F; color: #A8B5CC; }}
    QPushButton.tileOrangeOff {{
        background: #44546F;
        color: #A8B5CC;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        padding: 4px 8px;
        min-height: 40px;
        font-size: 9pt;
        letter-spacing: 1px;
    }}
    QPushButton.tileRed {{
        background: {RED};
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        font-size: 9pt;
        padding: 4px 8px;
        min-height: 40px;
        letter-spacing: 1px;
    }}
    QPushButton.tileRed:hover {{ background: {RED_HOVER}; }}
    QPushButton.tileGreen {{
        background: {GREEN};
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 800;
        font-size: 9pt;
        padding: 4px 8px;
        min-height: 40px;
        letter-spacing: 1px;
    }}

    /* ===== Status pills / chips (Passed / Hold / Rejected) ===== */
    QPushButton#pillPassed, QPushButton#pillHold, QPushButton#pillRejected {{
        border-radius: 8px;
        font-weight: 700;
        font-size: 9pt;
        padding: 6px 12px;
        min-height: 26px;
    }}
    QPushButton#pillPassed {{ background: {BG_PANEL_SOFT}; color: white; border: 2px solid {BORDER}; }}
    QPushButton#pillPassed:checked {{ background: {GREEN}; color: white; border: 2px solid {GREEN}; }}
    QPushButton#pillHold {{ background: {BG_PANEL_SOFT}; color: white; border: 2px solid {BORDER}; }}
    QPushButton#pillHold:checked {{ background: {AMBER}; color: {TEXT_DARK}; border: 2px solid {AMBER}; }}
    QPushButton#pillRejected {{ background: {BG_PANEL_SOFT}; color: white; border: 2px solid {BORDER}; }}
    QPushButton#pillRejected:checked {{ background: {RED}; color: white; border: 2px solid {RED}; }}

    /* ===== Splitter ===== */
    QSplitter::handle {{ background: {BORDER}; width: 2px; }}

    /* ===== Scroll area ===== */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{
        background: {BG_PANEL};
        width: 10px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    /* ===== Tables ===== */
    QTableWidget {{
        background: {BG_LIGHT};
        color: {TEXT_DARK};
        gridline-color: #C7CFE0;
        selection-background-color: {ORANGE};
        selection-color: white;
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QHeaderView::section {{
        background: {BG_PANEL_SOFT};
        color: white;
        padding: 5px 8px;
        border: none;
        font-weight: 700;
        font-size: 10pt;
    }}
    QTableWidget {{
        font-size: 10pt;
    }}
    QTableWidget::item {{
        padding: 3px 6px;
    }}

    /* ===== Tooltip ===== */
    QToolTip {{
        background: {BG_PANEL};
        color: white;
        border: 1px solid {ORANGE};
        padding: 4px 6px;
    }}
    """
