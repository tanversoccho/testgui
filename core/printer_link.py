"""Dikai DC81 link.

Real path uses the LT-series TCP protocol from pep/gui_2.py (RNWD state
field reports jet state: 0 stopped, 1 starting, 2 ready/sprayable, 3
stopping). When the printer is unreachable the link runs in a faithful
simulator so the GUI can still be exercised end-to-end on the desktop.

Important behaviours implemented here:

  • Starting the jet does NOT go straight to Ready. It steps through
    HEATING (printhead warm-up) → PURGING (nozzle clear) → READY.
  • While printing, every trigger advances a counter — each tick fires
    `print_event` so the application layer can insert one row into
    XXFG_CARTON_MASTER per actual print.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import socket
import threading
import time
import random


# DC81 fault bitmap — same layout as pep/protocol.cpp::FAULTS. Indexed
# by ESWD bit. Surfaced as one chip per active fault in the UI.
FAULT_BITS = {
    0:  "Jet not running",
    1:  "Maintenance due",
    2:  "Encoder too fast",
    3:  "Lid/hood removed",
    4:  "VMS chamber not full",
    5:  "VMS chamber not empty",
    6:  "Viscosity high/low",
    7:  "Solvent low",
    8:  "Ink low",
    9:  "Mixer tank low",
    10: "Cabinet too hot",
    11: "Gutter fault",
    12: "Charge error",
    13: "Mixer tank full",
    14: "EHT trip",
    15: "Fan failed",
    16: "Viscosity abnormal",
    17: "Printhead dirty",
}

from PySide6.QtCore import QObject, QThread, Signal

from config import config_app


SEND_STX = 0x10
SEND_ETX = 0x01
# Printer replies use *swapped* STX/ETX framing per the LT spec.
RECV_STX = 0x01
RECV_ETX = 0x10

# Back-compat aliases — older code paths reference these.
STX = SEND_STX
ETX = SEND_ETX

# Printhead column-count limit on the DC81 (per pep). The QR bitmap must
# fit inside an N×N grid where N ≤ MAX_DOTS.
MAX_DOTS = 34


def _frame(pkgid: int, type_byte: int, data: bytes = b"") -> bytes:
    return bytes([SEND_STX, pkgid, type_byte]) + data + bytes([SEND_ETX])


def parse_edit_params_reply(data: bytes) -> "dict | None":
    """Parse an 'E' (Edit System Parameters) reply.

    Reply frame (RECV_STX/RECV_ETX are swapped vs send):
        [0x01][PKGID][E][PTE(1)][MOD(2)][VSC(3)][PRS(3)][PTT(2)][CHG(3)][0x10]
    Returns a dict matching read_system_parameters()'s shape, or None
    if the bytes don't look like a valid reply.
    """
    if not data or len(data) < 17:
        return None
    if data[0] != RECV_STX or data[-1] != RECV_ETX:
        return None
    if data[2] != ord("E"):
        return None
    body = data[3:-1]    # PTE + numeric fields
    if len(body) < 14:
        return None
    try:
        return {
            "print_enable":   body[0:1] == b"1",
            "modulation":     int(body[1:3]),
            "viscosity":      int(body[3:6]),
            "ink_pressure":   int(body[6:9]),
            "nozzle_temp":    int(body[9:11]),
            "charge_value":   int(body[11:14]),
        }
    except (ValueError, IndexError):
        return None


def _fixed_ascii_number(value: int, width: int) -> bytes:
    """Zero-padded ASCII number for fixed-width protocol fields. Matches
    pep's helper of the same idea — '7' with width 2 → b'07'."""
    return f"{int(value):0{width}d}".encode("ascii")


def build_check_activity() -> bytes:
    """PKGID 0 resets the printer's communication sequence id."""
    return _frame(0, ord("A"))


def build_control_command(pkgid: int,
                          start_jet: bool = False,
                          stop_jet: bool = False,
                          reset_counter: bool = False) -> bytes:
    """LT-series 'C' packet — 3 ASCII flag bytes ('1'/'0' each)
    indicating start_jet, stop_jet, reset_counter."""
    data  = b"1" if start_jet     else b"0"
    data += b"1" if stop_jet      else b"0"
    data += b"1" if reset_counter else b"0"
    return _frame(pkgid, ord("C"), data)


def build_edit_system_params(pkgid: int,
                             print_enable: bool,
                             modulation:   int = 0,
                             viscosity:    int = 0,
                             ink_pressure: int = 0,
                             nozzle_temp:  int = 0,
                             charge:       int = 0) -> bytes:
    """LT-series 'E' packet — Edit System Parameters (spec 4.7).

    Frame data: [PTE(1)][MOD(2)][VSC(3)][PRS(3)][PTT(2)][CHG(3)]

    Numeric values of 0 mean "leave unchanged" on the printer — this
    lets us toggle print-enable on/off without disturbing the other
    setpoints. PTE is mandatory and is always written.

    Ranges per spec: MOD 5–99, VSC 150–600, PRS 100–500, PTT 10–60,
    CHG 100–200.
    """
    data  = b"1" if print_enable else b"0"
    data += _fixed_ascii_number(modulation,   2)
    data += _fixed_ascii_number(viscosity,    3)
    data += _fixed_ascii_number(ink_pressure, 3)
    data += _fixed_ascii_number(nozzle_temp,  2)
    data += _fixed_ascii_number(charge,       3)
    return _frame(pkgid, ord("E"), data)


def build_update_network_text(pkgid: int, text_id: int, text_value: str) -> bytes:
    """LT-series 'T' packet — update a Network Text field (1..10)."""
    text_value = (text_value or " ")[:100]
    text_id = max(1, min(10, int(text_id)))
    data = bytes([text_id]) + text_value.encode("ascii", errors="replace")
    return _frame(pkgid, ord("T"), data)


def build_modify_message_params(
    pkgid: int,
    *,
    reverse: bool = False,
    invert: bool = False,
    width: int = 100,
    delay: int = 0,
    height: int = 1,
    dots: int = 34,
    trigger_times: int = 1,
    gap: int = 0,
    column_repeats: int = 0,
    char_space: int = 1,
) -> bytes:
    """LT-series 'P' packet — Modify Message Parameters (spec 4.4).

    Byte layout (all ASCII digits, zero-padded fixed width):
        [reverse 1B '0'/'1']  [invert 1B '0'/'1']
        [width 4B]            [delay 5B]
        [height 2B]           [dots 2B]
        [trig_times 2B]       [gap 5B]
        [column_repeats 2B]   [char_space 1B]

    Total payload = 25 ASCII bytes. Scales the *whole* active message —
    QR (Net Logo) and text (Net Text) share the same sizing. There is
    no per-field scale in the DC81 protocol; for independent text vs.
    QR sizing you'd need separate templates and an 'M' Select Message.

    Byte-identical to pep's build_modify_message_params and verified
    against the protocol PDF example in pep/verify_protocol.py."""
    data  = b"1" if reverse else b"0"
    data += b"1" if invert  else b"0"
    data += _fixed_ascii_number(width,           4)
    data += _fixed_ascii_number(delay,           5)
    data += _fixed_ascii_number(height,          2)
    data += _fixed_ascii_number(dots,            2)
    data += _fixed_ascii_number(trigger_times,   2)
    data += _fixed_ascii_number(gap,             5)
    data += _fixed_ascii_number(column_repeats,  2)
    data += _fixed_ascii_number(char_space,      1)
    return _frame(pkgid, ord("P"), data)


