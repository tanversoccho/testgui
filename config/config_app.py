"""Industrial-ready app settings.

Persistent overrides live in dikai_config.json next to the project.
"""

APP_NAME = "Dikai Carton Label Printing"
APP_VERSION = "1.0.0"

# ---------- Printer (Dikai DC81) ----------
PRINTER_IP = "192.168.1.110"
PRINTER_PORT = 4916
PRINTER_PROTO = "UDP"            # "TCP" or "UDP" — operator's printer uses UDP
PRINTER_TIMEOUT = 2.0            # seconds
PRINTER_PKGID = 254              # current packet sequence id (LT protocol)
PRINTER_AUTO_INCREMENT_PKGID = True

# Developer-only flag — when False (production default) the app behaves
# strictly: if the TCP connection to the printer fails, the app stays
# offline and every read/write/button is disabled until the printer
# comes online. When True, a simulator stands in so the GUI can be
# exercised without hardware. NOT exposed in the UI.
SIMULATOR_MODE = False

# Simulated jet stage durations (real printer reports actual states)
JET_HEATING_SECONDS = 6.0    # heater warm-up
JET_PURGING_SECONDS = 3.0    # nozzle clear

# How often we poll printer status / counter
POLL_INTERVAL_SECONDS = 1.0

# ---------- Printer system parameters (DC81 'E' command) ----------
# Operator-tunable runtime settings. Ranges per the DC81 panel.
PRINT_ENABLE      = True               # Print Enable / Disable
MODULATION_SET    = 25                 # 5–99
VISCOSITY_SET     = 285                # 150–600
INK_PRESSURE      = 280                # 100–500
NOZZLE_TEMP       = 40                 # 10–60
CHARGE_VALUE      = 155                # 100–200

# ---------- Print Sizing (QR + 'P' Modify Message Params) ----------
# QR — passed to qr_builder.build_printer_bitmap on every push_label.
# Lowering QR_MAX_DOTS shrinks the QR; raising QR_ERROR_CORRECTION
# makes the matrix denser (more robust scans, larger print).
QR_MAX_DOTS         = 30       # 1–34. 30 leaves 1-dot margin inside
                               #   MSG_PRINTED_DOTS=30 so the printer
                               #   never has to scale the QR. push_label
                               #   takes min(QR_MAX_DOTS, MSG_PRINTED_DOTS)
                               #   so this is belt + suspenders.
QR_BORDER           = 2        # 0–4 quiet-zone padding inside max_dots
QR_ERROR_CORRECTION = "L"      # "L" / "M" / "Q" / "H"
                               # L = best scannability per DC800 manual:
                               #   "reducing the accuracy level of QR code
                               #    can improve the recognition rate".

# Message Params — sent via the 'P' packet. Scales the active message
# on the printer (QR + text together — there is no per-field scale in
# the protocol). Ranges per the DC800-series spec.
#
# These defaults match the proven pep/gui_2.py reference build for the
# same DC81 hardware family — pep's QRs print square at MSG_WIDTH=999;
# our earlier MSG_WIDTH=100 was compressing the QR horizontally and
# making it unscannable.
MSG_REVERSE         = False        # mirror left↔right
MSG_INVERT          = False        # flip upside-down
MSG_WIDTH           = 999          # Character Width 0–1000.
                                   #   Max horizontal pitch — keeps the
                                   #   QR aspect square. Matches pep.
MSG_DELAY           = 100          # Print Delay 0–10000 (sensor → spray
                                   #   delay). Reasonable starting point.
MSG_HEIGHT          = 1            # Character Height 0–10. KEEP AT 1 —
                                   #   any higher scales the QR vertically
                                   #   past MSG_PRINTED_DOTS and truncates.
MSG_PRINTED_DOTS    = 30           # Printed Dots 1–34 (vertical print area).
                                   #   30 fits our 29-dot QR + 1 margin,
                                   #   and is taller than 16x11 / 19x14
                                   #   text fonts so both QR and text fit.
MSG_TRIG_TIMES      = 1            # Prints / Trigger 1–99 (one print per
                                   #   sensor trigger).
MSG_GAP             = 0            # Multi-Print Gap 0–10000 (only matters
                                   #   when MSG_TRIG_TIMES > 1).
