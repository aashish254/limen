"""Dialect-completeness tests for FR-8: Gemini generateContent and OpenAI
/responses must be routed, relayed (streamed + non-streamed) and have their
usage reconstructed end-to-end through the proxy.
"""

import json
import socket
import threading
import time

import pytest

from fakeup import server as mockup
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


def test_gemini_streamed_e2e_records_usage(proxied, settled):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.gemini_synthetic(1)
    assert _post_stream(base, "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse",
                        body, {"x-goog-api-key": "mock"}) == 200
    settled(tel, 1)
    row = tel.query("SELECT api, model, in_tok, cr_tok, out_tok, reasoning_tok, usage_source "
                    "FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert row["api"] == "gemini" and row["usage_source"] == "gemini"
    # The mock prices the body it was sent (S46/B4), so the expectation is that body's
    # weight — not a constant someone once typed into the mock.
    assert row["in_tok"] == mockup._tok(mockup.prompt_text(body)) and row["cr_tok"] == 0
    assert row["out_tok"] == 410 and row["reasoning_tok"] == 90


def test_gemini_non_stream_e2e(proxied, settled):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.gemini_synthetic(2)
    assert _post_stream(base, "/v1beta/models/gemini-2.5-flash:generateContent",
                        body, {"x-goog-api-key": "mock"}) == 200
    settled(tel, 1)
    row = tel.query("SELECT in_tok, out_tok, usage_source FROM requests "
                    "ORDER BY id DESC LIMIT 1")[0]
    assert row["usage_source"] == "gemini"
    assert row["in_tok"] == mockup._tok(mockup.prompt_text(body))
    assert row["out_tok"] == 410


def test_responses_streamed_e2e_records_usage(proxied, settled):
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.responses_synthetic(1)
    assert _post_stream(base, "/v1/responses", body,
                        {"Authorization": "Bearer mock", "user-agent": "openai-python/1.0"}) == 200
    settled(tel, 1)
    row = tel.query("SELECT api, in_tok, cr_tok, out_tok, reasoning_tok, usage_source "
                    "FROM requests ORDER BY id DESC LIMIT 1")[0]
    assert row["api"] == "responses" and row["usage_source"] == "responses"
    assert row["in_tok"] == mockup._tok(mockup.prompt_text(body)) and row["cr_tok"] == 0
    assert row["out_tok"] == 210 and row["reasoning_tok"] == 64


def _row(tel, settled, n=1):
    settled(tel, n)
    return tel.query("SELECT in_tok, cr_tok, out_tok FROM requests "
                     "ORDER BY id DESC LIMIT 1")[0]


def test_the_mock_bills_a_repeated_prefix_as_a_cache_read(proxied, settled):
    """The meter has to move when the request moves.

    This is the S46/B4 witness: while the mock answered with a fixed usage block, the
    enforce arm and the observe arm of the same traffic printed identical `billed input`
    and `spend`, and `subproto report` contradicted `subproto live` on the same home.
    """
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.synthetic_request(3)
    assert _post_stream(base, "/v1/messages", body, {"x-api-key": "mock"}) == 200
    first = _row(tel, settled)
    assert first["in_tok"] == mockup._tok(mockup.prompt_text(body))
    assert first["cr_tok"] == 0, "nothing was sent before this, so nothing can be cached"
    assert _post_stream(base, "/v1/messages", body, {"x-api-key": "mock"}) == 200
    again = _row(tel, settled, 2)
    assert again["in_tok"] == 0 and again["cr_tok"] == first["in_tok"], again


def test_a_rewritten_prefix_is_billed_as_a_cache_miss(proxied, settled):
    """I2 in the meter, not only in the invariant test.

    A proxy that edits the cached head of the request saves tokens on the body and pays
    for the whole prefix again. The mock is the only party here that knows about prompt
    caching, so this is where that trade becomes visible.
    """
    cfg, tel, srv = proxied
    base = "http://127.0.0.1:%d" % cfg.port
    body = demo.synthetic_request(4)
    assert _post_stream(base, "/v1/messages", body, {"x-api-key": "mock"}) == 200
    before = _row(tel, settled)
    edited = json.loads(json.dumps(body))
    edited["system"] = edited["system"] + " and then a clause nobody had before"
    assert _post_stream(base, "/v1/messages", edited, {"x-api-key": "mock"}) == 200
    after = _row(tel, settled, 2)
    assert after["cr_tok"] < before["in_tok"], "a changed prefix cannot be a full read"
    assert after["in_tok"] > 0, "the rewritten head is re-billed, which is the point"
