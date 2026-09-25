import json
import os
import sqlite3
import threading
import time

from . import pricing

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  day TEXT NOT NULL,
  api TEXT,
  path TEXT,
  client TEXT,
  model TEXT,
  stream INTEGER,
  status INTEGER,
  err TEXT,
  latency_ms INTEGER,
  ttfb_ms INTEGER,
  in_tok INTEGER,
  cw_tok INTEGER,
  cr_tok INTEGER,
  out_tok INTEGER,
  reasoning_tok INTEGER,
  usage_source TEXT,
  cost_usd REAL,
  msg_count INTEGER,
  sys_chars INTEGER,
  tool_spec_chars INTEGER,
  tool_count INTEGER,
  tool_names TEXT,
  cat_chars TEXT,
  hist_age_rank REAL,
  body_sha TEXT,
  features_sha TEXT,
  features TEXT,
  decisions TEXT,
  interventions TEXT,
  est_in_tok INTEGER
);
CREATE INDEX IF NOT EXISTS requests_day ON requests(day);
CREATE INDEX IF NOT EXISTS requests_model ON requests(model);
"""

CATEGORIES = ("system", "tool_result", "assistant_tool_call", "assistant", "user")


class Telemetry:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def record(self, rec):
        u = rec.get("usage") or {}
        feats = rec.get("features") or {}
        cats = feats.get("cat_chars") or {}
        cost = rec.get("cost_usd")
        if cost is None:
            cost = pricing.cost_usd({
                "model": u.get("model") or rec.get("model"),
                "input_uncached": u.get("input_uncached", 0),
                "cache_write": u.get("cache_write", 0),
                "cache_read": u.get("cache_read", 0),
                "output": u.get("output", 0),
            })
        row = (
            rec.get("ts", time.time()),
            time.strftime("%Y-%m-%d", time.gmtime(rec.get("ts", time.time()))),
            rec.get("api"),
            rec.get("path"),
            rec.get("client"),
            u.get("model") or rec.get("model"),
            1 if rec.get("stream") else 0,
            rec.get("status"),
            rec.get("err"),
            rec.get("latency_ms"),
            rec.get("ttfb_ms"),
            u.get("input_uncached", 0),
            u.get("cache_write", 0),
            u.get("cache_read", 0),
            u.get("output", 0),
            u.get("reasoning", 0),
            u.get("source", "none"),
            cost,
            feats.get("msg_count"),
            feats.get("sys_chars"),
            feats.get("tool_spec_chars"),
            feats.get("tool_count"),
            json.dumps(feats.get("tool_names") or []),
            json.dumps(cats),
            json.dumps(feats.get("hist_age_rank") or {}),
            rec.get("body_sha"),
            rec.get("features_sha"),
            json.dumps(feats.get("features")) if feats.get("features") else None,
            json.dumps(rec.get("decisions")) if rec.get("decisions") else None,
            json.dumps(rec.get("interventions")) if rec.get("interventions") else None,
            feats.get("est_in_tok"),
        )
        sql = """INSERT INTO requests (
            ts, day, api, path, client, model, stream, status, err, latency_ms, ttfb_ms,
            in_tok, cw_tok, cr_tok, out_tok, reasoning_tok, usage_source, cost_usd,
            msg_count, sys_chars, tool_spec_chars, tool_count, tool_names, cat_chars,
            hist_age_rank, body_sha, features_sha, features, decisions, interventions,
            est_in_tok
        ) VALUES (""" + ",".join("?" * 31) + ")"
        with self._lock:
            self._conn.execute(sql, row)
            self._conn.commit()

    def query(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]
