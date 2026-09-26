"""T12: the three invariants SPEC says we may never break.

I5  observation mode is byte-for-byte transparent — the body that reaches the
    model is exactly the body the agent sent.
I2  the context slot only ever *appends*; it never rewrites or reorders the
    cached prompt prefix (a rewrite would bust the provider cache and cost more).
I3  the compact slot's protected tail survives even an absurdly small budget —
    recent turns are sacred.
"""

import gzip
import json
import os
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from subproto import demo, heuristics, protocol
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer, analyze_request
from subproto.telemetry import Telemetry


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def proxied(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBPROTO_APPLY", raising=False)
    monkeypatch.delenv("SUBPROTO_ENFORCE", raising=False)
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, Engine(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import fakeup.server as mock
    mock_up = mock.MockUpstream(port=free_port()).start()
    cfg.source = dict(cfg.source, anthropic_upstream="http://127.0.0.1:%d" % mock_up.port)
    try:
        yield cfg, tel, srv
    finally:
        srv.shutdown()
        srv.server_close()
        mock_up.stop()
        tel.close()


def test_I5_observation_is_byte_for_byte(proxied):
    """No slots enforced => the upstream receives the request body verbatim."""
    cfg, tel, srv = proxied
    body = demo.synthetic_request(3, jitter=False, turns=3)
    body["stream"] = False
    body["_mock_echo"] = True
    raw = protocol.dump_body(body)
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/messages" % cfg.port, data=raw,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    assert resp.get("_mock_echo") == body, "observation mode mutated the request"
    # and the response relayed the mock's own fields untouched
    assert resp["role"] == "assistant" and resp["usage"]["output_tokens"] == 320


def test_I2_context_slot_never_touches_the_cached_prefix(tmp_path, monkeypatch):
    """The graph note must land *after* the prefix, not inside it.

    For Anthropic the cached prefix is the top-level `system` string plus the tools
    block, so writing the note into `system` would re-bill every turn as a fresh
    cache write — the exact failure I2 exists to prevent.
    """
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg, graph_path=gp)
    body = demo.synthetic_request(0, jitter=False, turns=4)
    body["messages"][0]["content"] = [{"type": "text",
        "text": "refactor PaymentGateway.retry refund in app/payments/retry.py"}]
    original_system = body["system"]
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    ctx = [d for d in decisions if d["slot"] == "context"][0]
    assert ctx["applied"] is True, "context should have applied"
    assert new_body is not None
    # the prefix is byte-identical, not merely prefix-preserving
    assert new_body["system"] == original_system, "system prefix was rewritten"
    assert "code graph" not in new_body["system"]
    # every turn the agent already sent is unchanged
    assert new_body["messages"][:-1] == body["messages"][:-1]
    # ...and the note sits in the last turn, after the prefix
    assert "code graph" in json.dumps(new_body["messages"][-1])
    # the input body was not mutated in place
    assert body["messages"][-1].get("content") != "code graph"


def test_I2_context_appends_after_an_assistant_turn_as_a_new_user_turn(tmp_path, monkeypatch):
    """Anthropic/OpenAI both require role alternation, so an assistant-final prompt
    takes the note as a following user turn — still after the prefix."""
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg, graph_path=gp)
    body = {
        "model": "claude-sonnet-4-5",
        "system": "you are a coding agent",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text":
                "refactor PaymentGateway.retry refund in app/payments/retry.py"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    }
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert [d for d in decisions if d["slot"] == "context"][0]["applied"] is True
    assert new_body["system"] == "you are a coding agent"
    assert new_body["messages"][:2] == body["messages"]
    assert new_body["messages"][-1]["role"] == "user"
    assert "code graph" in json.dumps(new_body["messages"][-1])


def test_I2_context_reports_not_applied_when_the_shape_has_nowhere_to_append(
        tmp_path, monkeypatch):
    """A body we cannot append to is not an intervention — say so in the record.

    Named in SUBPROTO_APPLY is not the same as did something; the old flag conflated
    the two and let the meter claim savings from a slot that changed nothing.
    """
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg, graph_path=gp)
    body = {"model": "claude-sonnet-4-5", "system": "s", "messages": [
        {"role": "user", "content": [{"type": "text", "text":
            "refactor PaymentGateway.retry refund in app/payments/retry.py"}]},
        {"role": "user", "content": None},
    ]}
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    ctx = [d for d in decisions if d["slot"] == "context"]
    assert ctx and ctx[0]["applied"] is False
    assert new_body is None


