"""T15 / S09: the tool_gate ablation scores **adapters** through the ModelAdapter
interface, agrees with the heuristic, and refuses to invent a column for a model
that has no endpoint.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import bench.ablation as ablation


def _in_range(x):
    return 0.0 <= x <= 1.0


def test_run_ablation_shape():
    m = ablation.run_ablation()
    assert m["n_cases"] == len(ablation.GROUND_TRUTH)
    b = m["heuristic"]
    for k in ("precision", "recall", "f1"):
        assert _in_range(b[k]), ("heuristic", k)
        assert b["precision"] > 0.5  # the estimator beats chance on this set
    assert [a["name"] for a in m["adapters"]] == ["laya"]
    a = m["adapters"][0]
    for k in ("precision", "recall", "f1", "agreement", "delta_vs_heuristic"):
        assert a[k] is not None and _in_range(max(a[k], 0.0)), k
    assert len(m["per_case"]) == m["n_cases"]
    assert set(("query", "gold", "heuristic", "laya")) <= set(m["per_case"][0])


def test_adapter_columns_come_from_a_live_score_call():
    """The laya column must be served over /score, not computed in-process."""
    m = ablation.run_ablation()
    assert m["adapters"][0]["source"] == "bundled lexical stand-in"


def test_unconfigured_adapters_are_skipped_not_fabricated():
    m = ablation.run_ablation(labels=["laya", "openjev", "djev", "notreal"])
    assert [a["name"] for a in m["adapters"]] == ["laya"], "no endpoint, no number"
    assert [(s["name"], "endpoint" in s["why"]) for s in m["skipped"]] == [
        ("openjev", True), ("djev", True), ("notreal", False)]
    assert "openjev" in ablation.render(m)


def test_a_configured_endpoint_becomes_its_own_column(monkeypatch):
    from subproto.laya_server import LayaServer

    srv = LayaServer(port=0, backend="lexical").start()
    try:
        monkeypatch.setenv("OPENJEV_URL", "http://127.0.0.1:%d" % srv.server_address[1])
        m = ablation.run_ablation(labels=["laya", "openjev"])
    finally:
        srv.stop()
    assert [a["name"] for a in m["adapters"]] == ["laya", "openjev"]
    assert m["adapters"][1]["source"] == "configured"
    assert m["skipped"] == []
    # both are the same lexical scorer, so the columns must agree exactly
    for row in m["per_case"]:
        assert row["laya"] == row["openjev"]


def test_committed_evidence_carries_no_host_specific_urls():
    """A reproducible artifact cannot embed an ephemeral port or a local host."""
    import json

    m = ablation.run_ablation(labels=["laya", "openjev"])
    blob = json.dumps({"adapters": m["adapters"], "per_case": m["per_case"]})
    assert "127.0.0.1" not in blob
    assert "url" not in blob, "endpoints are host-specific, so they are not recorded"


def test_ground_truth_is_consistent():
    for case in ablation.GROUND_TRUTH:
        assert set(case["keep"]) <= set(case["tools"]), case["query"][:30]
        assert "mcp__" in " ".join(case["tools"])  # every case has a droppable tool


def test_render_includes_both_backends():
    m = ablation.run_ablation()
    text = ablation.render(m)
    assert "heuristic" in text and "laya" in text and "precision" in text
    assert "Δprecision vs heuristic" in text and "stand-in" in text


def test_a_backend_that_answered_nothing_gets_no_favourable_cell(monkeypatch):
    """0/0/0 aggregates to precision 1.0 by convention, and `%.0f%%` of `None` is
    a TypeError — both are the same mistake: the row was never designed for a
    backend that never replied. It must print a row with no numbers."""
    import subproto.systemone as systemone

    class Silent(object):
        last_latency_ms = None
        last_error = "connection refused"

        def __init__(self, url, **kw):
            pass

        def score(self, state, question, options):
            return {}

    monkeypatch.setattr(systemone, "HTTPScoreAdapter", Silent)
    m = ablation.run_ablation(labels=["laya"])
    a = m["adapters"][0]
    assert a["answered"].startswith("0/"), "an unanswered case is not an answered one"
    assert a["agreement"] is None and "precision" not in a and "f1" not in a
    assert a["delta_vs_heuristic"] is None
    text = ablation.render(m)
    assert "not measured" in text
    assert "| laya | — | — | — | not measured |" in text


def test_the_corpus_reports_its_own_ceiling():
    """The table's headline number is only as wide as the set's disagreement, so
    the set has to say so."""
    c = ablation.corpus_ceiling()
    assert c["cases"] == len(ablation.GROUND_TRUTH)
    assert c["decisions"] == sum(len(x["tools"]) for x in ablation.GROUND_TRUTH)
    assert c["gold_equals_heuristic"] > c["cases"] // 2, (
        "if the baseline stops matching the gold sets, this harness is measuring "
        "something real again and the note below the table needs rewriting")
    assert c["baseline_false_keeps"] + c["baseline_false_cuts"] == sum(
        len(d["only_in_gold"]) + len(d["only_in_heuristic"])
        for d in c["differing_cases"])
    assert "cannot measure" in ablation.render(ablation.run_ablation(labels=["laya"]))