def _strip_for_text(s: str) -> str:
    """Coerce a label string into something the LT-series text command accepts."""
    s = s or " "
    # Replace anything outside printable ASCII with '?'
    return "".join(c if 0x20 <= ord(c) <= 0x7E else "?" for c in s)[:100]


def build_net_logo_packet(pkgid: int, logo_id: int, width: int, height: int, bits: str) -> bytes:
    """LT-series 'L' packet — upload a monochrome bitmap to a Net Logo slot."""
    logo_id = max(1, min(5, int(logo_id)))
    data = (
        bytes([logo_id])
        + f"{width:04d}".encode("ascii")
        + f"{height:02d}".encode("ascii")
        + bits.encode("ascii")
    )
    return _frame(pkgid, ord("L"), data)


def build_request_status(pkgid: int) -> bytes:
    """LT-series 'S' packet — Request Status. The printer replies with
    the current jet RNWD word, fault bitmap, telemetry, and cumulative
    print count. Used for periodic polling."""
    return _frame(pkgid, ord("S"))


# RNWD (Running Word) values used in the 'S' reply.
RNWD_STOPPED  = 0
RNWD_STARTING = 1
RNWD_READY    = 2
RNWD_STOPPING = 3


def _ascii_num(b: bytes, default=None):
    try:
        return int(b.decode("ascii"))
    except Exception:
        return default


def parse_status_reply(packet: bytes) -> "dict | None":
    """Parse an 'S' Request-Status reply frame into a structured dict.

    Reply frame: [0x01][PKGID][S][DATA][0x10]
    DATA layout (same fields the operator panel shows):
        RNWD(1) PRES(3) VISC(3) PHPF(2) PHAG(2) IKTP(3) HDTP(3)
        CBTP(3) MXLV(1) IPCT(3) SPCT(3) ESWD(4) PCUM(10)

    Returns None on a malformed or short frame."""
    if not packet or len(packet) < 4:
        return None
    if packet[0] != RECV_STX or packet[-1] != RECV_ETX:
        return None
    if packet[2] != ord("S"):
        return None
    data = packet[3:-1]
    # 1+3+3+2+2+3+3+3+1+3+3+4+10 = 41 bytes minimum
    if len(data) < 41:
        return None
    off = 0

    def take(n):
        nonlocal off
        chunk = data[off:off + n]
        off += n
        return chunk

    try:
        rnwd = take(1)[0]
        pres = _ascii_num(take(3))
        visc = _ascii_num(take(3))
        take(2)                       # PHPF (not surfaced)
        take(2)                       # PHAG (not surfaced)
        iktp = _ascii_num(take(3))
        hdtp = _ascii_num(take(3))
        cbtp = _ascii_num(take(3))
        mxlv = take(1)[0]
        ipct = _ascii_num(take(3))
        spct = _ascii_num(take(3))
        eswd = int.from_bytes(take(4), "big")
        pcum = _ascii_num(take(10))
    except Exception:
        return None
    return {
        "rnwd":         rnwd,
        "ink_pressure": pres,
        "viscosity":    visc,
        "ink_temp":     iktp,
        "head_temp":    hdtp,
        "cabinet_temp": cbtp,
        "mxlv":         mxlv,
        "ink_pct":      ipct,
        "sol_pct":      spct,
        "fault_word":   eswd,
        "print_count":  pcum,
    }


class JetState(Enum):
    STOPPED  = "STOPPED"
    HEATING  = "HEATING"
    PURGING  = "PURGING"
    READY    = "READY"
    STOPPING = "STOPPING"
    FAULT    = "FAULT"

    @property
    def is_starting(self) -> bool:
        return self in (JetState.HEATING, JetState.PURGING)

    @property
    def label(self) -> str:
        return {
            JetState.STOPPED:  "Jet Stopped",
            JetState.HEATING:  "Heating ink…",
            JetState.PURGING:  "Purging nozzle…",
            JetState.READY:    "Ready to spray",
            JetState.STOPPING: "Stopping…",
            JetState.FAULT:    "Fault",
        }[self]


@dataclass
class PrinterStatus:
    connected: bool = False
    simulated: bool = False
    jet: JetState = JetState.STOPPED

    # True if the printer has replied to at least one of our packets —
    # tells the operator that the link is genuinely bidirectional
    # (vs. UDP "one-way" printers that ignore probes but accept writes).
    two_way: bool = False

    # Print Enable — independent of jet. Auto-printing requires BOTH
    # jet == READY AND print_enabled == True.
    print_enabled: bool = False

    # Progress 0..100 within the current starting stage (for the progress bar)
    stage_progress: int = 0

    ink_ok: bool = True
    sol_ok: bool = True
    ink_level: int = 78
    sol_level: int = 65

    counter: int = 0
    fault: str = ""
    # Active fault chips — one entry per fault bit set in ESWD (after
    # masking out info-only bits like "Jet not running" / Ink low / Sol
    # low which are surfaced through the jet state and INK/SOL chips).
    faults: List[str] = field(default_factory=list)

    def is_ready_to_print(self) -> bool:
        return self.connected and self.jet == JetState.READY and self.print_enabled


