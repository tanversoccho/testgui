"""Round-trip verification for the Dikai printer link.

Covers:
  * Outgoing packet bytes for E (params), C (control), S (status).
  * Reply parsing for E, C, S.
  * End-to-end through PrinterLink on UDP AND TCP, with fakes injected
    via monkey-patching `socket.socket` / `socket.create_connection` so
    we exercise the same fresh-socket-per-call (UDP) and persistent
    socket (TCP) paths that production uses.
  * Hysteresis: a single missed reply must NOT flip two_way; threshold
    misses must.
"""
import os
import sys
import socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from config import config_app
from core import printer_link as PL
from core.printer_link import (
    PrinterLink, JetState,
    build_edit_system_params, build_control_command, build_request_status,
    parse_edit_params_reply, parse_status_reply,
    SEND_STX, SEND_ETX, RECV_STX, RECV_ETX,
    RNWD_STOPPED, RNWD_STARTING, RNWD_READY, RNWD_STOPPING,
)


# ---------- module-level frame helpers ----------

def test_outgoing_query_bytes():
    pkt = build_edit_system_params(pkgid=17, print_enable=True)
    assert pkt[0]  == SEND_STX
    assert pkt[1]  == 17
    assert pkt[2]  == ord("E")
    assert pkt[3]  == ord("1")
    assert pkt[4:17] == b"0000000000000"
    assert pkt[-1] == SEND_ETX
    print("  E query bytes OK", pkt.hex())


def test_parse_synthetic_reply():
    body = b"1" + b"27" + b"305" + b"290" + b"42" + b"160"
    reply = bytes([RECV_STX, 0x55, ord("E")]) + body + bytes([RECV_ETX])
    parsed = parse_edit_params_reply(reply)
    assert parsed["modulation"]   == 27
    assert parsed["viscosity"]    == 305
    assert parsed["ink_pressure"] == 290
    assert parsed["nozzle_temp"]  == 42
    assert parsed["charge_value"] == 160
    print("  E reply parses OK")


def test_parse_rejects_garbage():
    assert parse_edit_params_reply(b"") is None
    assert parse_edit_params_reply(b"\x99\x01E1000000000000\x10") is None
    assert parse_edit_params_reply(b"\x01\x01X1000000000000\x10") is None
    assert parse_edit_params_reply(b"\x01\x01E000\x10") is None
    print("  malformed E replies rejected")


def test_status_reply_parse():
    data = (
        bytes([RNWD_READY])
        + b"300" + b"280" + b"01" + b"02"
        + b"040" + b"038" + b"030"
        + bytes([0x02])
        + b"075" + b"060"
        + (0).to_bytes(4, "big")
        + b"0000004210"
    )
    reply = bytes([RECV_STX, 0x44, ord("S")]) + data + bytes([RECV_ETX])
    p = parse_status_reply(reply)
    assert p is not None
    assert p["rnwd"]         == RNWD_READY
    assert p["ink_pressure"] == 300
    assert p["viscosity"]    == 280
    assert p["ink_pct"]      == 75
    assert p["sol_pct"]      == 60
    assert p["fault_word"]   == 0
    assert p["print_count"]  == 4210
    print("  S reply parses OK")


def test_control_packet_bytes():
    pkt = build_control_command(pkgid=42, start_jet=True)
    assert pkt[3:6] == b"100"
    pkt = build_control_command(pkgid=42, stop_jet=True)
    assert pkt[3:6] == b"010"
    pkt = build_control_command(pkgid=42, reset_counter=True)
    assert pkt[3:6] == b"001"
    print("  C packet bytes OK")


def test_request_status_bytes():
    pkt = build_request_status(pkgid=99)
    assert pkt[0] == SEND_STX
    assert pkt[2] == ord("S")
    assert pkt[-1] == SEND_ETX
    print("  S query bytes OK")


# ---------- reply builders ----------

