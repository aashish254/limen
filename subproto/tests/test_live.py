"""T18: the live meter renders the hero line from a telemetry snapshot, and the
CLI `subproto live --once` exits 0 printing it."""

import json
import socket

import pytest

from subproto import cli, live, style
from subproto.config import Config
from subproto.telemetry import Telemetry


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_render_snapshot_has_saved_line():
    snap = {"n": 42, "in_tok": 100000, "cost_usd": 1.02, "saved_tok": 38000,
            "saved_pct": 38.0, "after_usd": 0.63, "mode": "delivered"}
    text = live.render(snap, color=False)
    assert "saved" in text
    assert "38%" in text
    assert "38,000" in text
    assert "$1.02" in text and "$0.63" in text


def test_render_empty_snapshot():
    text = live.render({"n": 0}, color=False)
    assert "no traffic yet" in text


def test_render_color_wraps_ansi():
    snap = {"n": 1, "in_tok": 10, "cost_usd": 0.0, "saved_tok": 1,
            "saved_pct": 10.0, "after_usd": 0.0, "mode": "potential"}
    text = live.render(snap, color=True)
    assert "\033[" in text  # bold/frame codes present when colored


def test_snapshot_reads_potential_headroom(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    decisions = [{"slot": "tool_gate", "savings_est_tok": 500},
                 {"slot": "compact", "savings_est_tok": 300}]
    for _ in range(3):
        tel.record({"ts": 1.0, "api": "anthropic", "model": "claude-x", "status": 200,
                    "usage": {"model": "claude-x", "input_uncached": 1000, "cache_write": 0,
                    "cache_read": 0, "output": 50, "reasoning": 0,
                    "source": "anthropic"},
                    "features": {"cat_chars": {}}, "decisions": decisions})
    s = live.snapshot(tel)
    assert s["n"] == 3
    assert s["saved_tok"] == 800 * 3
    assert s["mode"] == "potential"  # no interventions recorded
    tel.close()


def test_saved_pct_never_exceeds_100_when_most_input_is_cached(tmp_path):
    """Regression: savings are measured over the whole prompt, so the meter must not
    divide a full-prompt numerator by the tiny cache-excluded billed input and report
    a >100% 'saving'. Here 800k of the input is cached; the honest share is 30%."""
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    tel.record({"ts": 1.0, "api": "anthropic", "model": "claude-x", "status": 200,
                "usage": {"model": "claude-x", "input_uncached": 1000, "cache_write": 0,
                          "cache_read": 800000, "output": 50, "reasoning": 0,
                          "source": "anthropic"},
                "features": {"cat_chars": {}, "est_in_tok": 10000},
                "decisions": [{"slot": "tool_gate", "savings_est_tok": 3000}]})
    s = live.snapshot(tel)
    assert s["saved_pct"] == pytest.approx(30.0), \
        "must use the full estimated input, not the ~1k uncached slice"
    assert s["saved_pct"] <= 100.0
    assert s["after_usd"] < s["cost_usd"]  # a real, sub-total reduction
    tel.close()


def test_cli_live_once_exits_zero_and_prints(tmp_path, capsys):
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port())
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    tel.record({"ts": 1.0, "api": "anthropic", "model": "claude-x", "status": 200,
                "usage": {"model": "claude-x", "input_uncached": 2000, "cache_write": 0,
                          "cache_read": 0, "output": 10, "reasoning": 0, "source": "anthropic"},
                "features": {"cat_chars": {}},
                "decisions": [{"slot": "compact", "savings_est_tok": 400}]})
    tel.close()
    rc = cli.main(["live", "--home", str(tmp_path), "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "subproto" in out and "save" in out


def test_the_saved_percent_is_reproducible_from_the_page():
    """66,493 / 179,280 reads 37%, but the meter said 32%: the ratio is against the
    whole estimated prompt, not the billed input. If the page shows a percent and
    two token figures that do not divide into it, a reader cannot check the number
    from a screenshot, so the denominator has to be on the page with them."""
    snap = {"n": 17, "in_tok": 179280, "cost_usd": 0.59, "saved_tok": 66493,
            "saved_pct": 32.1, "denom_tok": 207168, "after_usd": 0.40,
            "mode": "potential"}
    plain = live.render(snap, color=False)
    assert "prompt incl. cached" in plain
    assert "207,168" in plain
    # the three printed figures have to agree with each other, not just with the
    # reader's trust: percent == round(100 * saved / denom), and the denominator is
    # never the billed line two rows down.
    shown = int([w for w in plain.split() if w.endswith("%")][0].rstrip("%"))
    assert shown == int(round(100.0 * snap["saved_tok"] / snap["denom_tok"]))
    assert snap["denom_tok"] != snap["in_tok"]
    # the new row goes through the same one gate as every other field
    assert style.strip(live.render(snap, color=True)) == plain


def test_snapshot_reports_the_divisor_it_actually_used(tmp_path):
    """The percent on the page must be reproducible from the denominator the page
    prints, so `snapshot` has to hand render the divisor it really used — not let
    render re-guess it from the billed line."""
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    tel.record({"ts": 1.0, "api": "anthropic", "model": "claude-x", "status": 200,
                "usage": {"model": "claude-x", "input_uncached": 1000,
                          "cache_write": 0, "cache_read": 500, "output": 10,
                          "reasoning": 0, "source": "anthropic"},
                "features": {"cat_chars": {}, "est_in_tok": 1500},
                "decisions": [{"slot": "tool_gate", "savings_est_tok": 300}]})
    snap = live.snapshot(tel)
    assert snap["saved_tok"] == 300
    assert snap["denom_tok"] == 1500          # the whole prompt, not the 1000 billed
    assert snap["in_tok"] == 1000
    assert snap["saved_pct"] == pytest.approx(20.0)
    # and the rendered page divides out to the percent it printed
    plain = live.render(snap, color=False)
    assert "1,500" in plain
    shown = int([w for w in plain.split() if w.endswith("%")][0].rstrip("%"))
    assert shown == int(round(100.0 * snap["saved_tok"] / snap["denom_tok"]))
    tel.close()
