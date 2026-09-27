"""Regression: `subproto demo` must share a data dir with the rest of the CLI.

A first-time user sets SUBPROTO_HOME (the documented override) and runs
`subproto demo --slots` then `subproto report`. cmd_demo used to hard-code
`/tmp/subproto-demo` unless `--home` was passed, so report (which resolves data_dir
through Config, honouring SUBPROTO_HOME) read an empty DB and showed 0 requests.
"""

import json
import re
import socket
import threading
import time


_REPLAYED = re.compile(r"replayed (\d+) synthetic agent requests")
_SANDBOX = re.compile(r"^sandbox: (.+)$", re.M)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _report_count(capsys, args):
    from subproto import cli
    capsys.readouterr()
    assert cli.main(args) == 0
    return json.loads(capsys.readouterr().out)["totals"]["n"]


def test_demo_honours_subproto_home_env(tmp_path, monkeypatch, capsys):
    from subproto import cli

    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    monkeypatch.delenv("LAYA_URL", raising=False)
    monkeypatch.setenv("SUBPROTO_HOME", str(tmp_path))
    monkeypatch.setenv("SUBPROTO_PORT", str(free_port()))

    assert cli.main(["demo", "--slots", "--port", str(free_port())]) in (None, 0)
    page = capsys.readouterr().out
    assert (tmp_path / "telemetry.db").exists(), \
        "demo must write into SUBPROTO_HOME, not a hard-coded /tmp sandbox"
    # report, using the same env, sees the traffic the demo generated — and the same
    # amount of it the demo's own page claimed to have replayed
    assert _report_count(capsys, ["report", "--json"]) == int(
        _REPLAYED.search(page).group(1)), \
        "the page and the database it wrote count different traffic"


def test_demo_explicit_home_still_wins(tmp_path, monkeypatch, capsys):
    """--home overrides SUBPROTO_HOME for both commands, consistently."""
    from subproto import cli

    home = tmp_path / "chosen"
    monkeypatch.setenv("SUBPROTO_HOME", str(tmp_path / "ignored"))
    monkeypatch.setenv("SUBPROTO_MODEL", "heuristic")

    assert cli.main(["demo", "--home", str(home), "--port", str(free_port())]) in (None, 0)
    assert (home / "telemetry.db").exists()
    assert not (tmp_path / "ignored").exists()


def test_demo_default_is_an_isolated_sandbox(tmp_path, monkeypatch, capsys, request):
    """With neither --home nor SUBPROTO_HOME, demo isolates in a fresh temp dir.

    A fixed `/tmp/subproto-demo` looked tidy and was still a collision: two demos
    on one machine — or one demo run twice to check a claim — wrote into the same
    SQLite file, so the second one's counts included the first one's traffic.
    Because the dir is chosen per run, the page has to name it: a result the reader
    cannot find is a result they cannot re-read.
    """
    from subproto import cli
    import os
    import shutil

    monkeypatch.delenv("SUBPROTO_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # ~/.subproto must stay clean
    monkeypatch.setenv("SUBPROTO_MODEL", "heuristic")

    def run_and_say_where():
        assert cli.main(["demo", "--port", str(free_port())]) in (None, 0)
        out = capsys.readouterr().out
        assert int(_REPLAYED.search(out).group(1)) > 0, out
        chosen = _SANDBOX.search(out)
        assert chosen, "the demo isolated itself somewhere and never said where"
        home = chosen.group(1)
        # the demo leaves its sandbox for the reader; this test may not
        request.addfinalizer(lambda: shutil.rmtree(home, ignore_errors=True))
        return home

    first = run_and_say_where()
    assert os.path.basename(first).startswith("subproto-demo-"), first
    assert not first.startswith(str(tmp_path)), "a sandbox must not be the user's home"
    assert os.path.exists(os.path.join(first, "telemetry.db")), first
    assert not (tmp_path / ".subproto").exists()

    second = run_and_say_where()
    assert second != first, "one run, one sandbox: %s then %s" % (first, second)


def test_settled_waits_for_the_row_that_is_still_on_its_way(tmp_path):
    """`demo.settled` is the demo's promise that its page counts what it replayed.

    The write lands after the response does, so the wait is the difference between
    a report that says 17 and a table that adds up to 16. Tested against a real
    late writer rather than a mocked clock, because the thing being verified is the
    polling itself.
    """
    from subproto import demo
    from subproto.telemetry import Telemetry

    tel = Telemetry(str(tmp_path / "telemetry.db"))
    row = {"ts": time.time(), "api": "anthropic", "path": "/v1/messages", "client": "t",
           "model": "claude-test", "status": 200, "stream": 1, "in_tok": 10,
           "cr_tok": 0, "out_tok": 5, "cost_usd": 0.001, "usage_source": "anthropic"}
    threading.Thread(target=lambda: (time.sleep(0.15), tel.record(row)), daemon=True).start()
    assert demo.settled(tel, 1) == 1, "it gave up before the write it was waiting for"
    # and it stops: a database that will never reach `n` is answered with what there is
    assert demo.settled(tel, 9, timeout=0.2) == 1
    tel.close()


def test_demo_survives_a_busy_mock_port(tmp_path, monkeypatch, capsys):
    """`port + 1` belongs to the mock upstream by convention, not by right.

    Something else holding that port is an ordinary machine, so the demo takes any
    free port for its own mock and still answers with a full report.
    """
    from subproto import cli

    monkeypatch.delenv("SUBPROTO_HOME", raising=False)
    monkeypatch.setenv("SUBPROTO_MODEL", "heuristic")
    home = tmp_path / "home"
    # a kernel-chosen port whose neighbour is *also* free — otherwise this test is
    # squatting on a port nobody offered it, and the demo's own fallback hides that
    for proxy in (free_port() for _ in range(20)):
        squatter = socket.socket()
        try:
            squatter.bind(("127.0.0.1", proxy + 1))
            break
        except OSError:
            squatter.close()
    else:
        raise AssertionError("every port's neighbour on this machine is taken")
    squatter.listen(8)
    try:
        assert cli.main(["demo", "--home", str(home), "--port", str(proxy)]) in (None, 0)
    finally:
        squatter.close()
    assert int(_REPLAYED.search(capsys.readouterr().out).group(1)) > 0
    assert (home / "telemetry.db").exists()
