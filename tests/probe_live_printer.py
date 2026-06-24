"""Live probe against the real printer at 192.168.1.110.

Sends a short Check Activity over TCP and UDP and dumps whatever the
printer replies. This is a *sanity check* — it doesn't change any
printer state. Safe to run on production hardware.

Run from the project root:
    python tests/probe_live_printer.py
"""
import os
import sys
import socket
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from core.printer_link import (
    build_check_activity, build_request_status, build_edit_system_params,
    parse_edit_params_reply, parse_status_reply,
    SEND_STX, SEND_ETX, RECV_STX, RECV_ETX,
)

IP   = "192.168.1.110"
PORT = 4916
TIMEOUT = 3.0


def hex_dump(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def probe_tcp():
    print("\n=== TCP probe ===")
    try:
        with socket.create_connection((IP, PORT), timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            # 1) Check Activity (PKGID 0) — resets the printer's sequence
            pkt = build_check_activity()
            print(f"-> A (Check Activity)   {hex_dump(pkt)}")
            sock.sendall(pkt)
            rx = _read_frame(sock, TIMEOUT)
            print(f"<- {len(rx)}B  {hex_dump(rx) if rx else '(no reply)'}")

            # 2) Request Status
            pkt = build_request_status(1)
            print(f"-> S (Request Status)   {hex_dump(pkt)}")
            sock.sendall(pkt)
            rx = _read_frame(sock, TIMEOUT)
            print(f"<- {len(rx)}B  {hex_dump(rx) if rx else '(no reply)'}")
            if rx:
                parsed = parse_status_reply(rx)
                print(f"   parsed status: {parsed}")

            # 3) Read System Parameters (numeric=0 => echo current)
            pkt = build_edit_system_params(2, print_enable=True)
            print(f"-> E (Read SysParams)   {hex_dump(pkt)}")
            sock.sendall(pkt)
            rx = _read_frame(sock, TIMEOUT)
            print(f"<- {len(rx)}B  {hex_dump(rx) if rx else '(no reply)'}")
            if rx:
                parsed = parse_edit_params_reply(rx)
                print(f"   parsed params: {parsed}")
    except OSError as e:
        print(f"TCP probe failed: {e}")


def probe_udp():
    print("\n=== UDP probe ===")
    # Fresh socket per command — pep pattern
    for label, pkt, parse in [
        ("A (Check Activity)", build_check_activity(),                 None),
        ("S (Request Status)", build_request_status(1),                parse_status_reply),
        ("E (Read SysParams)", build_edit_system_params(2, print_enable=True),
                                                                       parse_edit_params_reply),
    ]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(TIMEOUT)
            print(f"-> {label}   {hex_dump(pkt)}")
            try:
                s.sendto(pkt, (IP, PORT))
            except OSError as e:
                print(f"   sendto failed: {e}")
                continue
            try:
                rx, src = s.recvfrom(65535)
                print(f"<- {len(rx)}B  from {src}  {hex_dump(rx)}")
                if parse:
                    print(f"   parsed: {parse(rx)}")
            except socket.timeout:
                print("<- (timeout)")


def _read_frame(sock: socket.socket, timeout_s: float) -> bytes:
    rx = bytearray()
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        rx.extend(chunk)
        if len(rx) >= 4 and rx[0] == RECV_STX and rx[-1] == RECV_ETX:
            break
    return bytes(rx)


def ping():
    print(f"\n=== ICMP ping {IP} (3 packets) ===")
    code = os.system(f"ping -n 3 -w 1500 {IP}")
    print(f"ping exit code: {code}")


if __name__ == "__main__":
    ping()
    probe_tcp()
    probe_udp()
