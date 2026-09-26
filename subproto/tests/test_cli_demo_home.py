"""Regression: `subproto demo` must share a data dir with the rest of the CLI.

A first-time user sets SUBPROTO_HOME (the documented override) and runs
`subproto demo --slots` then `subproto report`. cmd_demo used to hard-code
`/tmp/subproto-demo` unless `--home` was passed, so report (which resolves data_dir
through Config, honouring SUBPROTO_HOME) read an empty DB and showed 0 requests.
"""

import json
import socket
import time


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
    assert (tmp_path / "telemetry.db").exists(), \
        "demo must write into SUBPROTO_HOME, not a hard-coded /tmp sandbox"
    # report, using the same env, sees the traffic the demo generated
    assert _report_count(capsys, ["report", "--json"]) > 0


def test_demo_explicit_home_still_wins(tmp_path, monkeypatch, capsys):
    """--home overrides SUBPROTO_HOME for both commands, consistently."""
    from subproto import cli

    home = tmp_path / "chosen"
    monkeypatch.setenv("SUBPROTO_HOME", str(tmp_path / "ignored"))
    monkeypatch.setenv("SUBPROTO_MODEL", "heuristic")

    assert cli.main(["demo", "--home", str(home), "--port", str(free_port())]) in (None, 0)
    assert (home / "telemetry.db").exists()
    assert not (tmp_path / "ignored").exists()


def test_demo_default_is_an_isolated_sandbox(tmp_path, monkeypatch, capsys):
    """With neither --home nor SUBPROTO_HOME, demo stays in /tmp/subproto-demo."""
    from subproto import cli
    import os

    monkeypatch.delenv("SUBPROTO_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # ~/.subproto must stay clean
    monkeypatch.setenv("SUBPROTO_MODEL", "heuristic")
    sandbox = "/tmp/subproto-demo"

    assert cli.main(["demo", "--port", str(free_port())]) in (None, 0)
    assert os.path.exists(os.path.join(sandbox, "telemetry.db"))
    assert not (tmp_path / ".subproto").exists()
