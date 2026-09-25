from subproto import heuristics
from subproto.demo import synthetic_request, tool_specs
from subproto.proxy import analyze_request


def test_tool_gate_keeps_core_and_drops_offtopic_mcp():
    tools = tool_specs(24)
    decisions = heuristics.tool_gate(tools, "read the retry.py file and run pytest")
    kept = {d["target"] for d in decisions if d["keep"]}
    assert "read" in kept
    assert "mcp__slack__post" not in kept or len(kept) >= heuristics.CORE_FLOOR
    assert len(kept) < len(tools)


def test_compact_protects_tail_and_drops_old_tool_results():
    msgs = []
    for i in range(12):
        role = "user" if i == 0 else ("assistant" if i % 2 else "user")
        kind = "tool_result" if i % 2 == 1 and i < 8 else role
        msgs.append((role, "x" * 3000 if kind == "tool_result" else "short note", kind))
    flags = heuristics.compact_messages(msgs, budget_tokens=2000, protected_tail=4)
    tail = flags[-4:]
    assert all(f["keep"] for f in tail)
    assert any(not f["keep"] for f in flags)


def test_route_effort_trivial_vs_complex():
    feats = {"paths": ["a.py"], "cat_chars": {"tool_result": 100}, "user_turns": 1}
    low = heuristics.route_effort(feats, "fix typo in README", 500)
    assert low["tier"] == "low" and low["small_model_ok"]
    feats_big = {"paths": ["a.py", "b.py", "c.py"],
                 "cat_chars": {"tool_result": 50000}, "user_turns": 6}
    high = heuristics.route_effort(feats_big, "refactor the auth migration and concurrency", 30000)
    assert high["tier"] == "high"


def test_analyze_request_shape():
    body = synthetic_request(1, jitter=False)
    a = analyze_request(body)
    assert a["tool_count"] == 24
    assert a["sys_chars"] > 1000
    assert a["est_in_tok"] > 0