MSG_COL_REPEATS     = 1            # Column Repeats 0–10. 1 = single fire
                                   #   per column. >1 over-prints columns
                                   #   and smudges the QR.
MSG_CHAR_SPACE      = 1            # Character Space 0–9 (gap between glyphs).

# ---------- Oracle DB ----------
DB_HOST = ""
DB_PORT = 1521
DB_SERVICE = ""
DB_USER = ""
DB_PASSWORD = ""

# Oracle Instant Client (thick mode). When USE_THICK_MODE is True the
# database layer calls oracledb.init_oracle_client(lib_dir=...) once at
# startup. If the path here is empty, the app auto-detects a bundled
# instantclient_* folder next to main.py.
USE_THICK_MODE = True
ORACLE_INSTANT_CLIENT_PATH = ""    # auto-detected if empty
TNS_ADMIN_PATH = ""                # auto-detected if empty (uses IC's network/admin)

# Connect by host/port/service OR by TNS alias from tnsnames.ora
DB_USE_TNS = False
DB_TNS_ALIAS = ""

# Internal flag — when no DB credentials are configured the app keeps
# carton records in an in-memory store so the operator can still work.
# Nothing in the UI is labelled "mock"; the bottom status bar shows
# "DB: offline" when this is on.
USE_MOCK_DB = True

# ---------- Server API ----------
# When enabled, the GUI never opens Oracle directly. core.database and
# core.lpn_generator call this HTTP gateway instead, so Oracle credentials,
# pooling, idempotency and the daily LPN counter stay server-owned.
USE_SERVER_API = False
SERVER_API_BASE_URL = "http://127.0.0.1:8000/api/v1"
SERVER_DEVICE_ID = "stm32-test"
SERVER_DEVICE_SECRET = "test"
SERVER_API_TIMEOUT = 5.0

# ---------- Carton table ----------
CARTON_TABLE       = "APPS.XXFG_CARTON_MASTER"
CARTON_BATCH_TABLE = "APPS.XXFG_CARTON_BATCH_MASTER"
CARTON_BATCH_SEQ   = "APPS.XXFG_CARTON_BATCH_S"

# ---------- LPN ----------
LPN_PREFIX = "LPN-C-"
LPN_PADDING = 8

# ---------- UI defaults ----------
DEFAULT_UOM = "CTN"
DEFAULT_CARTON_QTY = 1

# Operator-facing dropdown values
SHIFT_OPTIONS = ["M", "N", "E"]            # Morning / Night / Evening
GRADE_OPTIONS = ["A", "B", "C"]

# Brands → (name, org_code, inv_code). org_code is the human-readable
# 3-digit Oracle org code shown next to the brand name; inv_code is the
# numeric ORGANIZATION_ID used in the SQL queries against XXFG_ORG_ITEMS
# and stored in XXFG_CARTON_MASTER.ORGANIZATION_ID. Mirrors the pallet
# app's BRAND_CODES so the data lines up across both apps.
BRANDS = [
    ("Monalisa",  "089", 481),
    ("X Monica",  "063", 369),
    ("Alexander", "064", 370),
    ("X Tiles",   "093", 522),
    ("Venus",     "062", 368),
]

# Item codes are NOT seeded — they come from APPS.XXFG_ORG_ITEMS in
# production (pallet-app pattern). In offline mode the Item Code field
# is a free-typed editable combo so the operator isn't blocked.

# Offline fallback for XXFG_SAMPLE_CARTON_CONFIG. Only used when
# USE_MOCK_DB=True, so the operator can exercise the PCS Type / CTN /
# pcs/ctn row without hitting Oracle. Mirrors the rows shown in your
# DB screenshot.
#   SIZE_CODE → (NORMAL_PCS_CTN, SAMPLE_PCS_CTN, CONVERSION_CTN)
FALLBACK_SAMPLE_CONFIG = {
    "20X30":        (25, 10, 0.40),
    "25X40":        (10,  6, 0.60),
    "30X45":        ( 8,  6, 0.75),
    "30X50":        ( 8,  6, 0.75),
    "30X60":        ( 6,  6, 1.00),
    "40X40":        ( 8,  6, 0.75),
    "60X60":        ( 4,  4, 1.00),
    "30X60(ROCKX)": ( 5,  6, 1.20),
}
