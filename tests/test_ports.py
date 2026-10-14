"""Row R16: `make app` checks both ports before it starts anything. With something listening on the web port it
stops at once, naming the port and SMALLS_WEB_PORT."""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from smalls_data import ports

ROOT = Path(__file__).resolve().parent.parent


def listening() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def free() -> int:
    """A port the check itself finds free: the system's pick for IPv4 can be one a service holds on IPv6."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if not ports.taken(port):
            return port
    raise RuntimeError("no free port found")


def test_a_listening_port_is_taken_and_a_free_one_is_not():
    with listening() as s:
        port = s.getsockname()[1]
        assert ports.taken(port)
        found = ports.problems([("SMALLS_WEB_PORT", port), ("SMALLS_API_PORT", free())])
        assert len(found) == 1 and f"port {port} is taken" in found[0] and "SMALLS_WEB_PORT=" in found[0]
    assert ports.main(["SMALLS_WEB_PORT=nope"]) == 2


def test_make_app_stops_before_starting_anything_when_the_web_port_is_taken():
    with listening() as s:
        port, api = s.getsockname()[1], free()
        started = time.monotonic()
        done = subprocess.run(["make", "--no-print-directory", "app", f"SMALLS_WEB_PORT={port}", f"SMALLS_API_PORT={api}"],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert done.returncode != 0
        assert f"port {port} is taken" in done.stderr and "SMALLS_WEB_PORT" in done.stderr
        assert "Smalls: http://" not in done.stdout                    # nothing was started
        assert not ports.taken(api) and time.monotonic() - started < 60
