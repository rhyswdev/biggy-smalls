"""`make app`'s first step (docs/decisions/ui-build.md section 5): check that the app's ports are free before
anything starts. A port is taken when something accepts a connection on it, over IPv4 or IPv6 loopback. When
one is, it says which, and the variable that picks another.

    python -m smalls_data.ports SMALLS_WEB_PORT=7317 SMALLS_API_PORT=7318
"""

from __future__ import annotations

import socket
import sys


def taken(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:         # no IPv6 loopback on this machine
            continue
    return False


def problems(wanted: list[tuple[str, int]]) -> list[str]:
    return [f"port {port} is taken: something is already listening on it. Stop it, or choose another port: "
            f"make app {name}={port + 100}" for name, port in wanted if taken(port)]


def main(argv: list[str]) -> int:
    wanted = []
    for arg in argv:
        name, _, value = arg.partition("=")
        if not name or not value.isdigit():
            print(f"ports: expected NAME=PORT, not {arg!r}", file=sys.stderr)
            return 2
        wanted.append((name, int(value)))
    found = problems(wanted)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
