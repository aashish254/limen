"""S29 — implicit labels: the drop that the traffic itself contradicted.

Every case here is *constructed* traffic: two turns whose bodies and decisions are
recorded exactly the way the proxy records them (gzip body under `<dialect>_<sha>.json.gz`
+ a telemetry row with the `decisions` blob), because the thing under test is what a
later turn does to an earlier turn's evictions. Nothing is mocked inside `implicit.py`
itself — the proofs come from `compiler.compile_turn`, so the eviction indices, targets
and `applied` flags are the real artifact.
"""

import gzip
import json
import os

import pytest

from subproto import dataset, implicit, protocol
from subproto.compiler import compile_turn
from subproto.config import Config
from subproto.telemetry import Telemetry

RETRY = "app/payments/retry.py"
LEDGER = "db/migrations/0007_refund_ledger.sql"
TASK = "why does the refund retry swallow the gateway error?"


def _dump(path, marker, n=220):
    """File content as a real Read result looks: a header naming the path, then lines."""
    lines = ["Contents of /repo/%s, from line 1-%d (total %d lines)" % (path, n, n)]
    lines += ["%4d  def %s_line_%d():\n%4d      return %d" % (i, marker, i, i, i)
              for i in range(n)]
    return "\n".join(lines)


def _read_pair(idx, path, marker, n=220):
    """The assistant call + tool result pair for one file read, Anthropic shape."""
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_%d" % idx, "name": "Read",
             "input": {"file_path": path}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_%d" % idx,
             "content": _dump(path, marker, n)}]},
    ]


def _bare_pair(idx, path, n=60):
    """A read whose result carries no path header — only the call names the file."""
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_%d" % idx, "name": "Read",
             "input": {"file_path": path}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_%d" % idx,
             "content": "def retry():\n    return 1\n" * n}]},
    ]


def _search_pair(idx, listed, tok="Grep"):
    """A search call + a result that only *lists* the file."""
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_%d" % idx, "name": tok,
             "input": {"pattern": "retry", "path": listed}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_%d" % idx,
             "content": "%s:12: def retry()\n%s:4: x" % (listed, "app/payments/api.py")}]},
    ]


def _turn_a():
    """A turn whose oldest read is the one the task is about (so it loses on ratio)."""
    msgs = [{"role": "user", "content": TASK}]
    msgs += _read_pair(1, RETRY, "retry", n=220)
    msgs += _read_pair(2, "app/legacy/notes.md", "notes", n=30)
    msgs += _read_pair(3, "app/legacy/flags.md", "flags", n=30)
    msgs += [{"role": "user", "content": "which change do I ship?"}]
    return {"model": "claude-sonnet-x", "system": "You are a coding agent. " * 20,
            "messages": msgs}


def _turn_b(extra):
    """Turn A's history with one more fetch appended — the shape of a re-read."""
    body = json.loads(json.dumps(_turn_a()))
    body["messages"] += extra
    return body


def _home(tmp_path):
    config = Config.load(None, data_dir=str(tmp_path / "home"), store_bodies=True)
    config.ensure_dirs()
    return config


def _telemetry(config):
    return Telemetry(config.db_path)


def _compile(body, budget=1400, enforce=("tool_gate", "compact", "context")):
    """The real proof, attached the way engine._decide_compiled attaches it."""
    from subproto.proxy import analyze_request

    sha = protocol.sha256_12(protocol.dump_body(body))
    _new, decisions, proof = compile_turn("anthropic", body, analyze_request(body), None,
                                          implicit.last_user_text(body), budget=budget,
                                          body_sha=sha, enforce=enforce)
    if proof is not None and decisions:
        decisions[0]["proof"] = proof
    return sha, decisions, proof


