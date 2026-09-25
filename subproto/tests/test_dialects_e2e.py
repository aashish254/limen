"""Dialect-completeness tests for FR-8: Gemini generateContent and OpenAI
/responses must be routed, relayed (streamed + non-streamed) and have their
usage reconstructed end-to-end through the proxy.
"""

import json
import socket
import threading
import time

import pytest

from subproto import demo
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer, dialect_for
from subproto.telemetry import Telemetry
from subproto import usage as usage_mod


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- unit: parsers ---------------------------------------------------------

def test_dialect_for_routes_new_paths():
    assert dialect_for("/v1beta/models/gemini-2.5-flash:generateContent") == "gemini"
    assert dialect_for("/v1beta/models/x:streamGenerateContent?alt=sse") == "gemini"
    assert dialect_for("/v1/responses") == "responses"
    assert dialect_for("/v1/chat/completions") == "openai"
    assert dialect_for("/v1/messages") == "anthropic"


def test_gemini_non_stream_parser():
    u = usage_mod.from_gemini({"modelVersion": "gemini-x", "usageMetadata": {
        "promptTokenCount": 5320, "cachedContentTokenCount": 5100,
        "candidatesTokenCount": 410, "thoughtsTokenCount": 90}})
    assert u["input_uncached"] == 220
    assert u["cache_read"] == 5100
    assert u["output"] == 410
    assert u["reasoning"] == 90
    assert u["model"] == "gemini-x"


def test_gemini_stream_keeps_last_cumulative_frame():
    acc = usage_mod.UsageAccumulator("gemini")
    acc.feed_obj({"candidates": [{"content": {"parts": [{"text": "do"}]}}]})
    acc.feed_obj({"modelVersion": "gemini-y", "usageMetadata": {
        "promptTokenCount": 1000, "candidatesTokenCount": 50}})
    u = acc.usage()
    assert u["input_uncached"] == 1000
    assert u["output"] == 50
    assert u["source"] == "gemini"


def test_responses_parser_reads_completed_event():
    obj = {"type": "response.completed", "response": {
        "model": "gpt-5", "usage": {
            "input_tokens": 9000, "output_tokens": 210,
            "input_tokens_details": {"cached_tokens": 8000},
            "output_tokens_details": {"reasoning_tokens": 64}}}}
    u = usage_mod.from_responses(obj)
    assert u["input_uncached"] == 1000
    assert u["cache_read"] == 8000
    assert u["output"] == 210
    assert u["reasoning"] == 64


def test_responses_stream_ignores_usageless_frames():
    acc = usage_mod.UsageAccumulator("responses")
    acc.feed_obj({"type": "response.created", "response": {"model": "gpt-5"}})
    acc.feed_obj({"type": "response.output_text.delta", "delta": "hi"})
    acc.feed_obj({"type": "response.completed", "response": {
        "model": "gpt-5", "usage": {"input_tokens": 400, "output_tokens": 20}}})
    u = acc.usage()
    assert u["input_uncached"] == 400
    assert u["output"] == 20
    assert u["source"] == "responses"


# --- normalize: gemini message shape --------------------------------------

def test_normalize_gemini_contents():
    body = {"systemInstruction": {"parts": [{"text": "be safe"}]},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]},
                         {"role": "model", "parts": [{"text": "yo"}]}]}
    norm = demo.protocol.normalize_messages(body)
    kinds = [k for _, _, k in norm]
    assert kinds[:1] == ["system"]
    assert "user" in kinds and "assistant" in kinds


# --- e2e through the proxy + mock -----------------------------------------

@pytest.fixture
def proxied(tmp_path):
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    eng = Engine(cfg)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, eng)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    import fakeup.server as mock
    mock_up = mock.MockUpstream(port=free_port()).start()
    src = dict(cfg.source)
    base = "http://127.0.0.1:%d" % mock_up.port
    src["anthropic_upstream"] = base
    src["openai_upstream"] = base
    src["gemini_upstream"] = base
    cfg.source = src
    try:
        yield cfg, tel, srv
    finally:
        srv.shutdown()
        srv.server_close()
        mock_up.stop()
        tel.close()


def _post_stream(base, path, body, headers):
    import urllib.request
    raw = demo.protocol.dump_body(body)
    h = {"content-type": "application/json", "user-agent": "gemini-cli/1.0"}
    h.update(headers)
    req = urllib.request.Request(base + path, data=raw, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
        return r.status


def test_gemini_streamed_e2e_records_usage(proxied):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.gemini_synthetic(1)
    assert _post_stream(base, "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse",
                        body, {"x-goog-api-key": "mock"}) == 200
    time.sleep(0.2)
    row = tel.query("SELECT api, model, in_tok, cr_tok, out_tok, reasoning_tok, usage_source "
                    "FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert row["api"] == "gemini" and row["usage_source"] == "gemini"
    assert row["in_tok"] == 220 and row["cr_tok"] == 5100
    assert row["out_tok"] == 410 and row["reasoning_tok"] == 90


def test_gemini_non_stream_e2e(proxied):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.gemini_synthetic(2)
    assert _post_stream(base, "/v1beta/models/gemini-2.5-flash:generateContent",
                        body, {"x-goog-api-key": "mock"}) == 200
    time.sleep(0.2)
    row = tel.query("SELECT in_tok, out_tok, usage_source FROM requests "
                    "ORDER BY id DESC LIMIT 1")[0]
    assert row["usage_source"] == "gemini"
    assert row["in_tok"] == 220 and row["out_tok"] == 410


def test_responses_streamed_e2e_records_usage(proxied):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.responses_synthetic(1)
    assert _post_stream(base, "/v1/responses", body,
                        {"Authorization": "Bearer mock", "user-agent": "openai-python/1.0"}) == 200
    time.sleep(0.2)
    row = tel.query("SELECT api, in_tok, cr_tok, out_tok, reasoning_tok, usage_source "
                    "FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert row["api"] == "responses" and row["usage_source"] == "responses"
    assert row["in_tok"] == 340 and row["cr_tok"] == 52000
    assert row["out_tok"] == 210 and row["reasoning_tok"] == 64
