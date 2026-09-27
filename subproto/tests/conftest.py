"""Shared test-room setup: the suite runs against this repo, not against a shell.

`subproto` reads twenty-odd `SUBPROTO_*`/`LAYA_*` variables through `Config`, and a
developer who has `SUBPROTO_MODEL` pointed at a live backend for their own work would
otherwise change which adapter these tests exercise — a green suite on their machine
would not be the suite CI ran.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Everything this project reads from the environment, whatever the host set it to.
OURS = tuple(v for v in os.environ
             if v.startswith("SUBPROTO_") or v.startswith("LAYA_"))


@pytest.fixture(autouse=True)
def host_keeps_its_settings_out_of_the_test_room(monkeypatch):
    for name in OURS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settled():
    """Wait for the proxy's own writes instead of waiting a fixed amount of time.

    A request is answered on the client's socket before its telemetry row is
    committed, so a test that sleeps 0.2 s is a test that passes on an idle laptop
    and fails on a loaded runner — which is exactly the flake a stranger sees and
    reports as a bug. This asks the database until it says enough, and names the
    number it was waiting for when it does not.
    """
    def wait(tel, n, timeout=15.0):
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            rows = tel.query("SELECT COUNT(*) AS n FROM requests")
            seen = rows[0]["n"] if rows else 0
            if seen >= n:
                return seen
            time.sleep(0.02)
        raise AssertionError("telemetry settled at %d of %d rows in %gs"
                             % (seen, n, timeout))
    return wait