def _record(config, telemetry, body, decisions, ts):
    raw = protocol.dump_body(body)
    sha = protocol.sha256_12(raw)
    os.makedirs(config.bodies_dir, exist_ok=True)
    with gzip.open(os.path.join(config.bodies_dir, "anthropic_%s.json.gz" % sha), "wb") as f:
        f.write(raw)
    telemetry.record({"ts": ts, "api": "anthropic", "path": "/v1/messages",
                      "client": "claude-code", "model": "claude-sonnet-x", "status": 200,
                      "body_sha": sha, "decisions": decisions})
    return sha


def _session(tmp_path, turns, enforce=("tool_gate", "compact", "context"), budgets=None):
    """Record `turns` (bodies) in order with their compiled decisions.

    A per-turn budget is exposed because the fixture needs two different ones: turn A
    must be tight enough to evict its oldest read, while a later turn that replays that
    read in its protected tail needs room or it reports `over_budget` and stores no
    decisions at all (and an unrecorded turn cannot contradict anything).
    """
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    budgets = budgets or [1400] * len(turns)
    proofs = []
    for i, body in enumerate(turns):
        sha, decisions, proof = _compile(body, budget=budgets[i], enforce=enforce)
        assert sha == protocol.sha256_12(protocol.dump_body(body))
        proofs.append(proof)
        _record(config, telemetry, body, decisions, 1000.0 + 30 * i)
    return config, telemetry, proofs[-1]


# ---------------------------------------------------------------- the detectors

def test_a_re_read_file_convicts_the_drop_that_evicted_it(tmp_path):
    config, telemetry, proof = _session(
        tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
        budgets=[1400, 4200])
    assert [d["id"] for d in proof["dropped"] if d["kind"] == "message"], \
        "the fixture must actually evict a read, or it proves nothing"
    out = implicit.harvest(config, telemetry)
    entry = out["labels"][str(_first(telemetry))]
    label = list(entry["targets"].values())[0]
    assert label["verdict"] == "keep"
    assert label["source"] == "implicit:re-read"
    assert protocol.same_path(label["path"], RETRY)
    assert label["regret_tok"] == protocol.approx_tokens(_dump(RETRY, "retry")), \
        "the price is exactly the second read, no more"
    assert label["evicted_tok"] == label["regret_tok"]
    assert RETRY in label["evidence"]
    assert "was 1" in label["evidence"]
    summary = out["summary"]
    assert summary["wrong_drops_enforced"] == 1
    assert summary["refetch_tok_paid"] == label["regret_tok"]


def _first(telemetry):
    return telemetry.query("SELECT min(id) m FROM requests")[0]["m"]


def test_shadow_mode_reports_the_wrong_drop_without_inventing_a_cost(tmp_path):
    """With nothing enforced the agent re-read anyway — the bytes were never lost.

    The same observation is still a label (the content was in live use), but the token
    price belongs to `re-read` only, because only an enforced cut made it pay twice.
    """
    config, telemetry, proof = _session(
        tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
        enforce=(), budgets=[1400, 4200])
    out = implicit.harvest(config, telemetry)
    summary = out["summary"]
    assert summary["wrong_drops_enforced"] == 0
    assert summary["wrong_drops_shadow"] == 1
    assert summary["refetch_tok_paid"] == 0
    label = list(out["labels"].values())[0]["targets"]
    assert list(label.values())[0]["source"] == "implicit:would-be-live"
    assert list(label.values())[0]["regret_tok"] == 0


def test_a_grep_that_lists_the_file_is_not_a_second_fetch(tmp_path):
    """Looking for a file and paying for it again are different events."""
    config, telemetry, _ = _session(tmp_path, [_turn_a(), _turn_b(_search_pair(9, RETRY))],
                                    budgets=[1400, 4200])
    out = implicit.harvest(config, telemetry)
    assert out["summary"]["wrong_drops"] == 0, json.dumps(out["labels"], indent=1)
    assert out["summary"]["evicted_reads_seen"] > 0, "the drop still happened"


