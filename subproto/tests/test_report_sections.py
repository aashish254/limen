"""T24: the report carries the measured cache-hit-rate, per-decision latency,
and label-driven slot-precision sections in both the text and json renders, and
render_json stays backward-compatible with the pre-existing keys."""

import json

from subproto import report
from subproto.config import Config
from subproto.telemetry import Telemetry


def _corpus(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    decisions = [
        {"slot": "tool_gate", "savings_est_tok": 400, "decision_ms": 1.2},
        {"slot": "compact", "savings_est_tok": 250, "decision_ms": 0.8},
    ]
    # two cache-read-heavy requests + one fresh-only request -> non-trivial hit rate
    for cr, cw, engine in ((800, 0, 2.0), (400, 0, 1.5), (0, 0, 1.0)):
        tel.record({"ts": 1.0, "api": "anthropic", "model": "claude-3-5-sonnet",
                    "status": 200,
                    "usage": {"model": "claude-3-5-sonnet", "input_uncached": 1000,
                              "cache_write": cw, "cache_read": cr, "output": 50,
                              "reasoning": 0, "source": "anthropic"},
                    "features": {"cat_chars": {"tool_result": 900, "system": 300}},
                    "engine_ms": engine, "decisions": decisions})
    return cfg, tel


LABELS = {
    "1": {"tool_gate": "good", "compact": "good"},
    "2": {"tool_gate": "good", "compact": "bad"},
    "3": {"tool_gate": "uncertain"},
}


def test_summarize_exposes_new_sections(tmp_path):
    _, tel = _corpus(tmp_path)
    summary = report.summarize(tel, labels=LABELS)
    assert summary["cache_hit_rate"]["hit_rate"] is not None
    # cached = 800+400 = 1200; input = (1000*3) + 1200 = 4200
    assert summary["cache_hit_rate"]["cached_in"] == 1200
    assert abs(summary["cache_hit_rate"]["hit_rate"] - 1200 / 4200.0) < 1e-4
    assert summary["decision_latency"]["n"] == 3
    # tool_gate: good=2 bad=0 uncertain=1 -> approval 1.0; compact: good=1 bad=1 -> 0.5
    assert summary["slot_precision"]["tool_gate"]["approval_rate"] == 1.0
    assert summary["slot_precision"]["compact"]["approval_rate"] == 0.5
    tel.close()


def test_slot_precision_absent_without_labels(tmp_path):
    _, tel = _corpus(tmp_path)
    summary = report.summarize(tel)  # no labels arg
    assert "slot_precision" not in summary
    assert "cache_hit_rate" in summary  # always available, it is measured
    tel.close()


def test_uncertain_only_slot_reports_none(tmp_path):
    _, tel = _corpus(tmp_path)
    # only an uncertain verdict for tool_gate -> denominator 0 -> approval None
    summary = report.summarize(tel, labels={"1": {"tool_gate": "uncertain"}})
    assert summary["slot_precision"]["tool_gate"]["approval_rate"] is None
    assert summary["slot_precision"]["tool_gate"]["labelled"] == 1
    tel.close()


def test_render_text_has_cache_and_precision_lines(tmp_path):
    _, tel = _corpus(tmp_path)
    text = report.render_text(report.summarize(tel, labels=LABELS))
    assert "cache hit rate" in text
    assert "slot decision latency" in text
    assert "slot precision" in text
    assert "approval" in text
    tel.close()


def test_render_json_backward_compatible(tmp_path):
    _, tel = _corpus(tmp_path)
    payload = report.render_json(report.summarize(tel, labels=LABELS))
    # pre-existing keys must survive
    for key in ("totals", "by_model", "by_client", "cat_chars", "by_day",
                "requests", "cache_headroom", "slot_headroom"):
        assert key in payload, "render_json dropped %s" % key
    # new keys are additive
    for key in ("decision_latency", "cache_hit_rate", "slot_precision"):
        assert key in payload
    # still serialises cleanly
    json.dumps(payload, default=str)
    tel.close()