def _e_reply(pkgid=0x10, pte=True, mod=33, vsc=410, prs=275, ptt=50, chg=180):
    body = (b"1" if pte else b"0") + f"{mod:02d}{vsc:03d}{prs:03d}{ptt:02d}{chg:03d}".encode("ascii")
    return bytes([RECV_STX, pkgid, ord("E")]) + body + bytes([RECV_ETX])


def _c_reply(pkgid=0x10, start=False, stop=False, reset=False):
    body = (b"1" if start else b"0") + (b"1" if stop else b"0") + (b"1" if reset else b"0")
    return bytes([RECV_STX, pkgid, ord("C")]) + body + bytes([RECV_ETX])


def _s_reply(pkgid=0x10, rnwd=RNWD_READY, count=100, fault_word=0):
    data = (
        bytes([rnwd]) + b"300" + b"280" + b"01" + b"02"
        + b"040" + b"038" + b"030" + bytes([0x02])
        + b"075" + b"060"
        + fault_word.to_bytes(4, "big")
        + f"{count:010d}".encode("ascii")
    )
    return bytes([RECV_STX, pkgid, ord("S")]) + data + bytes([RECV_ETX])


# ---------- fake sockets ----------

class _Replies:
    """Shared reply queue + never_replies flag — same instance is used
    across multiple FakeUDP instances since UDP creates a fresh socket
    per command."""
    def __init__(self):
        self.queue = []
        self.never = False
        self.sent_packets = []     # every packet sent on this run


class FakeUDP:
    """Imitates a socket.socket(AF_INET, SOCK_DGRAM) instance for one
    send/recv round-trip. Matches the API _send_recv_udp uses."""
    type = socket.SOCK_DGRAM
    def __init__(self, replies: _Replies):
        self._replies = replies
        self._timeout = 1.0
    def settimeout(self, t):     self._timeout = t
    def gettimeout(self):        return self._timeout
    def sendto(self, data, _addr):
        self._replies.sent_packets.append(bytes(data))
        return len(data)
    def recvfrom(self, n):
        if self._replies.never or not self._replies.queue:
            raise socket.timeout()
        return self._replies.queue.pop(0), ("printer", 4916)
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class FakeTCP:
    """Imitates a TCP socket — sendall + recv (multi-chunk reader)."""
    type = socket.SOCK_STREAM
    def __init__(self, replies: _Replies):
        self._replies = replies
        self._timeout = 1.0
        self._rx_buf = b""
    def settimeout(self, t):     self._timeout = t
    def gettimeout(self):        return self._timeout
    def sendall(self, data):
        self._replies.sent_packets.append(bytes(data))
    def recv(self, n):
        if self._replies.never:
            raise socket.timeout()
        if not self._rx_buf:
            if not self._replies.queue:
                raise socket.timeout()
            self._rx_buf = self._replies.queue.pop(0)
        chunk, self._rx_buf = self._rx_buf[:n], self._rx_buf[n:]
        return chunk
    def close(self): pass


def install_udp_fake(replies: _Replies, monkey_target=PL.socket):
    """Patch socket.socket inside core.printer_link to hand out FakeUDP."""
    def _factory(family=None, type_=None, *a, **kw):
        return FakeUDP(replies)
    monkey_target.socket = _factory


def install_tcp_fake(replies: _Replies):
    """Patch socket.create_connection inside core.printer_link to hand
    out a FakeTCP. _send_recv_tcp only uses create_connection."""
    def _factory(addr, timeout=None):
        f = FakeTCP(replies)
        f.settimeout(timeout or 2.0)
        return f
    PL.socket.create_connection = _factory


def restore_socket():
    """Undo our monkey-patching of the real socket module."""
    import importlib
    importlib.reload(socket)
    PL.socket.socket = socket.socket
    PL.socket.create_connection = socket.create_connection


def make_link():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    link = PrinterLink()
    link._status.connected = True
    link._status.simulated = False
    link._status.two_way   = True
    link._consecutive_miss = 0
    return link


# ---------- per-mode command exercises ----------