def test_a_turn_with_no_traffic_after_it_is_never_labelled(tmp_path):
    config, telemetry, _ = _session(tmp_path, [_turn_a()])
    out = implicit.harvest(config, telemetry)
    assert out["labels"] == {}
    assert out["summary"]["wrong_drops"] == 0
    # the proof dropped one message, whose file has two spellings on the wire; one
    # read evicted is one entry, not one per way of naming it
    assert out["summary"]["evicted_reads_seen"] == 1, "the drop happened; nothing contradicted it"


def test_a_correction_labels_the_previous_turn_and_stays_a_turn_verdict(tmp_path):
    """A complaint names the turn, not a candidate — so it never flips a keep/drop."""
    later = _turn_b([{"role": "user",
                      "content": "no, that's wrong — the retry helper is not in that file"}])
    config, telemetry, _ = _session(tmp_path, [_turn_a(), later], budgets=[1400, 4200])
    out = implicit.harvest(config, telemetry)
    rid = str(_first(telemetry))
    slots = out["labels"][rid]["slots"]
    assert slots["turn"]["source"] == "implicit:correction"
    assert slots["turn"]["verdict"] == "bad"
    assert "no, that's wrong" in slots["turn"]["evidence"]


def test_the_same_body_twice_reads_as_a_re_run(tmp_path):
    config, telemetry, _ = _session(tmp_path, [_turn_a(), _turn_a()])
    out = implicit.harvest(config, telemetry)
    rid = str(_first(telemetry))
    slots = out["labels"][rid]["slots"]
    assert slots["turn"]["source"] == "implicit:re-run"


def test_a_body_that_was_never_stored_is_counted_not_guessed(tmp_path):
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    sha, decisions, _ = _compile(_turn_a())
    _record(config, telemetry, _turn_a(), decisions, 1000.0)
    for name in os.listdir(config.bodies_dir):
        os.remove(os.path.join(config.bodies_dir, name))
    out = implicit.harvest(config, telemetry)
    assert out["summary"]["skipped_no_body"] == 1
    assert out["summary"]["bodies_available"] == 0
    assert out["labels"] == {}


# ------------------------------------------------------- the store, kept separate

def test_run_writes_its_own_file_and_leaves_human_labels_alone(tmp_path):
    config, telemetry, _ = _session(
        tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
        budgets=[1400, 4200])
    dataset.save_labels(config, {"1": {"compact": "good", "reason": "human said so"}})
    out = implicit.run(config, telemetry)
    assert os.path.exists(implicit.implicit_path(config))
    stored = implicit.load_implicit(config)
    assert stored == out["labels"]
    # the human store is untouched, and still answers in its own shape
    assert dataset.load_labels(config) == {"1": {"compact": "good",
                                                "reason": "human said so"}}
    assert out["summary"]["stored"] == len(stored)


def test_harvest_survives_a_corrupt_decisions_blob(tmp_path):
    config, telemetry, _ = _session(
        tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
        budgets=[1400, 4200])
    with telemetry._lock:
        telemetry._conn.execute("UPDATE requests SET decisions = '{oops' WHERE id = 1")
        telemetry._conn.commit()
    out = implicit.harvest(config, telemetry)
    assert isinstance(out["summary"]["wrong_drops"], int)


# ------------------------------------------------------------- read anatomy units

def test_fetch_counts_are_cumulative_over_history(tmp_path):
    """Counts grow with the replayed history, on one key per file."""
    counts, toks = implicit.read_counts(_turn_b(_read_pair(9, RETRY, "retry")))
    assert [k for k in counts if protocol.same_path(k, RETRY)] == ["/repo/" + RETRY]
    assert counts["/repo/" + RETRY] == 2
    assert counts["/repo/app/legacy/notes.md"] == 1
    assert implicit.read_counts(_turn_a())[0]["/repo/" + RETRY] == 1
    assert toks["/repo/" + RETRY] == protocol.approx_tokens(_dump(RETRY, "retry"))


