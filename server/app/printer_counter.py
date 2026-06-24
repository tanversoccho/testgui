"""Printer-owned count reader for dashboard/API counts.

The Dikai/LT printer owns the main cumulative print count. The API count
endpoint reads that value from the printer over TCP using the same
protocol documented in the pep reference:

    Send:    10 [PKGID] [TYPE] [DATA] 01
    Receive: 01 [PKGID] [TYPE] [DATA] 10

For count reads we open a short TCP session, send Check Activity with
PKGID 0 to reset the printer's session counter, then send Request Status
with PKGID 1 and parse the PCUM(10) field from the status payload.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import date
from typing import Optional, Tuple

from .config import settings

SEND_STX = 0x10
SEND_ETX = 0x01
RECV_STX = 0x01
RECV_ETX = 0x10


def _frame(pkgid: int, type_byte: int, data: bytes = b"") -> bytes:
    return bytes([SEND_STX, pkgid & 0xFF, type_byte]) + data + bytes([SEND_ETX])


def build_check_activity() -> bytes:
    """PKGID 0 resets the printer's communication sequence id."""
    return _frame(0, ord("A"))


def build_request_status(pkgid: int = 1) -> bytes:
    """LT-series `S` request. Reply contains RNWD, faults, fluids, and PCUM."""
    return _frame(pkgid, ord("S"))


def _ascii_num(raw: bytes, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(raw.decode("ascii"))
    except Exception:
        return default


def parse_status_reply(packet: bytes) -> Optional[dict]:
    """Parse a Request Status reply and return the cumulative printer count.

    DATA layout:
        RNWD(1) PRES(3) VISC(3) PHPF(2) PHAG(2) IKTP(3) HDTP(3)
        CBTP(3) MXLV(1) IPCT(3) SPCT(3) ESWD(4) PCUM(10)
    """
    if not packet or len(packet) < 45:
        return None
    if packet[0] != RECV_STX or packet[-1] != RECV_ETX:
        return None
    if packet[2] != ord("S"):
        return None
    data = packet[3:-1]
    if len(data) < 41:
        return None
    try:
        idx = 0

        def take(n: int) -> bytes:
            nonlocal idx
            part = data[idx:idx + n]
            idx += n
            return part

        rnwd = data[0]
        take(1)   # RNWD
        take(3)   # PRES
        take(3)   # VISC
        take(2)   # PHPF
        take(2)   # PHAG
        take(3)   # IKTP
        take(3)   # HDTP
        take(3)   # CBTP
        take(1)   # MXLV
        ink_pct = _ascii_num(take(3))
        sol_pct = _ascii_num(take(3))
        fault_word = int.from_bytes(take(4), "big")
        print_count = _ascii_num(take(10))
    except Exception:
        return None
    return {
        "rnwd": rnwd,
        "ink_pct": ink_pct,
        "sol_pct": sol_pct,
        "fault_word": fault_word,
        "print_count": print_count,
    }


def _recv_packet(sock: socket.socket) -> bytes:
    chunks = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
        if data and data[-1] == RECV_ETX:
            return data
    return b"".join(chunks)


def read_printer_status() -> Optional[dict]:
    """Read one live status frame from the printer.

    Returns None when the printer is unreachable, not responding, or sends
    a malformed status packet.
    """
    if settings.USE_MOCK_DB:
        return None
    if settings.PRINTER_PROTO.upper() != "TCP":
        return None
    try:
        with socket.create_connection(
            (settings.PRINTER_IP, int(settings.PRINTER_PORT)),
            timeout=float(settings.PRINTER_TIMEOUT),
        ) as sock:
            sock.settimeout(float(settings.PRINTER_TIMEOUT))
            sock.sendall(build_check_activity())
            try:
                _recv_packet(sock)
            except socket.timeout:
                pass
            sock.sendall(build_request_status(1))
            reply = _recv_packet(sock)
    except OSError:
        return None
    return parse_status_reply(reply)


def read_print_count() -> Optional[int]:
    status = read_printer_status()
    if not status or status.get("print_count") is None:
        return None
    return max(0, int(status["print_count"]))


def _state_path() -> str:
    return settings.PRINTER_COUNT_STATE_PATH


def _read_state() -> dict:
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def counts_from_printer(counter: Optional[int]) -> Tuple[int, int]:
    """Return `(today, all)` from the printer's cumulative counter.

    Disconnected or unreadable printer returns zero/zero. The first
    successful read each system day becomes the TODAY baseline.
    """
    if counter is None:
        return 0, 0
    current = max(0, int(counter))
    today = date.today().isoformat()
    state = _read_state()
    baseline = state.get("baseline")
    if state.get("date") != today or baseline is None:
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    try:
        baseline = int(baseline)
    except (TypeError, ValueError):
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    if current < baseline:
        baseline = current
        _write_state({"date": today, "baseline": baseline})
    return max(0, current - baseline), current


def count_for_scope(scope: str) -> int:
    today_count, total_count = counts_from_printer(read_print_count())
    return today_count if scope == "today" else total_count
