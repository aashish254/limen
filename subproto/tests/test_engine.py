import json
import os

from subproto.config import Config
from subproto.demo import synthetic_request
from subproto.engine import Engine
from subproto.proxy import analyze_request


def _cfg(tmp_path, **kw):
    return Config(source={"data_dir": str(tmp_path)}, **kw)


def test_engine_observation_mode_does_not_modify_body(tmp_path):
    cfg = _cfg(tmp_path)
    eng = Engine(cfg)
    body = synthetic_request(0)
    analysis = analyze_request(body)
    new_body, decisions = eng.decide("anthropic", body, analysis, cfg)
    assert new_body is None, "no enforcement should mean the body is untouched"
    assert {d["slot"] for d in decisions} >= {"tool_gate", "compact", "effort"}


def test_engine_tool_gate_enforcement_reduces_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    cfg = _cfg(tmp_path)
    eng = Engine(cfg)
    body = synthetic_request(0)
    analysis = analyze_request(body)
    new_body, decisions = eng.decide("anthropic", body, analysis, cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert new_body is not None
    assert len(new_body["tools"]) < len(body["tools"])
    assert len(new_body["tools"]) == gate["kept"]


def test_engine_context_slot_with_graph(tmp_path, monkeypatch):
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    monkeypatch.setenv("SUBPROTO_ENFORCE", "1")
    cfg = _cfg(tmp_path)
    eng = Engine(cfg, graph_path=gp)
    body = synthetic_request(0)
    body["messages"][-1]["content"] = [{"type": "text",
        "text": "refactor PaymentGateway.retry refund in app/payments/retry.py"}]
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    ctx = [d for d in decisions if d["slot"] == "context"]
    assert ctx, "graph should produce a context decision"
    targets = [c["target"] for c in ctx[0]["candidates"]]
    assert any("retry.py" in t for t in targets)


def test_engine_falls_back_when_laya_unreachable(tmp_path):
    cfg = _cfg(tmp_path, laya_url="http://127.0.0.1:1")  # nothing listening
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "heuristic"


def test_engine_records_per_decision_latency(tmp_path):
    cfg = _cfg(tmp_path)
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert decisions, "expect at least one slot decision"
    for d in decisions:
        assert "decision_ms" in d, d["slot"]
        assert d["decision_ms"] >= 0.0
    # heuristics are sub-millisecond on this tiny corpus
    assert sum(d["decision_ms"] for d in decisions) < 500.0
