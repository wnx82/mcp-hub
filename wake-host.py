#!/usr/bin/env python3
"""Send a Wake-on-LAN magic packet to a host declared in hosts.yaml.

usage: wake-host.py <host-name>
"""
import socket
import sys

import config

PORTS = (9, 7)


def magic_packet(mac: str) -> bytes:
    clean = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(clean) != 12:
        raise ValueError(f"invalid MAC: {mac!r}")
    return bytes.fromhex("FF" * 6 + clean * 16)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: wake-host.py <host-name>", file=sys.stderr)
        return 2

    name = sys.argv[1]
    hosts = config.load_hosts()

    if name not in hosts:
        wakeable = sorted(h for h, c in hosts.items() if c.get("mac"))
        print(f"unknown host: {name}", file=sys.stderr)
        print(f"hosts with a declared MAC: {', '.join(wakeable) or '(none)'}",
              file=sys.stderr)
        return 2

    mac = hosts[name].get("mac")
    if not mac:
        print(f"no MAC declared for {name} in {config.HOSTS_FILE}", file=sys.stderr)
        return 2

    packet = magic_packet(mac)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for port in PORTS:
            sock.sendto(packet, (config.WOL_BROADCAST, port))
    finally:
        sock.close()

    ports = "/".join(str(p) for p in PORTS)
    print(f"magic packet sent to {name} ({mac}) via {config.WOL_BROADCAST}:{ports}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