def test_the_two_spellings_of_one_file_are_folded_into_one_fetch(tmp_path):
    """A call names `app/x/retry.py`, its result header names `/repo/app/x/retry.py`.

    Counting them per key would report two files, and the second read of *one* file
    would never look like growth — which is the whole signal.
    """
    counts, toks = implicit.read_counts(_turn_b(_bare_pair(9, RETRY)))
    assert [k for k in counts if protocol.same_path(k, RETRY)] == ["/repo/" + RETRY]
    assert counts["/repo/" + RETRY] == 2, "one file, two fetches, not two files"
    assert toks["/repo/" + RETRY] == protocol.approx_tokens(
        _bare_pair(9, RETRY)[1]["content"][0]["content"]), \
        "the newest copy's price, not the largest one on the wire"


def test_an_openai_tool_call_names_the_file_the_result_does_not(tmp_path):
    """In the chat shape the path exists only in the call's arguments."""
    body = {"model": "gpt-x", "messages": [
        {"role": "user", "content": TASK},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file",
                          "arguments": json.dumps({"path": LEDGER})}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file",
         "content": "CREATE TABLE refund_ledger (id int);\n" * 40},
    ]}
    counts, _toks = implicit.read_counts(body)
    assert list(counts) == [LEDGER]
    assert counts[LEDGER] == 1


def test_a_gemini_function_response_is_a_fetch_too():
    body = {"systemInstruction": {"parts": [{"text": "agent"}]}, "contents": [
        {"role": "user", "parts": [{"text": TASK}]},
        {"role": "model", "parts": [{"functionCall": {
            "name": "read_file", "args": {"file": "app/payments/retry.py"}}}]},
        {"role": "user", "parts": [{"functionResponse": {
            "name": "read_file", "response": {"content": "def retry(): pass"}}}]},
    ]}
    counts, _ = implicit.read_counts(body)
    assert list(counts) == ["app/payments/retry.py"]
    assert counts["app/payments/retry.py"] == 1


def test_aligned_messages_line_up_with_normalized_indices():
    body = _turn_a()
    norm = protocol.normalize_messages(body)
    raws = implicit.aligned_messages(body)
    assert len(raws) == len(norm)
    assert raws[0] == {} and norm[0][2] == "system"
    for i, m in enumerate(raws[1:], 1):
        assert m is body["messages"][i - 1]


def test_evicted_paths_follow_the_call_when_the_result_says_nothing():
    body = {"model": "gpt-x", "messages": [
        {"role": "user", "content": TASK},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file",
                          "arguments": json.dumps({"path": LEDGER})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "CREATE TABLE refund_ledger;"},
    ]}
    norm = protocol.normalize_messages(body)
    raws = implicit.aligned_messages(body)
    # the tool result is normalized index 3 (no system string in this shape: 0,1,2)
    idx = next(i for i, (_r, _t, k) in enumerate(norm) if k == "tool_result")
    paths = implicit._evicted_paths(raws, norm, idx)
    assert paths == [LEDGER]


def test_evictions_reads_both_the_compiled_proof_and_the_per_slot_candidates():
    _sha, compiled, proof = _compile(_turn_a())
    rows = implicit.evictions(compiled)
    assert rows, "a compiled turn must expose what it cut"
    assert all(r[3] is not None for r in rows), "proof keeps the message index"
    per_slot = [{"slot": "compact", "applied": True,
                 "candidates": [{"target": "tool_result#2", "keep": False, "tokens": 90,
                                 "index": 2},
                                {"target": "user#1", "keep": True, "tokens": 20,
                                 "index": 1}]}]
    assert implicit.evictions(per_slot) == [("message:tool_result#2", "tool_result#2",
                                            90, 2, True)]


