import json
import sqlite3

from subproto import report
from subproto.config import Config
from subproto.telemetry import Telemetry


def _minimal_rec(**over):
    rec = {
        "ts": 1.0, "api": "anthropic", "model": "claude-x", "stream": 1, "status": 200,
        "latency_ms": 900, "ttfb_ms": 120,
        "usage": {"model": "claude-x", "input_uncached": 100, "cache_write": 10,
                  "cache_read": 50, "output": 20, "reasoning": 0, "source": "anthropic"},
        "features": {"est_in_tok": 2000, "msg_count": 4, "cat_chars": {}},
    }
    rec.update(over)
    return rec


def test_record_stores_engine_ms(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    tel.record(_minimal_rec(engine_ms=0.42))
    row = tel.query("SELECT engine_ms FROM requests")[0]
    assert row["engine_ms"] == 0.42
    tel.close()


def test_report_surfaces_decision_latency(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    tel = Telemetry(cfg.db_path)
    for ms in (0.1, 0.2, 0.3, 0.9):
        tel.record(_minimal_rec(engine_ms=ms))
    summary = report.summarize(tel)
    dl = summary["decision_latency"]
    assert dl["n"] == 4
    assert dl["p50"] is not None and dl["p95"] >= dl["p50"]
    txt = report.render_text(summary)
    assert "decision latency" in txt
    assert report.render_json(summary)["decision_latency"]["n"] == 4
    tel.close()


def test_migration_adds_engine_ms_to_old_schema(tmp_path):
    """An existing DB from before the engine_ms column must upgrade idempotently."""
    db = str(tmp_path / "telemetry.db")
    conn = sqlite3.connect(db)
    # an old table that intentionally lacks engine_ms
    conn.execute("""CREATE TABLE requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, day TEXT NOT NULL,
        api TEXT, path TEXT, client TEXT, model TEXT, stream INTEGER, status INTEGER,
        err TEXT, latency_ms INTEGER, ttfb_ms INTEGER, in_tok INTEGER, cw_tok INTEGER,
        cr_tok INTEGER, out_tok INTEGER, reasoning_tok INTEGER, usage_source TEXT,
        cost_usd REAL, msg_count INTEGER, sys_chars INTEGER, tool_spec_chars INTEGER,
        tool_count INTEGER, tool_names TEXT, cat_chars TEXT, hist_age_rank REAL,
        body_sha TEXT, features_sha TEXT, features TEXT, decisions TEXT,
        interventions TEXT, est_in_tok INTEGER)""")
    conn.execute("INSERT INTO requests (ts, day, model, in_tok) VALUES (1.0,'1970-01-01','m',5)")
    conn.commit()
    conn.close()

    tel = Telemetry(db)  # runs executescript(IF NOT EXISTS no-op) + _migrate()
    cols = {r["name"] for r in tel.query("PRAGMA table_info(requests)")}
    assert "engine_ms" in cols
    # old row survived, new insert works
    tel.record(_minimal_rec(engine_ms=1.5))
    rows = tel.query("SELECT engine_ms FROM requests ORDER BY id")
    assert rows[0]["engine_ms"] is None
    assert rows[1]["engine_ms"] == 1.5
    tel.close()
    # reopening is a no-op (idempotent)
    tel2 = Telemetry(db)
    assert {r["name"] for r in tel2.query("PRAGMA table_info(requests)")} >= {"engine_ms"}
    tel2.close()
