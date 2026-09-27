"""T11: WIRING.md must document every tool, and the proxy must actually serve
every endpoint the docs promise — so the README wiring claims stay auditable
(invariant I6: measured, not claimed)."""

import os
import socket
import threading
import time
import urllib.request

import pytest

from subproto import demo
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer, dialect_for
from subproto.telemetry import Telemetry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIRING = os.path.join(ROOT, "WIRING.md")

DOCUMENTED_PATHS = [
    "/v1/messages",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1beta/models/gemini-2.5-flash:generateContent",
    "/v1beta/models/gemini-2.5-flash:streamGenerateContent",
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_wiring_documents_every_endpoint():
    text = open(WIRING).read()
    markers = ["/v1/messages", "/v1/chat/completions", "/v1/completions",
               "/v1/responses", ":generateContent", ":streamGenerateContent"]
    for m in markers:
        assert m in text, "WIRING.md never mentions %s" % m


def test_wiring_covers_every_target_agent():
    text = open(WIRING).read().lower()
    for tool in ("claude code", "codex", "gemini cli", "cline", "aider",
                 "opencode", "antigravity"):
        assert tool in text, "WIRING.md is missing wiring for %s" % tool


def test_dialect_routes_every_documented_path():
    for path in DOCUMENTED_PATHS:
        assert dialect_for(path) is not None, "unrouted: %s" % path


@pytest.fixture
def proxied(tmp_path):
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, Engine(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import fakeup.server as mock
    mock_up = mock.MockUpstream(port=free_port()).start()
    base = "http://127.0.0.1:%d" % mock_up.port
    cfg.source = dict(cfg.source, anthropic_upstream=base, openai_upstream=base,
                      gemini_upstream=base)
    try:
        yield cfg, tel, srv
    finally:
        srv.shutdown()
        srv.server_close()
        mock_up.stop()
        tel.close()


def _body_for(path):
    if "/messages" in path:
        return demo.synthetic_request(0, turns=2)
    if "generateContent" in path:
        return demo.gemini_synthetic(0)
    if "/responses" in path:
        return demo.responses_synthetic(0)
    if "/completions" in path:
        return demo.openai_synthetic(0)
    return {}


def test_proxy_serves_every_documented_path(proxied, settled):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    seen = []
    for path in DOCUMENTED_PATHS:
        body = _body_for(path)
        raw = demo.protocol.dump_body(body)
        req = urllib.request.Request(base + path, data=raw,
                                     headers={"content-type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            assert r.status == 200, path
            r.read()
        seen.append(path)
    settled(tel, len(seen))
    rows = tel.query("SELECT api, status FROM requests ORDER BY id")
    assert len(rows) == len(seen)
    assert all(r["status"] == 200 for r in rows)
