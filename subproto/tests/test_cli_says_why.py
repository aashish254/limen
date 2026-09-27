"""S46 — a surface has to say what it actually did.

Every test here is one answer a first-time-user run got wrong in a different way: a
banner that read the environment instead of the engine (M1), a `--model` name that
resolved to nothing and printed nothing (m8), a date the parser refused without
saying what it wanted (M3), a `--json` payload that could not tell "no such request"
from "a request with no decisions" (m4), and `--path` failing at a file the reader
never typed (m2).

`subproto up` normally blocks in `serve_forever`, so these hand it a stub: the banner
is the product under test, and it is printed before the block.
"""

import json
import re
import socket

import pytest

from subproto import cli


class _StubServer:
    def serve_forever(self):
        pass

    def shutdown(self):
        pass


@pytest.fixture
def banner(monkeypatch, capsys):
    """Run `subproto up` far enough to print its startup page, and give the page back."""
    from subproto import proxy

    def run(*args):
        monkeypatch.setattr(proxy, "serve", lambda *a, **k: _StubServer())
        capsys.readouterr()
        rc = cli.main(["up", "--port", str(_free_port())] + list(args))
        return rc, capsys.readouterr().out
    return run


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- M1: the banner asks the engine, not the environment -----------------------

def test_enforce_env_turns_the_banner_on_and_names_the_slots(banner, monkeypatch):
    """`SUBPROTO_ENFORCE` selects the default pair without naming them.

    The banner used to read `SUBPROTO_APPLY` only, so this start rewrote every request
    while printing "observation mode — it records, it never rewrites"."""
    monkeypatch.setenv("SUBPROTO_ENFORCE", "1")
    _rc, out = banner("--slots", "--no-color")
    assert "observation mode" not in out, out
    assert "tool_gate" in out and "compact" in out, out


def test_the_default_start_still_says_it_is_observing(banner):
    """The guard against the first test passing because the banner always says `on`."""
    _rc, out = banner("--slots", "--no-color")
    assert "observation mode" in out


def test_an_advisory_slot_is_still_marked_advisory(banner, monkeypatch):
    """`SUBPROTO_APPLY=effort` names a slot that never rewrites, and says so.

    Both halves have to survive the change: the slot is applied, so it is not
    "observation mode", and it is advisory, so it is not a rewrite either."""
    monkeypatch.setenv("SUBPROTO_APPLY", "effort")
    _rc, out = banner("--slots", "--no-color")
    assert "advisory-only" in out


# --- m8: a named model that resolved to nothing is said out loud ---------------

def test_an_unknown_model_name_is_refused_in_words_not_silence(banner):
    _rc, out = banner("--slots", "--no-color", "--model", "nope/ghost")
    assert "nope/ghost is not a known backend" in out, out
    assert "subproto models" in out, "the reason has to name where the names are"
    # One note, not one per slot: an unpinned slot inherits the global selection.
    assert out.count("is not a known backend") == 1, out


def test_a_known_model_with_no_endpoint_says_which_env_would_wire_it(banner):
    _rc, out = banner("--slots", "--no-color", "--model", "openjev")
    assert "openjev is not being served" in out, out
    assert "OPENJEV_URL" in out, out


def test_a_pinned_slot_gets_its_own_reason(banner, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL_COMPACT", "nope/ghost")
    _rc, out = banner("--slots", "--no-color")
    assert "nope/ghost is not a known backend" in out, out


# --- M3 / m4: a bad argument and a missing row answer as themselves ------------

def test_report_refuses_an_unparsable_window_saying_what_it_wanted(tmp_path, capsys):
    rc = cli.main(["report", "--since", "last-week", "--home", str(tmp_path),
                   "--no-color"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--since" in out, out
    # The example is a real date in the form it wanted, so the reader copies the line
    # back rather than guessing at a pattern.
    assert re.search(r"--since \d{4}-\d{2}-\d{2}", out), out
    # The reader is told the command still works; a refusal must not strand them.
    assert "0 requests" not in out


def test_show_json_distinguishes_a_missing_request_from_an_empty_one(tmp_path, capsys):
    rc = cli.main(["show", "999", "--json", "--home", str(tmp_path)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["decisions"] == []
    assert "999" in payload["error"] and str(tmp_path) in payload["error"], payload


# --- m2: --path names a repo, so a missing index is built rather than failed --

def test_where_builds_an_index_for_a_repo_that_has_never_been_indexed(tmp_path,
                                                                     capsys):
    from subproto import graph

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "retry.py").write_text(u"def retry(fn):\n    return fn()\n")
    capsys.readouterr()
    rc = cli.main(["where", "--path", str(repo), "--no-color", "retry"])
    captured = capsys.readouterr()
    assert rc == 0, captured.out
    assert "built one in memory" in captured.err, captured.err
    # The hint is the command that would have made it permanent, and the repo to
    # hand it — the two paths on that line are copied out of the page.
    assert graph.index_path(str(repo)) in captured.err
    assert "subproto graph %s" % repo in captured.err
    assert "retry.py" in captured.out


def test_where_still_fails_when_a_named_index_file_is_absent(tmp_path, capsys):
    missing = tmp_path / "sp-graph.json"
    rc = cli.main(["where", "--graph", str(missing), "--no-color", "retry"])
    assert rc == 1
    captured = capsys.readouterr()
    # The path is the whole answer, so it stays on one line even when the sentence
    # around it wraps (`prose` moves a long token rather than splitting it).
    assert str(missing) in captured.out
    assert "subproto graph" in captured.out