def test_I1_named_slot_that_drops_nothing_reports_not_applied(tmp_path, monkeypatch):
    """SUBPROTO_APPLY lists a slot; the slot finds nothing to do => applied False."""
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate,compact,context,effort")
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg)  # no graph, no tools, short conversation
    body = {"model": "claude-sonnet-4-5", "system": "s",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert new_body is None, "nothing should have been rewritten"
    assert decisions, "the slots still record what they would do"
    assert all(d["applied"] is False for d in decisions), \
        [(d["slot"], d["applied"]) for d in decisions]


def test_I2_gemini_context_appends_after_the_last_part(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg, graph_path=gp)
    body = {"contents": [{"role": "user", "parts": [
        {"text": "refactor PaymentGateway.retry refund in app/payments/retry.py"}]}]}
    new_body, decisions = eng.decide("gemini", body, analyze_request(body), cfg)
    assert [d for d in decisions if d["slot"] == "context"][0]["applied"] is True
    parts = new_body["contents"][-1]["parts"]
    assert parts[0] == body["contents"][-1]["parts"][0]
    assert "code graph" in parts[-1]["text"]


def test_I3_tail_protected_under_tiny_budget():
    msgs = []
    for i in range(14):
        kind = "tool_result" if i % 2 else "assistant"
        msgs.append((kind, "stale turn %d " % i + "x" * 400, kind))
    # a budget of 1 token cannot fit even one big block; the tail must still be kept
    flags = heuristics.compact_messages(msgs, budget_tokens=1, protected_tail=4)
    tail = [f for f in flags if f["index"] >= len(msgs) - 4]
    assert all(f["keep"] for f in tail), "protected tail was dropped"
    # and everything dropped is from the head, never the tail
    for f in flags:
        if not f["keep"]:
            assert f["index"] < len(msgs) - 4


# --- I5, the half the echo test cannot see: end-to-end headers ------------------
#
# `content-encoding` is an *end-to-end* header, not a hop-by-hop one. The SDKs ask
# for it (`accept-encoding: gzip`) and some agents gzip their request body, so a
# proxy that drops the header while relaying the bytes untouched hands the far side
# undecodable garbage. This sink records exactly what the proxy forwarded.

class _Sink(object):
    """Stand-in upstream: records the forwarded headers + bytes, replies gzipped."""

    def __init__(self):
        self.seen = {}
        sink = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                sink.seen["headers"] = dict((k.lower(), v) for k, v in self.headers.items())
                sink.seen["body"] = self.rfile.read(length)
                payload = gzip.compress(
                    b'{"id":"gz","role":"assistant","content":[],'
                    b'"usage":{"input_tokens":5,"output_tokens":2}}')
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-encoding", "gzip")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.port = free_port()
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _proxy_to(tmp_path, upstream_port, monkeypatch):
    monkeypatch.delenv("SUBPROTO_APPLY", raising=False)
    monkeypatch.delenv("SUBPROTO_ENFORCE", raising=False)
    cfg = Config.load(None, data_dir=str(tmp_path), port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    cfg.source = dict(cfg.source, anthropic_upstream="http://127.0.0.1:%d" % upstream_port)
    tel = Telemetry(cfg.db_path)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, Engine(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return cfg, tel, srv


def _row_after(tel, want=1, deadline=5.0):
    """The proxy records *after* it flushes the response, so poll for the row."""
    end = time.time() + deadline
    while time.time() < end:
        rows = tel.query("SELECT in_tok, out_tok, status, err FROM requests")
        if len(rows) >= want:
            return rows
        time.sleep(0.02)
    return tel.query("SELECT in_tok, out_tok, status, err FROM requests")


def test_I5_gzip_request_body_reaches_the_upstream_intact(tmp_path, monkeypatch):
    sink = _Sink()
    cfg, tel, srv = _proxy_to(tmp_path, sink.port, monkeypatch)
    gz = gzip.compress(protocol.dump_body(
        {"model": "claude-sonnet-4-5", "max_tokens": 8,
         "messages": [{"role": "user", "content": "hi"}]}))
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/messages" % cfg.port, data=gz,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        assert sink.seen["body"] == gz, "compressed request body was altered"
        assert sink.seen["headers"].get("content-encoding") == "gzip", \
            "content-encoding stripped: the upstream now reads gzip bytes as JSON"
        rows = _row_after(tel)
        assert rows, "the request was never recorded"
        assert rows[0]["status"] == 200, rows[0]["err"]
        assert (rows[0]["in_tok"], rows[0]["out_tok"]) == (5, 2), \
            "usage of a gzipped response was not reconstructed"
    finally:
        srv.shutdown()
        srv.server_close()
        sink.stop()
        tel.close()


def test_I5_gzip_response_header_is_forwarded_to_the_client(tmp_path, monkeypatch):
    sink = _Sink()
    cfg, tel, srv = _proxy_to(tmp_path, sink.port, monkeypatch)
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/messages" % cfg.port,
        data=protocol.dump_body({"model": "claude-sonnet-4-5", "max_tokens": 8,
                                 "messages": [{"role": "user", "content": "hi"}]}),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            encoding = r.headers.get("content-encoding")
            declared = r.headers.get("content-length")
            payload = r.read()
        assert encoding == "gzip", (
            "the proxy dropped the label from a gzipped body: the SDK now tries to "
            "json.loads raw gzip bytes")
        # content-length is not hop-by-hop on a response we never touch: dropping it
        # turns an HTTP/1.1 keep-alive body into one the client cannot frame.
        assert declared == str(len(payload)), (
            "the proxied response declares content-length %r for %d body bytes"
            % (declared, len(payload)))
        assert json.loads(gzip.decompress(payload).decode())["id"] == "gz"
    finally:
        srv.shutdown()
        srv.server_close()
        sink.stop()
        tel.close()
