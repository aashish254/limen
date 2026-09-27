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

# Captured before any fixture can replace it, so a test can ask for the shipped lookup.
from subproto import graph as _graph                      # noqa: E402

AMBIENT_INDEX_AS_SHIPPED = _graph.ambient


@pytest.fixture(autouse=True)
def host_keeps_its_settings_out_of_the_test_room(monkeypatch):
    for name in OURS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def test_room_has_no_ambient_repository(monkeypatch):
    """Nothing in the working directory decides which slots answer.

    `subproto graph <repo> --install` leaves a `.subproto-graph.json` behind, and the
    command layer reads that file as "this repo has an index" — so a contributor who had
    indexed their own checkout ran a different suite than CI did, and two of those tests
    were red rather than merely different. A test that wants an index hands one over.
    """
    from subproto import graph
    monkeypatch.setattr(graph, "ambient", lambda root=None: None)


@pytest.fixture
def ambient_index_as_shipped(monkeypatch):
    """Hand one test back the real lookup, because that lookup is what it is about."""
    from subproto import graph
    monkeypatch.setattr(graph, "ambient", AMBIENT_INDEX_AS_SHIPPED)


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
