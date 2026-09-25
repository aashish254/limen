"""T14: a live scoring server behind the laya adapter must flip the engine's
recorded backend from "heuristic" to "laya" and drive keep/drop."""

import json
import urllib.request

import pytest

from subproto import protocol
from subproto.config import Config
from subproto.demo import synthetic_request
from subproto.engine import Engine
from subproto.laya import LayaClient
from subproto.laya_server import LayaServer, lexical_probabilities
from subproto.proxy import analyze_request


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def laya_url():
    srv = LayaServer(port=free_port(), backend="lexical").start()
    try:
        yield "http://127.0.0.1:%d" % srv.port
    finally:
        srv.stop()


def test_health_endpoint(laya_url):
    with urllib.request.urlopen(laya_url + "/health", timeout=2) as r:
        data = json.loads(r.read().decode())
    assert data["ok"] is True and data["backend"] == "lexical"


def test_score_endpoint_returns_probabilities(laya_url):
    payload = json.dumps({"state": "read the retry file", "question": "keep",
                          "options": ["Read", "mcp__slack__post"]}).encode()
    req = urllib.request.Request(laya_url + "/score", data=payload,
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=2) as r:
        probs = json.loads(r.read())["probabilities"]
    assert set(probs) == {"Read", "mcp__slack__post"}
    assert probs["Read"] >= 0.5  # core tool kept
    assert 0.0 <= probs["mcp__slack__post"] <= 1.0


def test_client_scores_options(laya_url):
    client = LayaClient(laya_url)
    probs = client.score("edit the file", "keep", ["Edit", "WebSearch"])
    assert probs is not None and "Edit" in probs
    assert client.last_error is None


def test_engine_records_laya_backend(tmp_path, laya_url, monkeypatch):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": laya_url})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "laya", "engine should have used the scoring server"
    # every candidate carries a laya-driven score/keep and is bounded
    for c in gate["candidates"]:
        assert 0.0 <= c["score"] <= 1.0
    # core tools are never dropped regardless of the query
    dropped = set(gate["dropped"])
    assert not (dropped & {"read", "edit", "bash", "grep", "glob", "write"})


def test_engine_falls_back_when_server_down(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path),
                         "laya_url": "http://127.0.0.1:%d" % free_port()})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "heuristic"


def test_lexical_scorer_is_deterministic():
    a = lexical_probabilities("fix the payment retry", "keep",
                              ["Read", "mcp__jira__comment", "Bash"])
    b = lexical_probabilities("fix the payment retry", "keep",
                              ["Read", "mcp__jira__comment", "Bash"])
    assert a == b
