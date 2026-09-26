"""V2->V3 bridge (S11-S14): the Router picks a slot's backend from evidence.

The load-bearing properties under test are the honesty rules, not the happy path:
that routing is opt-in, that explicit pins survive it, that an adapter only wins on
*measured* precision, that a latency budget can veto a precise-but-slow model, and —
the I6 core — that with nothing to separate them the Router stays on `heuristic`
rather than fabricating an edge for a model we have not measured beating the baseline.
"""

import json

import pytest

from subproto import systemone
from subproto.config import Config
from subproto.demo import synthetic_request
from subproto.engine import Engine
from subproto.laya_server import LayaServer
from subproto.proxy import analyze_request
from subproto.systemone import router as router_mod


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server():
    srv = LayaServer(port=free_port(), backend="lexical").start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.stop()


def ev(**entries):
    """Build an evidence map from label -> (precision, latency_ms|None)."""
    out = {}
    for label, (precision, latency) in entries.items():
        out[label] = {"precision": precision, "latency_ms": latency, "source": "test"}
    return out


# --- opt-in / default-off -----------------------------------------------------

def test_routing_is_off_by_default_and_manual_selection_stands(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBPROTO_ROUTER", raising=False)
    monkeypatch.setenv("SUBPROTO_MODEL", "laya")
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": "http://127.0.0.1:9"})
    r = systemone.Router(cfg)
    assert r.available is False
    plan = r.plan()
    assert plan["tool_gate"]["mode"] == "manual"
    assert plan["tool_gate"]["label"] == "laya"


def test_engine_leaves_backends_untouched_when_router_off(tmp_path, monkeypatch, server):
    monkeypatch.delenv("SUBPROTO_ROUTER", raising=False)
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": server})
    eng = Engine(cfg)
    assert eng.router is None
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "laya"          # the manual/legacy path, as before
    assert "router" not in gate               # no routing reason recorded


# --- I6 honesty: the shipped evidence ties, so route to heuristic ------------

def test_shipped_ablation_evidence_routes_to_heuristic(tmp_path, monkeypatch):
    """bench/ablation.json has laya == heuristic at Δ0, so auto must NOT spend a hop."""
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path), "router": True,
                         "laya_url": "http://127.0.0.1:9"})
    r = systemone.Router(cfg)
    assert "heuristic" in r.evidence and "laya" in r.evidence
    assert r.evidence["laya"]["precision"] == r.evidence["heuristic"]["precision"]
    adapter, label, reason = r.select("tool_gate")
    assert adapter is None and label == "heuristic"
    assert "no endpoint hop" in reason or "beats the heuristics" in reason


def test_precision_tie_prefers_heuristic_over_a_configured_adapter():
    evidence = ev(heuristic=(0.9, 0.0), laya=(0.9, None))
    ranked = router_mod.rank(["laya", "heuristic"], evidence)
    assert [x["label"] for x in ranked][0] == "heuristic"


# --- best-measured selection --------------------------------------------------

def test_router_selects_the_highest_measured_precision_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("DJEV_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:2")
    cfg = Config(source={"data_dir": str(tmp_path)})
    evidence = ev(heuristic=(0.90, 0.0), laya=(0.93, 12), djev=(0.99, 20))
    r = systemone.Router(cfg, evidence=evidence)
    adapter, label, reason = r.select("tool_gate")
    assert label == "djev" and adapter is not None and adapter.label == "djev"
    assert "0.9900" in reason


def test_latency_budget_vetoes_a_precise_but_slow_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("DJEV_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:2")
    cfg = Config(source={"data_dir": str(tmp_path)})
    evidence = ev(heuristic=(0.90, 0.0), laya=(0.93, 12), djev=(0.99, 20))
    r = systemone.Router(cfg, evidence=evidence, budget_ms=15)
    _, label, reason = r.select("tool_gate")
    assert label == "laya"          # djev (20ms) is over the 15ms budget
    assert "under 15ms" in reason


def test_unmeasured_adapter_is_never_auto_picked(tmp_path, monkeypatch):
    """A configured endpoint with no ablation row cannot be 'best' on evidence it lacks."""
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("OPENJEV_URL", "http://127.0.0.1:3")
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:2")
    cfg = Config(source={"data_dir": str(tmp_path)})
    evidence = ev(heuristic=(0.90, 0.0), laya=(0.95, 8))
    r = systemone.Router(cfg, evidence=evidence)
    assert "openjev" in r.adapters and "openjev" not in r.evidence
    _, label, _ = r.select("tool_gate")
    assert label == "laya"          # openjev skipped for having no precision