class PrinterLink(QObject):
    """Public API:
        connect() / disconnect()
        start_jet() / stop_jet()
        push_label(carton)
    Signals:
        status_changed(PrinterStatus)
        print_event(int counter)   — emitted once per physical print
        log(str)
    """
    status_changed = Signal(object)
    print_event   = Signal(int)
    pushed        = Signal(str)        # emitted on every successful push (payload preview)
    log           = Signal(str)

    # Hysteresis: once the link is judged "responding", require this
    # many consecutive missed replies before downgrading to 1-way. Stops
    # the UI badge from ping-ponging on a single jittery packet.
    TWO_WAY_MISS_THRESHOLD = 3

    # After this many consecutive misses we also DROP the TCP socket.
    # The next command will reconnect, which automatically re-sends
    # Check Activity and re-syncs the printer's PKGID expectation.
    # Recovers from the "printer firmware in a stuck state" failure
    # without operator intervention.
    DROP_SOCKET_MISS_THRESHOLD = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        # IO lock — serialises every wire send (UDP sendto / TCP sendall +
        # recv). The poll thread can be triggering 'S' while the operator
        # hits "Apply Parameters" on the UI thread; without this they can
        # interleave bytes on the TCP stream.
        self._io_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._pkgid = config_app.PRINTER_PKGID & 0xFF
        self._status = PrinterStatus()
        self._poll_thread: Optional[QThread] = None
        self._poll_stop = threading.Event()

        # Hysteresis counter for two_way — see TWO_WAY_MISS_THRESHOLD.
        self._consecutive_miss = 0

        # Currently loaded label payload (auto-sent on push_label and re-sent on Ready)
        self._pending_label_text: str = ""
        self._pending_qr_payload: str = ""

        # Simulator state for the multi-stage start
        self._stage_started_at: float = 0.0

        # Operator-intent latches. Pep gets away with raw fire-and-forget
        # buttons because its Start and Stop are physically separate
        # widgets — pressing Stop never has to look at the printer's
        # RNWD. We have ONE toggle, so we need to remember which side
        # the operator most recently pressed and override the polled
        # state until the printer actually confirms the transition.
        # Without this the button bounces back to "Jet Stop" the moment
        # the status poll lands while the printer is still draining
        # (RNWD=3 STOPPING).
        self._intent_stop: bool = False    # set by stop_jet(), cleared on RNWD=0
        self._intent_start: bool = False   # set by start_jet(), cleared on RNWD=2

    # ------- PKGID API -------
    def get_pkgid(self) -> int:
        return self._pkgid

    def set_pkgid(self, n: int):
        self._pkgid = max(1, min(255, int(n))) & 0xFF
        config_app.PRINTER_PKGID = self._pkgid

    def reset_pkgid(self):
        self.set_pkgid(1)

    # ------- public -------
    @property
    def status(self) -> PrinterStatus:
        return self._status

    def connect(self) -> bool:
        """Open the link to the printer. Matches pep's pattern:

        * TCP — opens a long-lived socket and keeps it in `self._sock`.
          Every later command sends/recvs on this same session, so the
          printer's PKGID sequence stays in sync.
        * UDP — does NOT keep a socket. Each command opens a brand-new
          UDP socket and uses sendto/recvfrom. A `connect()`d UDP
          socket would filter inbound datagrams by source ⟨ip,port⟩
          and drop the printer's reply if it comes from a different
          ephemeral port — that's exactly the bug pep avoided. We probe
          reachability with a Check Activity send_recv here.

        Returns True if we treat the printer as connected. SIMULATOR_MODE
        falls back to the in-memory simulator for dev/testing.
        """
        # Close any existing socket first (no-op for UDP)
        self._close_socket()

        proto = (config_app.PRINTER_PROTO or "TCP").upper()
        try:
            if proto == "TCP":
                s = socket.create_connection(
                    (config_app.PRINTER_IP, config_app.PRINTER_PORT),
                    timeout=config_app.PRINTER_TIMEOUT,
                )
                s.settimeout(config_app.PRINTER_TIMEOUT)
                self._sock = s
            # UDP: nothing to open here — `_send_recv` makes a fresh
            # socket per call. We still attempt a probe below so the
            # operator gets immediate feedback.

            self._status.connected = True
            self._status.simulated = False
            # Optimistic: hysteresis ratchets this down only after
            # several consecutive misses.
            self._status.two_way = True
            self._consecutive_miss = 0
            # Reset our local PKGID to 0 so the post-probe sequence
            # starts at 1 — matches what the printer expects after a
            # Check Activity. LT firmware silently drops out-of-sequence
            # packets; if we don't reset, the first real command gets
            # ignored and the operator sees "no reply" for every action.
            self._pkgid = 0
            self.log.emit(
                f"Connected to printer at {config_app.PRINTER_IP}:{config_app.PRINTER_PORT} ({proto})."
            )
            # Probe — Check Activity (PKGID 0) per LT spec resets the
            # printer's PKGID sequence on a fresh TCP session and gives
            # us a way to confirm UDP reachability.
            reply = self._send_recv(build_check_activity(), suppress_log=True)
            if reply:
                self.log.emit(
                    f"Probe ACK ({len(reply)} bytes) — printer is responding."
                )
            else:
                # Some UDP printers ignore Check Activity but still
                # accept writes. Treat as 1-way; the first answered
                # command flips us back to responding.
                self._status.two_way = False
                self.log.emit(
                    "Probe sent but no reply within timeout — treating as one-way printer."
                )
        except Exception as e:
            self._sock = None
            if config_app.SIMULATOR_MODE:
                self._status.connected = True
                self._status.simulated = True
                self._status.two_way = True
                self.log.emit(f"Printer unreachable ({e}). Simulator engaged (dev mode).")
            else:
                self._status.connected = False
                self._status.simulated = False
                self._status.two_way = False
                self._status.jet = JetState.STOPPED
                self._status.print_enabled = False
                self._status.stage_progress = 0
                self.log.emit(f"Printer offline ({e}).")
        self._emit_status()
        return self._status.connected

    def _close_socket(self):
        """Cleanly tear down the TCP socket. We send a proper FIN
        (shutdown SHUT_RDWR) before close so the printer's embedded
        TCP stack releases its single-client session immediately —
        otherwise some firmwares hold the slot for a long timeout and
        refuse the next connection."""
        if self._sock is not None:
            try:
                if self._sock.type == socket.SOCK_STREAM:
                    try: self._sock.shutdown(socket.SHUT_RDWR)
                    except Exception: pass
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def apply_network_settings(
        self,
        *,
        mode: str,
        ip: str,
        port: int,
        timeout_s: float,
        pkgid: int,
        auto_increment: bool,
    ) -> bool:
        """Write the new connection params to config and (re)connect."""
        config_app.PRINTER_PROTO   = (mode or "TCP").upper()
        config_app.PRINTER_IP      = ip.strip() or config_app.PRINTER_IP
        config_app.PRINTER_PORT    = max(1, min(65535, int(port)))
        config_app.PRINTER_TIMEOUT = max(0.1, float(timeout_s))
        config_app.PRINTER_AUTO_INCREMENT_PKGID = bool(auto_increment)
        self.set_pkgid(int(pkgid))
        return self.connect()

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._status.connected = False
        self._status.simulated = False
        self._status.two_way = False
        self._status.jet = JetState.STOPPED
        self._status.print_enabled = False
        # Clear operator-intent latches so the next session starts clean.
        self._intent_stop = False
        self._intent_start = False
        # Re-baseline the print counter on the next connect — otherwise
        # reconnecting to a printer that's printed since we last saw it
        # would treat the delta as a stream of new events.
        self._counter_initialised = False
        self._emit_status()

    def is_operational(self) -> bool:
        """True only when the printer is genuinely usable (connected, no
        fault). Used by the UI to gate every read/write button."""
        return self._status.connected and not self._status.fault

    def start_polling(self, interval_s: float | None = None):
        if self._poll_thread is not None:
            return
        interval = interval_s or config_app.POLL_INTERVAL_SECONDS
        self._poll_stop.clear()
        self._poll_thread = _PollThread(self, interval, self._poll_stop)
        self._poll_thread.start()

    def stop_polling(self):
        self._poll_stop.set()
        if self._poll_thread:
            self._poll_thread.wait(2000)
            self._poll_thread = None

    # ----- Control commands (jet on/off, print on/off, system params) -----
    def start_jet(self) -> bool:
        """Fire pep's Jet Start (Control 'C', start_jet=True).

        Matches pep's dashboard button: always sends the command when
        the link is up, and updates local state optimistically. Sets
        the start-intent latch so status polls don't bounce the button
        back to "Jet Start" while RNWD transitions 0→1→2.

        Gated ONLY on connection — never on faults. The mandatory
        form-field check happens upstream in main_window._on_jet_start;
        active fault chips (lid/hood, mixer tank, etc.) MUST NOT block
        sending the command. The printer itself decides whether it can
        actually start; we just deliver the packet."""
        if not self._status.connected:
            self.log.emit("Cannot start jet: printer not connected.")
            return False
        # Operator intent: ON. Clears the opposite latch immediately so a
        # rapid Stop → Start press resolves to Start.
        self._intent_start = True
        self._intent_stop = False
        st = self._status
        if self._status.simulated:
            st.jet = JetState.HEATING
            st.stage_progress = 0
            self._stage_started_at = time.monotonic()
            self.log.emit("Start Jet (simulated).")
            self._emit_status()
            return True
        pkt = self._stamped(build_control_command, start_jet=True)
        self._send_recv(pkt, suppress_log=True)
        # Optimistic — flip state immediately so the button label updates.
        st.jet = JetState.HEATING
        st.stage_progress = 0
        self._stage_started_at = time.monotonic()
        self.log.emit("Start Jet sent.")
        self._emit_status()
        return True

    def stop_jet(self) -> bool:
        """Fire pep's Jet Stop (Control 'C', stop_jet=True).

        Fire-and-forget, mirrors pep's dashboard button. Sets the
        stop-intent latch so status polls don't bounce the button back
        to "Jet Stop" while the printer drains (RNWD=3 STOPPING).
        Cleared when the printer finally reports RNWD=0.

        Gated ONLY on connection — fault chips never block Stop."""
        if not self._status.connected:
            self.log.emit("Cannot stop jet: printer not connected.")
            return False
        # Operator intent: OFF. Clear any pending start latch.
        self._intent_stop = True
        self._intent_start = False
        st = self._status
        if self._status.simulated:
            st.print_enabled = False
            st.jet = JetState.STOPPING
            st.stage_progress = 0
            self._stage_started_at = time.monotonic()
            self.log.emit("Stop Jet (simulated).")
            self._emit_status()
            return True
        pkt = self._stamped(build_control_command, stop_jet=True)
        self._send_recv(pkt, suppress_log=True)
        # Optimistic — operator pressed Stop, treat as stopped so the
        # button label flips back to "Jet Start" immediately. The
        # _intent_stop latch prevents the next status poll from
        # bouncing us back to STOPPING.
        st.print_enabled = False
        st.jet = JetState.STOPPED
        st.stage_progress = 0
        self._stage_started_at = time.monotonic()
        self.log.emit("Stop Jet sent.")
        self._emit_status()
        return True

    def reset_counter(self) -> bool:
        """Send a 'C' Reset Message Counter command."""
        if not self.is_operational():
            return False
        if self._status.simulated:
            self._status.counter = 0
            self._emit_status()
            return True
        pkt = self._stamped(build_control_command, reset_counter=True)
        reply = self._send_recv(pkt)
        ok = self._control_reply_ok(reply, index=2)
        if ok:
            self._status.counter = 0
            self.log.emit("Counter reset.")
            self._emit_status()
        return ok

    def set_print_enabled(self, enabled: bool) -> bool:
        """Toggle Print Enable via Edit System Parameters ('E', PTE flag).

        IMPORTANT — DC800 firmware behaviour
        ────────────────────────────────────
        The original pep implementation sent the 'E' packet with all
        numeric fields zero, on the assumption that 0 means "leave
        unchanged". That assumption holds on the LT series but the
        DC800 firmware **validates** each numeric against its range
        (per the DC800 User Manual page 25):

            Modulation     5-99
            Viscosity      150-600
            Ink Pressure   100-500
            Nozzle Temp    0-60     (the only field where 0 is valid)
            Charge Value   80-200

        Four of those out-of-range zeros cause the firmware to reject
        the whole packet — including the PTE bit — which is exactly
        why the Print On/Off button "does nothing" on pep against a
        DC800. The fix is to send the **cached current setpoints** in
        the numeric fields so the packet validates; the printer then
        applies PTE without touching MOD/VSC/PRS/PTT/CHG (because the
        values are identical to what's already in the firmware).

        config_app holds those cached setpoints — they're populated
        on startup (config_loader.load) and refreshed by every
        successful read_system_parameters / apply_system_parameters,
        so they're always in-range and current.

        Fire-and-forget like the rest of the dashboard buttons; local
        state flips immediately so the UI updates without waiting for
        the printer's reply."""
        if not self.is_operational():
            self.log.emit("Cannot change print state: printer offline.")
            return False
        st = self._status
        if self._status.simulated:
            st.print_enabled = enabled
            self.log.emit(f"Print {'enabled' if enabled else 'disabled'} (sim).")
            self._emit_status()
            return True
        # Cached setpoints — guaranteed in-range by config_app defaults
        # and the loader's clamp. Send them back unchanged so only PTE
        # actually changes on the printer.
        pkt = self._stamped(
            build_edit_system_params,
            print_enable=enabled,
            modulation=int(config_app.MODULATION_SET),
            viscosity=int(config_app.VISCOSITY_SET),
            ink_pressure=int(config_app.INK_PRESSURE),
            nozzle_temp=int(config_app.NOZZLE_TEMP),
            charge=int(config_app.CHARGE_VALUE),
        )
        reply = self._send_recv(pkt, suppress_log=True)
        # Trust the printer's echo if we got one — confirms PTE flipped.
        parsed = parse_edit_params_reply(reply) if reply else None
        if parsed is not None:
            st.print_enabled = bool(parsed["print_enable"])
            config_app.PRINT_ENABLE = st.print_enabled
        else:
            # No reply (or malformed) — optimistic flip, same as before.
            st.print_enabled = enabled
            config_app.PRINT_ENABLE = enabled
        self.log.emit(
            f"Print {'ON' if st.print_enabled else 'OFF'} sent "
            f"(MOD={config_app.MODULATION_SET} VSC={config_app.VISCOSITY_SET} "
            f"PRS={config_app.INK_PRESSURE} PTT={config_app.NOZZLE_TEMP} "
            f"CHG={config_app.CHARGE_VALUE})."
        )
        self._emit_status()
        return True

    def apply_system_parameters(
        self,
        *,
        print_enable: bool,
        modulation: int,
        viscosity: int,
        ink_pressure: int,
        nozzle_temp: int,
        charge_value: int,
    ) -> bool:
        """Push the printer's system parameters ('E' command, spec 4.7).
        Waits for the reply and re-reads it as the new ground truth so
        the operator sees the printer's actual setpoints (which may be
        clamped to the printer's range)."""
        if not self.is_operational():
            self.log.emit("Cannot apply parameters: printer offline.")
            return False
        # Snapshot to config so a later restart applies the same setpoints
        config_app.PRINT_ENABLE   = print_enable
        config_app.MODULATION_SET = modulation
        config_app.VISCOSITY_SET  = viscosity
        config_app.INK_PRESSURE   = ink_pressure
        config_app.NOZZLE_TEMP    = nozzle_temp
        config_app.CHARGE_VALUE   = charge_value
        if self._status.simulated:
            self.log.emit("System params applied (simulated).")
            return True
        pkt = self._stamped(
            build_edit_system_params,
            print_enable=print_enable,
            modulation=modulation,
            viscosity=viscosity,
            ink_pressure=ink_pressure,
            nozzle_temp=nozzle_temp,
            charge=charge_value,
        )
        reply = self._send_recv(pkt)
        parsed = parse_edit_params_reply(reply) if reply else None
        if parsed is not None:
            config_app.PRINT_ENABLE   = bool(parsed["print_enable"])
            config_app.MODULATION_SET = int(parsed["modulation"])
            config_app.VISCOSITY_SET  = int(parsed["viscosity"])
            config_app.INK_PRESSURE   = int(parsed["ink_pressure"])
            config_app.NOZZLE_TEMP    = int(parsed["nozzle_temp"])
            config_app.CHARGE_VALUE   = int(parsed["charge_value"])
            self._status.print_enabled = bool(parsed["print_enable"])
            self.log.emit(
                f"System params confirmed  mod={parsed['modulation']} "
                f"visc={parsed['viscosity']} ink_p={parsed['ink_pressure']} "
                f"noz_t={parsed['nozzle_temp']} charge={parsed['charge_value']} "
                f"print={'on' if parsed['print_enable'] else 'off'}"
            )
            self._emit_status()
            return True
        self.log.emit(
            f"System params sent (no confirm)  mod={modulation} visc={viscosity} "
            f"ink_p={ink_pressure} noz_t={nozzle_temp} charge={charge_value} "
            f"print={'on' if print_enable else 'off'}"
        )
        return True

    def apply_message_params(
        self,
        *,
        reverse: bool,
        invert: bool,
        width: int,
        delay: int,
        height: int,
        dots: int,
        trigger_times: int,
        gap: int,
        column_repeats: int,
        char_space: int,
    ) -> bool:
        """Send the 'P' Modify Message Params packet (spec 4.4).

        Fire-and-forget like apply_system_parameters once we're confident
        the printer accepted — the reply echoes the same field layout
        but we don't currently re-display it. Updates config_app so the
        same values are reused on the next push_label."""
        if not self.is_operational():
            self.log.emit("Cannot apply message params: printer offline.")
            return False
        # Snapshot to config so a restart applies the same sizing
        config_app.MSG_REVERSE      = bool(reverse)
        config_app.MSG_INVERT       = bool(invert)
        config_app.MSG_WIDTH        = int(width)
        config_app.MSG_DELAY        = int(delay)
        config_app.MSG_HEIGHT       = int(height)
        config_app.MSG_PRINTED_DOTS = int(dots)
        config_app.MSG_TRIG_TIMES   = int(trigger_times)
        config_app.MSG_GAP          = int(gap)
        config_app.MSG_COL_REPEATS  = int(column_repeats)
        config_app.MSG_CHAR_SPACE   = int(char_space)
        if self._status.simulated:
            self.log.emit("Message params applied (simulated).")
            return True
        pkt = self._stamped(
            build_modify_message_params,
            reverse=reverse, invert=invert,
            width=width, delay=delay, height=height, dots=dots,
            trigger_times=trigger_times, gap=gap,
            column_repeats=column_repeats, char_space=char_space,
        )
        # Send WITH reply check so we can log whether the printer
        # echoed back a 'P' frame. A printer that's connected but
        # refusing the command (e.g. wrong PKGID sequence, no message
        # loaded) typically times out at this point.
        reply = self._send_recv(pkt)
        if reply and len(reply) >= 4 and reply[2] == ord("P"):
            self.log.emit(
                f"'P' confirmed  w={width} h={height} dots={dots} "
                f"trig={trigger_times} gap={gap} colRep={column_repeats} "
                f"chSp={char_space} rev={int(reverse)} inv={int(invert)}"
            )
        elif reply:
            self.log.emit(
                f"'P' sent but reply was unexpected ({len(reply)}B). "
                f"Setpoints saved locally; check printer's parameter menu."
            )
        else:
            self.log.emit(
                "'P' sent but no reply within timeout. Try Read Status "
                "to confirm the link is still responsive."
            )
        # Re-push T+L with the new sizing so the printer's stored
        # message picks up new QR dimensions and any text-related
        # changes on the next print without needing the operator to
        # edit a form field.
        self.repush_pending_label()
        return True

    # ----- 'C' reply helper -----
    @staticmethod
    def _control_reply_ok(reply: bytes | None, index: int) -> bool:
        """Inspect a 'C' (Cleaning/Control) reply frame. DATA is 3 ASCII
        flag bytes — index 0=start, 1=stop, 2=reset. '1' = accepted."""
        if not reply or len(reply) < 5:
            return False
        if reply[0] != RECV_STX or reply[-1] != RECV_ETX:
            return False
        if reply[2] != ord("C"):
            return False
        body = reply[3:-1]
        if len(body) <= index:
            return False
        return body[index:index + 1] == b"1"

    # ----- internal -----
    def _stamped(self, builder, *args, **kwargs) -> bytes:
        """Increment PKGID (if auto-increment), call the builder with the
        new id, return raw bytes ready for the wire."""
        with self._lock:
            if config_app.PRINTER_AUTO_INCREMENT_PKGID:
                self._pkgid = (self._pkgid + 1) & 0xFF
                if self._pkgid == 0:
                    self._pkgid = 1
            return builder(self._pkgid, *args, **kwargs)

    def read_print_count(self) -> int | None:
        """Return the printer's current Message Counter (one tick per print).
        Returns None when offline. Real mode goes through 'S' so the
        value reflects the printer's PCUM, not just our cached counter."""
        if not self.is_operational():
            return None
        if self._status.simulated:
            return self._status.counter
        parsed = self.request_status()
        if parsed and parsed.get("print_count") is not None:
            return int(parsed["print_count"])
        return self._status.counter

    def request_status(self) -> dict | None:
        """Send 'S' Request Status, parse the reply, update internal
        jet/counter/fault state, and return the parsed dict. Returns
        None if the printer didn't reply or the frame was malformed."""
        if not self.is_operational() or self._status.simulated:
            return None
        pkt = self._stamped(build_request_status)
        reply = self._send_recv(pkt)
        parsed = parse_status_reply(reply) if reply else None
        if parsed is None:
            return None
        # Apply to internal state
        st = self._status
        rnwd = parsed["rnwd"]
        new_jet = {
            RNWD_STOPPED:  JetState.STOPPED,
            RNWD_STARTING: JetState.HEATING,    # printer doesn't distinguish heating vs purging
            RNWD_READY:    JetState.READY,
            RNWD_STOPPING: JetState.STOPPING,
        }.get(rnwd, st.jet)

        # ---- Operator-intent overrides ----
        # If the operator pressed Stop, force the displayed state to
        # STOPPED until the printer actually reaches RNWD=0. Otherwise
        # the button bounces back to "Jet Stop" the moment the next
        # status poll lands while the printer is still draining
        # (RNWD=3 STOPPING). Clear the latch once the printer confirms
        # — at that point reality matches intent and the override is
        # no longer needed.
        if self._intent_stop:
            if rnwd == RNWD_STOPPED:
                self._intent_stop = False     # printer finally caught up
            else:
                new_jet = JetState.STOPPED    # hold the button on "Jet Start"
        # Same pattern for Start — keep "Jet Stop" label during the
        # 0→1→2 ramp even if a stale RNWD=0 reading sneaks in.
        if self._intent_start:
            if rnwd == RNWD_READY:
                self._intent_start = False
            elif rnwd == RNWD_STOPPED:
                new_jet = JetState.HEATING    # hold the button on "Jet Stop"

        # Don't clobber a UI-driven HEATING/PURGING transition just
        # because the printer reports "starting" — keep the more
        # specific stage so the progress bar still animates.
        if new_jet == JetState.HEATING and st.jet == JetState.PURGING:
            pass    # leave PURGING
        elif new_jet != st.jet:
            st.jet = new_jet
            if new_jet == JetState.READY:
                st.stage_progress = 100
            elif new_jet == JetState.STOPPED:
                st.stage_progress = 0
                st.print_enabled = False
        # Counter / consumables / fault
        if parsed.get("print_count") is not None:
            new_count = int(parsed["print_count"])
            if not getattr(self, "_counter_initialised", False):
                # First reading after connect — establish baseline
                # without firing any print_events (otherwise we'd
                # insert thousands of phantom DB rows for the printer's
                # lifetime cumulative count).
                st.counter = new_count
                self._counter_initialised = True
            elif new_count > st.counter:
                # Real increment since the previous poll — emit one
                # print_event per carton printed. Capped to avoid a
                # runaway insert if the printer's PCUM has jumped
                # unexpectedly (e.g. someone reset it).
                delta = new_count - st.counter
                if delta > 100:
                    self.log.emit(
                        f"Printer counter jumped by {delta} — "
                        f"treating as a reset, not emitting events."
                    )
                else:
                    for _ in range(delta):
                        self.print_event.emit(new_count)
                st.counter = new_count
            elif new_count < st.counter:
                # PCUM went down — operator reset the counter or the
                # printer was power-cycled. Re-baseline silently.
                st.counter = new_count
        if parsed.get("ink_pct") is not None:
            st.ink_level = int(parsed["ink_pct"])
            st.ink_ok = st.ink_level > 10
        if parsed.get("sol_pct") is not None:
            st.sol_level = int(parsed["sol_pct"])
            st.sol_ok = st.sol_level > 10
        # Mask out informational bits we already surface elsewhere:
        #   bit 0 — "Jet not running" (conveyed by jet state)
        #   bit 7 — Solvent low (shown as SOL warning chip)
        #   bit 8 — Ink low (shown as INK warning chip)
        # Anything else left set is a real fault the operator should see.
        FAULT_INFO_MASK = (1 << 0) | (1 << 7) | (1 << 8)
        real_faults = parsed["fault_word"] & ~FAULT_INFO_MASK
        st.fault = "" if real_faults == 0 else f"FAULT 0x{real_faults:08X}"
        # Build the human-readable fault list shown as chips by the UI.
        st.faults = [
            name for bit, name in FAULT_BITS.items()
            if real_faults & (1 << bit)
        ]
        return parsed

    def read_system_parameters(self) -> dict | None:
        """Read current setpoints from the printer.

        Returns:
          * dict with the printer's actual setpoints on a live reply
          * None if offline, no reply, or unrecognised reply

        We intentionally do NOT return the cached config values on no-
        reply. Surfacing stale data dressed up as a successful Read is
        worse than telling the operator the printer didn't answer."""
        if not self.is_operational():
            return None
        if self._status.simulated:
            return {
                "print_enable":   config_app.PRINT_ENABLE,
                "modulation":     config_app.MODULATION_SET,
                "viscosity":      config_app.VISCOSITY_SET,
                "ink_pressure":   config_app.INK_PRESSURE,
                "nozzle_temp":    config_app.NOZZLE_TEMP,
                "charge_value":   config_app.CHARGE_VALUE,
            }
        # Query frame — all numeric zeros = "report current values"
        pkt = self._stamped(
            build_edit_system_params,
            print_enable=config_app.PRINT_ENABLE,
            modulation=0, viscosity=0, ink_pressure=0,
            nozzle_temp=0, charge=0,
        )
        reply = self._send_recv(pkt)
        if reply is None:
            self.log.emit("Read system parameters: no reply from printer.")
            return None
        parsed = parse_edit_params_reply(reply)
        if parsed is None:
            self.log.emit(
                f"Read system parameters: unrecognised reply ({reply!r:.80})."
            )
            return None
        # Cache the live values into config so a UI restart doesn't lose them.
        config_app.PRINT_ENABLE   = bool(parsed["print_enable"])
        config_app.MODULATION_SET = int(parsed["modulation"])
        config_app.VISCOSITY_SET  = int(parsed["viscosity"])
        config_app.INK_PRESSURE   = int(parsed["ink_pressure"])
        config_app.NOZZLE_TEMP    = int(parsed["nozzle_temp"])
        config_app.CHARGE_VALUE   = int(parsed["charge_value"])
        self.log.emit(
            f"Read system params from printer  mod={parsed['modulation']} "
            f"visc={parsed['viscosity']} ink_p={parsed['ink_pressure']} "
            f"noz_t={parsed['nozzle_temp']} charge={parsed['charge_value']} "
            f"print={'on' if parsed['print_enable'] else 'off'}"
        )
        return parsed

    def repush_pending_label(self) -> bool:
        """Re-push the most recently set label to the printer using the
        CURRENT QR sizing config. Used after the operator changes Print
        Sizing in Settings — the saved knobs aren't visible on the
        carton until a fresh T+L is sent (the QR's dot dimensions live
        inside the 'L' packet header), and push_label is otherwise only
        triggered by form changes.

        Returns False with a log message if there's nothing buffered to
        re-push or the link is offline."""
        text = self._pending_label_text
        qr   = self._pending_qr_payload
        if not (text or qr):
            self.log.emit("Re-push skipped — no label payload buffered yet.")
            return False
        if not self.is_operational():
            self.log.emit("Re-push skipped — printer offline.")
            return False
        return self.push_label(text, qr)

    def push_label(self, text_payload: str, qr_payload: str) -> bool:
        """Send the current label data to the printer's active message —
        Network Text field 1 + Net Logo slot 1.

        Matches pep's QR+Text Printing tab: sends the same content to
        BOTH the printer's Net Text field and Net Logo (QR) slot, with
        a short delay between packets so the printer's parser has time
        to swallow the first frame before the second arrives. Both
        packets go through `_send_recv` so the reply is drained from
        the TCP buffer (and so the IO lock serialises us with the
        status poller).

        Called automatically whenever the operator changes any field
        that affects the QR payload; once jet is READY the printer
        will use this payload on its next sensor-triggered auto-print."""
        self._pending_label_text = text_payload or ""
        self._pending_qr_payload = qr_payload or ""

        # Gate on connected/simulated — NOT on self._sock, which is
        # always None in UDP mode (fresh socket per call).
        if self._status.connected and not self._status.simulated:
            try:
                # 1) Text packet — send the full QR-content string so
                #    the printer's network text field shows the same
                #    payload the QR encodes. Matches pep's "one content
                #    pushed to both text + QR" pattern.
                wire_text = _strip_for_text(qr_payload or text_payload or " ")
                self._send_recv(
                    self._stamped(build_update_network_text, 1, wire_text),
                    suppress_log=True,
                )
                # 2) Small delay so the printer fully processes T
                #    before L arrives — pep uses 300 ms between queue
                #    steps; 100 ms is plenty over a LAN.
                time.sleep(0.1)
                # 3) QR bitmap packet — sized by the operator-tunable
                #    config knobs in Settings → Print Sizing. Bounded
                #    by MAX_DOTS regardless of config.
                from core.qr_builder import build_printer_bitmap
                # Effective QR cap = the LOWEST of:
                #   * MAX_DOTS                  — DC81 printhead ceiling (34)
                #   * QR_MAX_DOTS               — operator's QR cap in Settings
                #   * MSG_PRINTED_DOTS          — the message's vertical print area
                #
                # The third one is the fix: if we send a QR matrix
                # taller than MSG_PRINTED_DOTS, the printer scales the
                # logo vertically to fit, which deforms the QR finder
                # patterns and timing rows — the resulting print is
                # squished and unscannable. By capping the matrix at
                # MSG_PRINTED_DOTS up-front, the printer never touches
                # it and the QR comes out square. Same cap works for
                # both Font Shapes 16x11 and 19x14 — text height and
                # QR size are now both driven by MSG_PRINTED_DOTS.
                msg_print_dots = int(getattr(config_app, "MSG_PRINTED_DOTS", MAX_DOTS))
                qr_max = max(1, min(
                    MAX_DOTS,
                    int(getattr(config_app, "QR_MAX_DOTS", MAX_DOTS)),
                    msg_print_dots if msg_print_dots > 0 else MAX_DOTS,
                ))
                qr_border = max(0, int(getattr(config_app, "QR_BORDER", 2)))
                qr_ecc = getattr(config_app, "QR_ERROR_CORRECTION", "L")
                # Standard QR's smallest version is 21x21 — if the
                # operator's print area is below that, no QR can fit
                # without distortion. Warn so they bump MSG_PRINTED_DOTS.
                if qr_max < 21:
                    self.log.emit(
                        f"QR print area ({qr_max} dots) is below the QR "
                        f"minimum of 21 — raise 'Printed Dots' in "
                        f"Settings -> Print Sizing for a scannable QR."
                    )
                w, h, bits = build_printer_bitmap(
                    qr_payload or " ",
                    max_size=qr_max,
                    border=qr_border,
                    error_correction=qr_ecc,
                )
                self._send_recv(
                    self._stamped(build_net_logo_packet, 1, w, h, bits),
                    suppress_log=True,
                )
            except Exception as e:
                self.log.emit(f"Push failed: {e}")
                return False

        # Emit a "pushed" pulse so the UI can flash a tiny indicator
        self.pushed.emit(qr_payload or "")
        return True

    # ----- packet senders for push_label -----
    def _send_typed(self, type_char: str, text: str):
        """Send a 'T'-style packet (Network Text field 1)."""
        self._send_raw(
            self._stamped(build_update_network_text, 1, text or " ")
        )

    def _build_logo_packet(self, logo_id: int, w: int, h: int, bits: str) -> bytes:
        return self._stamped(build_net_logo_packet, logo_id, w, h, bits)

    # ---- IO helpers ----
    def _read_tcp_frame(self, sock: socket.socket, timeout_s: float) -> bytes:
        """Read until a full RECV_STX…RECV_ETX frame arrives or `timeout_s`
        elapses. Mirrors pep._read_frame — needed because TCP can
        deliver the reply in multiple chunks."""
        rx = bytearray()
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                return bytes(rx)
            rx.extend(chunk)
            if len(rx) >= 4 and rx[0] == RECV_STX and rx[-1] == RECV_ETX:
                break
        return bytes(rx)

    def _mark_hit(self):
        self._consecutive_miss = 0
        if not self._status.two_way:
            self._status.two_way = True
            self._emit_status()

    def _mark_miss(self):
        self._consecutive_miss += 1
        if (
            self._status.two_way
            and self._consecutive_miss >= self.TWO_WAY_MISS_THRESHOLD
        ):
            self._status.two_way = False
            self._emit_status()
        # After enough misses, drop the TCP socket entirely so the next
        # command opens a fresh session + Check Activity, which is the
        # only reliable way to recover from a desynchronised printer
        # PKGID sequence or a stuck embedded TCP stack.
        if (
            self._sock is not None
            and self._consecutive_miss >= self.DROP_SOCKET_MISS_THRESHOLD
            and (config_app.PRINTER_PROTO or "TCP").upper() == "TCP"
        ):
            self.log.emit(
                f"Dropping stale TCP session after "
                f"{self._consecutive_miss} missed replies — will reconnect."
            )
            self._close_socket()

    def _send_recv_udp(self, pkt: bytes, timeout_s: float) -> bytes:
        """Fresh UDP socket per command — pep pattern. `recvfrom` accepts
        a reply from any source, which is what we need: some Dikai
        firmwares reply from an ephemeral port, and a `connect()`d UDP
        socket would silently drop those datagrams."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_s)
            sock.sendto(pkt, (config_app.PRINTER_IP, config_app.PRINTER_PORT))
            try:
                rx, _addr = sock.recvfrom(65535)
            except socket.timeout:
                rx = b""
            return rx

    def _send_recv_tcp(self, pkt: bytes, timeout_s: float) -> bytes:
        """Long-lived TCP socket with reconnect-on-stale, also pep
        pattern. If the printer closed the socket between commands, we
        reconnect once, send Check Activity to resync PKGID, then retry."""
        if self._sock is None:
            self._reopen_tcp_with_resync(timeout_s)
        try:
            self._sock.settimeout(timeout_s)
            self._sock.sendall(pkt)
        except OSError:
            # Stale socket — drop, reconnect+resync, retry sendall.
            self._close_socket()
            self._reopen_tcp_with_resync(timeout_s)
            self._sock.settimeout(timeout_s)
            self._sock.sendall(pkt)
        return self._read_tcp_frame(self._sock, timeout_s)

    def _reopen_tcp_with_resync(self, timeout_s: float):
        """Open a new TCP socket and immediately send Check Activity
        (PKGID 0) so the printer resets its expected-PKGID counter to
        match ours."""
        self._sock = socket.create_connection(
            (config_app.PRINTER_IP, config_app.PRINTER_PORT),
            timeout=timeout_s,
        )
        self._sock.settimeout(timeout_s)
        self._pkgid = 0
        try:
            self._sock.sendall(build_check_activity())
            # Drain the Check Activity reply (if any) so it doesn't get
            # mis-attributed to whatever command is about to be sent.
            _ = self._read_tcp_frame(self._sock, timeout_s)
        except Exception:
            pass

    def _send_recv(self,
                   pkt: bytes,
                   timeout: float | None = None,
                   suppress_log: bool = False) -> bytes | None:
        """Universal verb — used by EVERY command (read params, apply
        params, start/stop jet, push label, status query). Returns the
        printer's reply frame, or None on timeout/error. Updates
        `two_way` with hysteresis."""
        if not self._status.connected or self._status.simulated:
            return None
        t = timeout if timeout is not None else config_app.PRINTER_TIMEOUT
        proto = (config_app.PRINTER_PROTO or "TCP").upper()
        with self._io_lock:
            try:
                if proto == "UDP":
                    data = self._send_recv_udp(pkt, t)
                else:
                    data = self._send_recv_tcp(pkt, t)
            except OSError as e:
                if not suppress_log:
                    self.log.emit(f"Wire send/recv failed: {e}")
                # Drop the TCP socket so the next call cleanly reconnects.
                self._close_socket()
                return None
            except Exception as e:
                if not suppress_log:
                    self.log.emit(f"Wire send/recv failed: {e}")
                return None
        if data:
            self._mark_hit()
            return data
        self._mark_miss()
        if not suppress_log:
            self.log.emit("Printer did not reply within timeout.")
        return None

    def _send_raw(self, pkt: bytes):
        """Fire a packet at the printer and DISCARD any reply. We still
        go through send_recv so:
          * UDP gets its fresh socket (consistent path with everything else)
          * TCP drains the printer's reply so a subsequent command isn't
            confused by leftover bytes
        The reply is intentionally ignored; this is used for the T+L
        live-label push where the timing matters more than the ACK."""
        self._send_recv(pkt, suppress_log=True)

    def _advance_simulator(self):
        """Drive the simulated jet through its stages, tick consumables/counter."""
        st = self._status
        now = time.monotonic()

        if st.jet == JetState.HEATING:
            elapsed = now - self._stage_started_at
            st.stage_progress = min(100, int(100 * elapsed / config_app.JET_HEATING_SECONDS))
            if elapsed >= config_app.JET_HEATING_SECONDS:
                st.jet = JetState.PURGING
                st.stage_progress = 0
                self._stage_started_at = now
                self.log.emit("Printhead at temperature — purging nozzle.")

        elif st.jet == JetState.PURGING:
            elapsed = now - self._stage_started_at
            st.stage_progress = min(100, int(100 * elapsed / config_app.JET_PURGING_SECONDS))
            if elapsed >= config_app.JET_PURGING_SECONDS:
                st.jet = JetState.READY
                st.stage_progress = 100
                self.log.emit("Jet ready — printer will auto-print on each carton trigger.")

        elif st.jet == JetState.STOPPING:
            elapsed = now - self._stage_started_at
            if elapsed >= 1.0:
                st.jet = JetState.STOPPED
                st.stage_progress = 0
                self.log.emit("Jet stopped.")

        # Consumables drift only while the jet is doing real work
        if st.jet in (JetState.READY, JetState.HEATING, JetState.PURGING):
            if random.random() < 0.06:
                st.ink_level = max(0, st.ink_level - 1)
            if random.random() < 0.05:
                st.sol_level = max(0, st.sol_level - 1)
        st.ink_ok = st.ink_level > 10
        st.sol_ok = st.sol_level > 10

        # Fault detection (consumables, etc.)
        st.fault = ""
        if not st.ink_ok:
            st.fault = "INK LOW"
        elif not st.sol_ok:
            st.fault = "SOLVENT LOW"

        # Simulated auto-print events only when BOTH jet is READY and print
        # is enabled — matches real DC81 behaviour (Jet running + Print on).
        if (
            st.jet == JetState.READY
            and st.print_enabled
            and self._pending_qr_payload
        ):
            # ~1 print every 4–6 seconds (production line cadence)
            if random.random() < 0.18:
                st.counter += 1
                self.print_event.emit(st.counter)

    # Real-mode status poll cadence — how many 'S' polls per poll-thread
    # tick. ONE means "poll every tick"; combined with the default
    # POLL_INTERVAL_SECONDS = 1.0 this gives 1 Hz live updates of jet
    # state, fault word, ink/solvent levels, and the print counter.
    # During jet start/stop the poll thread tightens to ~10 Hz on its
    # own (see _PollThread.run), so transitions resolve within ~100 ms.
    REAL_STATUS_POLL_TICKS = 1

    def _poll_once(self):
        """Background tick.

        Simulator path: drive the fake jet state machine.
        Real path: every REAL_STATUS_POLL_TICKS ticks, send 'S' to
            refresh counter / fluid / jet state. The poll skips itself
            if the IO lock is busy (a user command is in flight), so
            operator actions are never blocked behind a status poll."""
        if not self._status.connected:
            return
        if self._status.simulated:
            self._advance_simulator()
            self._emit_status()
            return
        # Real mode — slow 'S' poll
        self._poll_tick = getattr(self, "_poll_tick", 0) + 1
        if self._poll_tick % self.REAL_STATUS_POLL_TICKS != 0:
            return
        # Skip if the IO lock is busy — a user-initiated command is in
        # progress. Trying to acquire would queue us behind it and the
        # whole poll thread would block, defeating the responsiveness
        # win. acquire(blocking=False) is the safe non-contending probe.
        got = self._io_lock.acquire(blocking=False)
        if not got:
            return
        try:
            pass
        finally:
            self._io_lock.release()
        # We didn't actually do the work under the lock — request_status
        # acquires it again. We only used acquire/release as a probe.
        self.request_status()
        self._emit_status()

    def _emit_status(self):
        s = self._status
        snap = PrinterStatus(
            connected=s.connected,
            simulated=s.simulated,
            two_way=s.two_way,
            jet=s.jet,
            print_enabled=s.print_enabled,
            stage_progress=s.stage_progress,
            ink_ok=s.ink_ok, sol_ok=s.sol_ok,
            ink_level=s.ink_level, sol_level=s.sol_level,
            counter=s.counter,
            fault=s.fault,
            faults=list(s.faults),
        )
        self.status_changed.emit(snap)


class _PollThread(QThread):
    def __init__(self, link: PrinterLink, interval_s: float, stop_evt: threading.Event):
        super().__init__()
        self._link = link
        self._interval = interval_s
        self._stop = stop_evt

    def run(self):
        # Tighter loop while jet is starting/stopping so the progress bar updates smoothly
        while not self._stop.is_set():
            jet = self._link._status.jet
            if jet.is_starting or jet == JetState.STOPPING:
                period = 0.10
            else:
                period = self._interval
            self._link._poll_once()
            self._stop.wait(period)
