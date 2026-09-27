"""The ambient-index witnesses (S46).

A first-time user who ran `subproto graph <repo> --install` inside a checkout and then
`python -m pytest` got a red suite: the engine read `.subproto-graph.json` from whatever
directory it happened to be constructed in, so the `context` slot switched on for some
tests and off for others. Two of those tests then failed on the shape of the record that
slot writes — which was its own bug, waiting for anyone to look at a context decision in
`show`, `export` or the dataset split.

These tests pin all three facts: the engine reads only the index it is handed, the
command layer is the one that decides whether a repository's installed index counts, and
the context decision is a complete record on both paths.
"""
import json
import os
import types

import pytest

from subproto import cli, graph as graph_mod
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import analyze_request
from subproto import systemone


BODY = {
    "model": "claude-sonnet-4-5",
    "max_tokens": 64,
    "system": "You are a coding agent.",
    "messages": [{"role": "user",
                  "content": "fix the retry connection helper in app/pay/retry.py"}],
}


@pytest.fixture
def home(tmp_path):
    return Config.load(None, data_dir=str(tmp_path / "home"), port=8899,
                       store_bodies=False)


@pytest.fixture
def repo(tmp_path):
    """A repository, indexed the way `subproto graph --install` leaves it."""
    root = tmp_path / "project"
    (root / "app" / "pay").mkdir(parents=True)
    (root / "app" / "pay" / "retry.py").write_text(
        '"""retry the connection when the socket drops."""\n'
        "def retry_connection():\n    return 'retry the connection'\n",
        encoding="utf-8")
    index = graph_mod.save(graph_mod.build(str(root)),
                           os.path.join(str(root), ".subproto-graph.json"))
    return str(root), index


def decisions_for(cfg, **kw):
    eng = Engine(cfg, **kw)
    _new, decisions = eng.decide("anthropic", BODY, analyze_request(BODY), cfg)
    return eng, decisions


def test_the_engine_reads_no_index_from_the_directory_it_was_built_in(home, repo,
                                                                     monkeypatch):
    root, _index = repo
    monkeypatch.chdir(root)
    eng, decisions = decisions_for(home)
    assert eng.graph is None and eng.graph_path is None
    assert "context" not in [d["slot"] for d in decisions]


def test_the_engine_reads_the_index_it_is_handed(home, repo, monkeypatch):
    root, index = repo
    eng, decisions = decisions_for(home, graph_path=root)
    assert eng.graph_path == index
    assert "context" in [d["slot"] for d in decisions]


def test_the_command_layer_is_what_decides(home, repo, monkeypatch, tmp_path,
                                           ambient_index_as_shipped):
    root, index = repo
    monkeypatch.chdir(root)
    args = types.SimpleNamespace(graph=None)
    assert cli._graph_arg(args) == index
    named = types.SimpleNamespace(graph="/somewhere/else.json")
    assert cli._graph_arg(named) == "/somewhere/else.json"
    bare = tmp_path / "no-index-here"
    bare.mkdir()
    elsewhere = types.SimpleNamespace(graph=None)
    monkeypatch.chdir(str(bare))
    assert cli._graph_arg(elsewhere) is None


def test_a_context_decision_is_a_complete_record(home, repo, monkeypatch):
    """The dataset loop indexes `savings_est_tok`; the slot that only adds still answers it."""
    root, _index = repo
    eng, decisions = decisions_for(home, graph_path=root)
    ctx = [d for d in decisions if d["slot"] == "context"]
    assert ctx, "the index was handed over and no context decision was made"
    for d in ctx:
        assert d["savings_est_tok"] == 0
        assert d["cost_est_tok"] == 0          # observing: nothing was added
        assert d["backend"] == "graph+heuristic"


def test_an_applied_context_note_prices_what_it_added(home, repo, monkeypatch):
    root, _index = repo
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    eng, decisions = decisions_for(home, graph_path=root)
    ctx = [d for d in decisions if d["slot"] == "context"]
    assert len(ctx) == 1
    assert ctx[0]["applied"] is True
    assert ctx[0]["cost_est_tok"] > 0, "an injected block that is not priced is a free lunch"


def test_a_source_label_never_becomes_a_model_version(home, repo, monkeypatch, tmp_path):
    """`model_version` groups a checkpoint against the baseline; a source name is not one."""
    root, _index = repo
    manifest = {"versions": [{"label": "v1", "url": "http://127.0.0.1:9",
                              "tier": "foundation", "added": "2026-09-27"}],
                "active": "v1", "previous": None}
    systemone.save(systemone.path(home), manifest)
    eng, decisions = decisions_for(home, graph_path=root)
    ctx = [d for d in decisions if d["slot"] == "context"]
    assert ctx
    for d in ctx:
        assert d["model_version"] == systemone.HEURISTIC, d["model_version"]
    assert eng.model_version == systemone.HEURISTIC   # v1 is dead: a refused port


def test_the_record_survives_the_telemetry_and_the_split(home, repo, monkeypatch, tmp_path):
    """A decision the proxy can record is the contract; a missing key used to be a KeyError."""
    from subproto.telemetry import Telemetry
    root, _index = repo
    eng, decisions = decisions_for(home, graph_path=root)
    tel = Telemetry(home.db_path)
    try:
        tel.record({"ts": 1.0, "api": "anthropic", "path": "/v1/messages",
                    "client": "pytest", "model": "m", "status": 200, "body_sha": "am1",
                    "features": analyze_request(BODY), "usage": {"input": 100, "output": 5},
                    "decisions": decisions})
        rows = tel.query("SELECT decisions FROM requests WHERE body_sha='am1'")
        back = json.loads(rows[0]["decisions"])
    finally:
        tel.close()
    assert [d for d in back if d["slot"] == "context"][0]["savings_est_tok"] == 0