def test_the_window_bounds_how_far_a_re_read_is_blamed(tmp_path):
    """A file fetched 40 turns later is a new task, not a consequence of this drop."""
    turns = [_turn_a()]
    for i in range(25):
        turns.append(_turn_b([{"role": "user", "content": "and the ledger? (%d)" % i}]))
    turns.append(_turn_b(_read_pair(9, RETRY, "retry")))
    # only turn 0 evicts; the middle turns get room enough to keep everything, so the
    # blame distance really is 26 turns and not 1.
    budgets = [1400] + [9000] * 25 + [4200]
    config, telemetry, _ = _session(tmp_path, turns, budgets=budgets)
    assert implicit.harvest(config, telemetry, window=5)["summary"]["wrong_drops"] == 0
    assert implicit.harvest(config, telemetry, window=40)["summary"]["wrong_drops"] == 1


def test_a_stale_enough_re_read_is_out_of_time_not_out_of_turns(tmp_path):
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    a = _turn_a()
    _record(config, telemetry, a, _compile(a, budget=1400)[1], 1000.0)
    b = _turn_b(_read_pair(9, RETRY, "retry"))
    _record(config, telemetry, b, _compile(b, budget=4200)[1],
            1000.0 + implicit.WINDOW_S + 60)
    assert implicit.harvest(config, telemetry)["summary"]["wrong_drops"] == 0


def test_a_neighbouring_search_does_not_borrow_the_blame():
    """A dropped read is the content of the file it read, not of what rode along.

    Agents call Read and Grep in the same turn, so the attribution walk-back sees both
    calls; only the read's own file may be blamed.
    """
    msgs = [{"role": "user", "content": TASK},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_g", "name": "Grep",
                 "input": {"pattern": "refund", "path": LEDGER}},
                {"type": "tool_use", "id": "tu_r", "name": "Read",
                 "input": {"file_path": RETRY}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_g",
                 "content": "%s:1: CREATE TABLE refund_ledger" % LEDGER},
                {"type": "tool_result", "tool_use_id": "tu_r",
                 "content": _dump(RETRY, "retry")}]}]
    body = {"model": "claude-sonnet-x", "messages": msgs}
    norm = protocol.normalize_messages(body)
    raws = implicit.aligned_messages(body)
    names = implicit._call_names(raws)
    idx = next(i for i, (_r, _t, k) in enumerate(norm) if k == "tool_result")
    paths = implicit._evicted_paths(raws, norm, idx, names)
    assert paths and all(protocol.same_path(p, RETRY) for p in paths)
    assert not any(protocol.same_path(p, LEDGER) for p in paths), \
        "the grep named the migration; the dropped turn was not its content"


def test_one_evicted_file_counts_once_whatever_the_spelling(tmp_path):
    """The dedupe in the harvest, witnessed against the raw attribution list."""
    a = _turn_a()
    config, telemetry, _ = _session(tmp_path, [a])
    out = implicit.harvest(config, telemetry)
    norm = protocol.normalize_messages(a)
    raws = implicit.aligned_messages(a)
    spelled = [p for _pt, _tg, _tk, idx, _ap in implicit.evictions(_compile(a)[1])
               for p in implicit._evicted_paths(raws, norm, idx, implicit._call_names(raws))]
    assert len(spelled) == 2 and spelled[0] != spelled[1], \
        "the header and the call name the same file differently"
    assert out["summary"]["evicted_reads_seen"] == 1


def test_the_nearest_eviction_is_the_one_blamed(tmp_path):
    """Two turns cut the same read, one re-fetch: that is one regret, not two.

    Every request replays the whole history, so a drop that stays dropped looks
    identical on 20 consecutive turns. Billing the re-read once per turn would report
    the same 2954 tokens as the price of the whole conversation.
    """
    mid = _turn_b([{"role": "user", "content": "and what about the ledger?"}])
    turns = [_turn_a(), mid, _turn_b(_bare_pair(9, RETRY))]
    config, telemetry, _ = _session(tmp_path, turns, budgets=[1400, 1400, 6000])
    out = implicit.harvest(config, telemetry)
    ids = sorted(int(k) for k in out["labels"])
    assert out["summary"]["evicted_reads_seen"] == 2, "both turns really did cut it"
    assert ids == [2], json.dumps(out["labels"], indent=1)
    label = list(out["labels"]["2"]["targets"].values())[0]
    assert label["source"] == "implicit:re-read"
    assert out["summary"]["wrong_drops"] == 1
    assert out["summary"]["refetch_tok_paid"] == label["regret_tok"]