# --- degrade to manual when nothing can be ranked -----------------------------

def test_router_degrades_to_manual_when_no_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("SUBPROTO_MODEL", "semif")
    monkeypatch.setenv("SEMIF_URL", "http://127.0.0.1:4")
    cfg = Config(source={"data_dir": str(tmp_path)})
    r = systemone.Router(cfg, evidence={})
    adapter, label, reason = r.select("tool_gate")
    assert label == "semif" and "standing pin selection" in reason


# --- explicit pins survive routing --------------------------------------------

def test_pinned_slot_is_respected_and_not_rerouted(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("SUBPROTO_MODEL_COMPACT", "semif")
    monkeypatch.setenv("SEMIF_URL", "http://127.0.0.1:4")
    cfg = Config(source={"data_dir": str(tmp_path)})
    evidence = ev(heuristic=(0.90, 0.0), djev=(0.99, 20))
    monkeypatch.setenv("DJEV_URL", "http://127.0.0.1:1")
    r = systemone.Router(cfg, evidence=evidence)
    plan = r.plan()
    assert plan["compact"]["mode"] == "pinned" and plan["compact"]["label"] == "semif"
    assert plan["tool_gate"]["mode"] == "routed"


# --- end-to-end through the engine --------------------------------------------

def test_engine_routes_and_records_the_reason(tmp_path, monkeypatch, server):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path)})
    # a temp evidence file that clearly favours `laya` over the heuristic
    evidence = ev(heuristic=(0.50, 0.0), laya=(0.99, 5))
    file_evidence = {"heuristic": {"precision": 0.50},
                     "adapters": [{"name": "laya", "precision": 0.99, "latency_ms": 5,
                                   "source": "synthetic", "recall": 0.9, "f1": 0.94}]}
    epath = tmp_path / "abl.json"
    epath.write_text(json.dumps(file_evidence))
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": server,
                         "router": True, "router_evidence": str(epath)})
    eng = Engine(cfg)
    assert eng.router_reasons.get("tool_gate")
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "laya"
    assert "routed to laya" in gate["router"]
    assert eng.model_status()["router"]["enabled"] is True


def test_engine_router_reason_absent_when_slot_is_pinned(tmp_path, monkeypatch, server):
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.setenv("SUBPROTO_MODEL_TOOL_GATE", "laya")   # explicit pin
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": server, "router": True})
    eng = Engine(cfg)
    assert "tool_gate" not in eng.router_reasons             # pins are not routed
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "laya"
    assert "router" not in gate


# --- config plumbing ----------------------------------------------------------

def test_router_budget_parses_from_env(monkeypatch):
    monkeypatch.setenv("SUBPROTO_ROUTER_BUDGET_MS", "25")
    assert Config(source={}).router_budget_ms == 25.0
    monkeypatch.setenv("SUBPROTO_ROUTER_BUDGET_MS", "not-a-number")
    assert Config(source={}).router_budget_ms is None
    monkeypatch.delenv("SUBPROTO_ROUTER_BUDGET_MS", raising=False)
    assert Config(source={}).router_budget_ms is None


def test_configured_adapters_lists_only_endpoint_backing():
    cfg = Config(source={"data_dir": "/tmp/x"})
    assert systemone.configured_adapters(cfg) == {}          # nothing wired


# --- evidence loader ----------------------------------------------------------

def test_load_evidence_missing_file_is_empty(tmp_path):
    assert router_mod.load_evidence(str(tmp_path / "nope.json")) == {}


def test_load_evidence_reads_precision_per_adapter(tmp_path):
    p = tmp_path / "ablation.json"
    p.write_text(json.dumps({
        "heuristic": {"precision": 0.9},
        "adapters": [{"name": "djev", "precision": 0.97, "source": "synthetic"}],
    }))
    evidence = router_mod.load_evidence(str(p))
    assert evidence["heuristic"]["precision"] == 0.9
    assert evidence["djev"]["precision"] == 0.97
    assert evidence["djev"]["latency_ms"] is None


# --- CLI surface --------------------------------------------------------------

def test_cli_models_reports_the_router_plan(tmp_path, monkeypatch, capsys):
    from subproto import cli
    monkeypatch.delenv("SUBPROTO_ROUTER", raising=False)
    monkeypatch.setenv("DJEV_URL", "http://127.0.0.1:1")
    assert cli.main(["models", "--router", "--json", "--home", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["router"]["enabled"] is True
    assert payload["router"]["plan"]["tool_gate"]["mode"] == "routed"

    capsys.readouterr()
    assert cli.main(["models", "--home", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "router  off" in text, "the page must say the router is not deciding"
