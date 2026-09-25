"""T18: the live meter renders the hero line from a telemetry snapshot, and the
CLI `subproto live --once` exits 0 printing it."""

import json
import socket

import pytest

from subproto import cli, live
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
