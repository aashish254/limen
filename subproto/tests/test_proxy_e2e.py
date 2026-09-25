import json
import socket
import threading
import time

import pytest

from subproto import demo, report
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer
from subproto.telemetry import Telemetry


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def proxied(tmp_path):
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port(), store_bodies=True)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    eng = Engine(cfg)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, eng)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield cfg, tel, srv, eng
    srv.shutdown()
    srv.server_close()
    tel.close()


def test_streamed_passthrough_and_telemetry(proxied):
    cfg, tel, srv, eng = proxied
    n = demo.replay(cfg, mock_port=free_port(), n=6, jitter=True)
    assert n == 7
    time.sleep(0.2)
    rows = tel.query("SELECT api, model, in_tok, cr_tok, out_tok, cost_usd, stream "
                     "FROM requests ORDER BY id")
    assert len(rows) == 7
    assert all(r["model"] for r in rows)
    assert any(r["cr_tok"] > 0 for r in rows)
    assert sum(r["out_tok"] for r in rows) > 0
    assert all(r["stream"] == 1 for r in rows)


def test_report_has_cache_headroom(proxied):
    cfg, tel, srv, eng = proxied
    demo.replay(cfg, mock_port=free_port(), n=6)
    time.sleep(0.2)
    summary = report.summarize(tel)
    assert summary["totals"]["n"] >= 6
    txt = report.render_text(summary)
    assert "subproto report" in txt and "slot headroom" in txt
    assert report.render_json(summary)["slot_headroom"]


def test_enforce_path_returns_200_and_records_interventions(proxied, monkeypatch):
    cfg, tel, srv, eng = proxied
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate,compact")
    n = demo.replay(cfg, mock_port=free_port(), n=5)
    time.sleep(0.2)
    rows = tel.query("SELECT status, interventions FROM requests ORDER BY id")
    assert n == 6 and all(r["status"] == 200 for r in rows)
    assert all(r["interventions"] for r in rows), "enforcement should tag every request"


def test_dataset_export_and_label_roundtrip(proxied, tmp_path):
    cfg, tel, srv, eng = proxied
    demo.replay(cfg, mock_port=free_port(), n=6)
    time.sleep(0.2)
    from subproto import dataset
    audit = dataset.audit(cfg, tel, engine=eng, limit=10)
    assert audit["requests_scanned"] >= 6
    # the proxy ran the engine in observation mode, so live decisions were stored
    exp = dataset.export(cfg, tel, path=str(tmp_path / "ds.jsonl"))
    assert exp["rows"] > 0
    assert exp["slots"]["tool_gate"] > 0
    with open(str(tmp_path / "ds.jsonl")) as f:
        first = json.loads(f.readline())
    assert first["schema"] == "subproto/1"
    assert first["slot"] in ("tool_gate", "compact", "effort", "context")
    dataset.save_labels(cfg, {"1": {"tool_gate": "bad", "reason": "dropped a needed tool"}})
    assert dataset.load_labels(cfg)["1"]["tool_gate"] == "bad"
