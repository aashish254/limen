"""T12: the three invariants SPEC says we may never break.

I5  observation mode is byte-for-byte transparent — the body that reaches the
    model is exactly the body the agent sent.
I2  the context slot only ever *appends*; it never rewrites or reorders the
    cached prompt prefix (a rewrite would bust the provider cache and cost more).
I3  the compact slot's protected tail survives even an absurdly small budget —
    recent turns are sacred.
"""

import json
import os
import socket
import threading
import time
import urllib.request

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


def test_I2_context_slot_only_appends(tmp_path, monkeypatch):
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
    assert new_body is not None, "context should have applied"
    # The cached prefix (here: the system prompt) is preserved as a strict prefix
    # and the graph note is appended after it — never a rewrite, never a reorder.
    assert new_body["system"].startswith(original_system), "prefix was rewritten"
    assert len(new_body["system"]) > len(original_system)
    assert "code graph" in new_body["system"][len(original_system):]
    # the message list is untouched
    assert new_body["messages"] == body["messages"]


def test_I2_context_appends_into_first_message_without_system(tmp_path, monkeypatch):
    """When there is no top-level system string, the injection must append to the
    first message's content blocks — still prefix-preserving, never a rewrite."""
    monkeypatch.setenv("SUBPROTO_APPLY", "context")
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")
    from subproto import graph
    gp = str(tmp_path / "g.json")
    graph.save(graph.build(repo), gp)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg, graph_path=gp)
    body = {
        "model": "claude-sonnet-4-5",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text":
                "refactor PaymentGateway.retry refund in app/payments/retry.py"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    }
    original_first = list(body["messages"][0]["content"])
    new_body, _ = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert new_body is not None
    new_first = new_body["messages"][0]["content"]
    assert new_first[:len(original_first)] == original_first, "prefix was rewritten"
    assert len(new_first) == len(original_first) + 1
    assert "code graph" in new_first[-1]["text"]
    assert new_body["messages"][1:] == body["messages"][1:]


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