def _exercise(mode_name, install_fn, queue_setup):
    print(f"\n--- {mode_name} ---")
    config_app.PRINTER_PROTO = mode_name
    config_app.PRINTER_IP    = "127.0.0.1"      # never reached, fakes intercept

    replies = _Replies()
    install_fn(replies)
    link = make_link()

    # TCP path auto-syncs PKGID on first connect by sending Check
    # Activity and draining the reply — give the fake a Check Activity
    # reply to consume so it doesn't steal the first E reply.
    if mode_name == "TCP":
        a_reply = bytes([RECV_STX, 0x00, ord("A"),
                         0x00, 0x01, 0x01, 0x05,
                         ord("D"), ord("C"), ord("8"), ord("0"),
                         RECV_ETX])
        replies.queue.append(a_reply)

    # read_system_parameters
    replies.queue.append(_e_reply())
    params = link.read_system_parameters()
    assert params is not None,             "read_system_parameters returned None"
    assert params["modulation"] == 33,     params
    print("  read_system_parameters: OK")

    # apply_system_parameters — printer echoes new values
    replies.queue.append(_e_reply(mod=44, vsc=420, prs=285, ptt=55, chg=190))
    ok = link.apply_system_parameters(
        print_enable=True, modulation=44, viscosity=420,
        ink_pressure=285, nozzle_temp=55, charge_value=190,
    )
    assert ok
    assert config_app.MODULATION_SET == 44
    print("  apply_system_parameters: OK")

    # start_jet
    replies.queue.append(_c_reply(start=True))
    assert link.start_jet() is True
    assert link.status.jet == JetState.HEATING
    print("  start_jet: OK")

    # request_status (also driven by read_print_count fallback)
    replies.queue.append(_s_reply(rnwd=RNWD_READY, count=4242))
    parsed = link.request_status()
    assert parsed is not None
    assert link.status.jet == JetState.READY
    assert link.status.counter == 4242
    print("  request_status: OK")

    # set_print_enabled
    replies.queue.append(_e_reply(pte=True))
    assert link.set_print_enabled(True) is True
    assert link.status.print_enabled is True
    print("  set_print_enabled: OK")

    # stop_jet
    replies.queue.append(_c_reply(stop=True))
    assert link.stop_jet() is True
    assert link.status.jet == JetState.STOPPING
    print("  stop_jet: OK")

    # reset_counter
    replies.queue.append(_c_reply(reset=True))
    assert link.reset_counter() is True
    assert link.status.counter == 0
    print("  reset_counter: OK")

    return replies


def test_udp_commands():
    replies = _exercise("UDP", install_udp_fake, None)
    # UDP must NOT have used link._sock; we expect every packet to have
    # been sent through a fresh socket.
    assert len(replies.sent_packets) >= 7, "expected at least 7 sent packets"
    for p in replies.sent_packets:
        assert p[0] == SEND_STX and p[-1] == SEND_ETX, p.hex()


def test_tcp_commands():
    _exercise("TCP", install_tcp_fake, None)


# ---------- hysteresis ----------

def test_two_way_hysteresis():
    config_app.PRINTER_PROTO = "UDP"
    replies = _Replies()
    replies.never = True
    install_udp_fake(replies)
    link = make_link()

    link.read_system_parameters()
    assert link.status.two_way is True, "responding after 1 miss"
    link.read_system_parameters()
    assert link.status.two_way is True, "responding after 2 misses"
    link.read_system_parameters()
    assert link.status.two_way is False, "1-way after 3 misses"
    print("  hysteresis OK — 1-way after 3 misses")

    replies.never = False
    replies.queue.append(_e_reply())
    link.read_system_parameters()
    assert link.status.two_way is True, "back to responding on hit"
    print("  hysteresis OK — back to responding on first hit")


def main():
    tests = [
        test_outgoing_query_bytes,
        test_parse_synthetic_reply,
        test_parse_rejects_garbage,
        test_status_reply_parse,
        test_control_packet_bytes,
        test_request_status_bytes,
        test_udp_commands,
        test_tcp_commands,
        test_two_way_hysteresis,
    ]
    for t in tests:
        print(t.__name__)
        t()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