def test_a_body_that_was_never_spooled_does_not_reset_the_baseline(tmp_path):
    """A gap in the recorded bodies must not look like every file was re-fetched.

    Turn 2 replays turn 0's read exactly once, so nothing was re-read. If the missing
    body in between reset the baseline, its own counts would read as new fetches and
    convict turn 0's drop a second time.
    """
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    a = _turn_a()
    _record(config, telemetry, a, _compile(a, budget=1400)[1], 1000.0)
    mid = _turn_b([{"role": "user", "content": "and what about the ledger?"}])
    mid_sha = _record(config, telemetry, mid, _compile(mid, budget=1400)[1], 1010.0)
    later = _turn_b([{"role": "user", "content": "still deciding"}])
    _record(config, telemetry, later, _compile(later, budget=1400)[1], 1020.0)
    for suffix in (".json.gz", ".json"):
        p = os.path.join(config.bodies_dir, "anthropic_%s%s" % (mid_sha, suffix))
        if os.path.exists(p):
            os.remove(p)
    out = implicit.harvest(config, telemetry)
    assert out["summary"]["skipped_no_body"] == 1
    assert out["summary"]["wrong_drops"] == 0, json.dumps(out["labels"], indent=1)


def test_a_search_result_is_the_content_of_none_of_the_files_it_lists():
    """Attribution has to know which call produced a dropped result.

    A `Grep` hit-list names a dozen paths; if the eviction of that result is read as
    the eviction of `retry.py`, a later re-read of `retry.py` bills regret to a drop
    that never removed its content.
    """
    body = {"model": "claude-sonnet-x", "messages":
            [{"role": "user", "content": TASK}] + _search_pair(5, RETRY)}
    norm = protocol.normalize_messages(body)
    raws = implicit.aligned_messages(body)
    idx = next(i for i, (_r, _t, k) in enumerate(norm) if k == "tool_result")
    assert RETRY in protocol.extract_paths(norm[idx][1]), "the text does list it"
    names = implicit._call_names(raws)
    assert names["tu_5"] == "grep"
    assert implicit._evicted_paths(raws, norm, idx, names) == []
    assert implicit._evicted_paths(raws, norm, idx) == [RETRY, "app/payments/api.py"], \
        "without the call map it is attributed, which is why harvest passes one"


def test_a_pure_search_drop_blames_nobody_even_when_a_read_rode_along():
    """The chat shape puts the calls one message earlier, so the walk-back sees them.

    A dropped `grep` result is the content of nothing, but two messages up sits the
    `read_file` call whose file the search was looking for; without the all-search
    check, blame lands on the wrong eviction.
    """
    body = {"model": "gpt-x", "messages": [
        {"role": "user", "content": TASK},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": RETRY})}},
            {"id": "c2", "type": "function", "function": {
                "name": "grep", "arguments": json.dumps({"pattern": "refund"})}}]},
        {"role": "tool", "tool_call_id": "c2", "name": "grep",
         "content": "%s:12: def retry()" % RETRY},
    ]}
    norm = protocol.normalize_messages(body)
    raws = implicit.aligned_messages(body)
    idx = next(i for i, (_r, _t, k) in enumerate(norm) if k == "tool_result")
    assert implicit._evicted_paths(raws, norm, idx, implicit._call_names(raws)) == []
    assert implicit._evicted_paths(raws, norm, idx) == [RETRY], \
        "and it is the call map that stops it"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
