#!/usr/bin/env python3
"""Harmless reachability check: RakNet *unconnected ping* (what a client's server list sends). No login, no join.

Usage: raknet_ping.py HOST:PORT [tries]
Prints only round-trip times and the protocol number from the pong; never the address or the MOTD.
Exit 0 when at least one pong arrived.
"""
import os
import socket
import struct
import sys
import time

MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def ping(host: str, port: int, timeout: float = 2.0):
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    family, _, _, _, addr = infos[0]
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.monotonic()
    packet = b"\x01" + struct.pack(">q", int(time.time() * 1000)) + MAGIC + os.urandom(8)
    s.sendto(packet, addr)
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:
        return None
    finally:
        s.close()
    rtt = (time.monotonic() - t0) * 1000
    if not data or data[0] != 0x1C or MAGIC not in data:
        return {"rtt_ms": round(rtt, 1), "valid": False}
    # 0x1c | time(8) | server guid(8) | magic(16) | strlen(2) | "MCPE;motd;protocol;version;online;max;..."
    try:
        text = data[35:35 + struct.unpack(">H", data[33:35])[0]].decode("utf-8", "replace")
        fields = text.split(";")
        protocol = fields[2] if len(fields) > 2 else "?"
        edition = fields[0] if fields else "?"
    except Exception:
        protocol, edition = "?", "?"
    return {"rtt_ms": round(rtt, 1), "valid": True, "edition": edition, "protocol": protocol}


def main() -> int:
    target = sys.argv[1]
    tries = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    host, _, port = target.rpartition(":")
    ok = 0
    for i in range(tries):
        try:
            r = ping(host, int(port))
        except OSError as e:
            print(f"try {i + 1}: socket error {type(e).__name__}")
            r = None
        if r is None:
            print(f"try {i + 1}: no pong (timeout)")
        else:
            ok += 1 if r["valid"] else 0
            print(f"try {i + 1}: " + " ".join(f"{k}={v}" for k, v in r.items()))
        time.sleep(1)
    print(f"pongs: {ok}/{tries}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
