"""v5 S25: the Context Compiler's joint budget, its retention proof, and its gates.

The headline claim is *accuracy at equal spend*, not tokens: one budget shared by
messages, tools and files must keep the evidence the task is about even where the
per-slot pipeline — which spends two independent budgets and cannot see the code
graph — drops it.
"""

import json
import os
import re

import pytest

from subproto import cli, compiler, dataset, graph as graph_mod, heuristics, protocol
from subproto.config import Config
from subproto.engine import ALL_SLOTS, Engine
from subproto.proxy import analyze_request
from subproto.telemetry import Telemetry

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "fixtures", "sample_repo")
RETRY = "app/payments/retry.py"
TASK = ("Refactor the retry path in %s so a swallowed APIConnectionError is caught "
        "and surfaced in app/payments/errors.py." % RETRY)
ENFORCE = ("tool_gate", "compact", "context")
# A query that names no file, for the tests that must separate *index* coupling from
# the weaker "the user typed this path" rule.
SYMPTOM = "quangle flux heptabase swallows the connection error"
QUANGLE = "app/pay/quangle_client.py"
# S27: a needed read in a file with no AST. The task below names no path, so the only
# thing that can save it is the index having a symbol for a migration.
LEDGER = "db/migrations/0007_refund_ledger.sql"
LEDGER_TASK = ("the refund ledger rows never land, the write is swallowed somewhere "
               "before the insert")


def _file_content(path, lines):
    return "# %s\n" % path + "".join(
        "def f_%d(x):\n    return x + %d  # padding\n" % (j % 40, j) for j in range(lines))


TOOLS = [{"name": n, "description": "tool %s does something unrelated here" % n,
          "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}
         for n in ("mcp__browser__click", "mcp__browser__fill", "exitplanmode",
                   "killshell", "read", "write", "edit", "bash")]


def _reads(task, pairs, tail=()):
    """An Anthropic-shaped session: one task turn, then a Read call + result per pair."""
    msgs = [{"role": "user", "content": task}]
    for i, (path, lines) in enumerate(pairs):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "u%d" % i, "name": "Read",
             "input": {"file_path": path}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u%d" % i,
             "content": _file_content(path, lines)}]})
    msgs.extend(tail)
    return {"model": "claude-sonnet-4-5", "max_tokens": 1024,
            "system": "You are a coding agent. " * 30, "tools": TOOLS,
            "messages": msgs, "stream": False}


_SCROLL = [{"role": "assistant", "content": "which of those do I change?"},
           {"role": "user", "content": "the retry one"}]


def _body():
    """A session that read the task's file first, then four legacy dumps."""
    return _reads(TASK, [(RETRY, 400), ("app/legacy/a.py", 400), ("app/legacy/b.py", 400),
                         ("app/legacy/c.py", 400), ("app/legacy/d.py", 2),
                         ("app/legacy/e.py", 2)])


@pytest.fixture(scope="module")
def repo_graph():
    return graph_mod.build(REPO)


@pytest.fixture(scope="module")
def symptom_graph(tmp_path_factory):
    """An index whose top hit for a path-free query is the file the session read."""
    root = tmp_path_factory.mktemp("symptom-repo")
    pay = root / "app" / "pay"
    pay.mkdir(parents=True)
    other = root / "app" / "other"
    other.mkdir(parents=True)
    (pay / "quangle_client.py").write_text(
        '"""quangle flux heptabase swallows connection error retries."""\n'
        "def quangle_flux():\n    return 'heptabase swallows the quangle error'\n"
        "def flux_heptabase():\n    return quangle_flux()  # quangle flux heptabase\n")
    (other / "decoy.py").write_text(
        "def unrelated():\n    return 1  # quangle flux heptabase swallows connection\n")
    return graph_mod.build(str(root))


@pytest.fixture(scope="module")
def schema_graph(tmp_path_factory):
    """S27: an index whose only signal for the file the session read is a DDL object."""
    root = tmp_path_factory.mktemp("schema-repo")
    mig = root / "db" / "migrations"
    mig.mkdir(parents=True)
    (root / "app" / "other").mkdir(parents=True)
    (mig / "0007_refund_ledger.sql").write_text(
        "-- bring the refund ledger online\n"
        "CREATE TABLE public.refund_ledger (id serial primary key, state text);\n"
        "INSERT INTO refund_ledger (state) VALUES ('pending');\n")
    (root / "app" / "other" / "decoy.py").write_text("def unrelated():\n    return 1\n")
    return graph_mod.build(str(root))


def _session(n_legacy, legacy_lines, retry_lines=400):
    """The task's one big read, then `n_legacy` medium dumps, then a short tail."""
    return _reads(TASK, [(RETRY, retry_lines)] + [("app/legacy/p%d.py" % i, legacy_lines)
                                                  for i in range(n_legacy)], tail=_SCROLL)


def _compile(body, graph, budget=None, query=TASK):
    analysis = analyze_request(body)
    budget = budget if budget is not None else compiler.default_budget(analysis)
    _, _, proof = compiler.compile_turn("anthropic", body, analysis, graph, query,
                                        budget=budget, enforce=ENFORCE)
    return budget, proof


def _kept_msgs(proof):
    return set(c["id"] for c in proof["kept"] if c["kind"] == "message")


@pytest.fixture
def cfg(tmp_path):
    c = Config.load(None, data_dir=str(tmp_path / "home"), port=8899, store_bodies=False)
    c.ensure_dirs()
    return c


@pytest.fixture
def compiled(repo_graph):
    """Compile _body() at the budget the per-slot compact slot would have used."""
    body = _body()
    analysis = analyze_request(body)
    budget = max(compiler.MIN_BUDGET, int(analysis["est_in_tok"] * compiler.TARGET_FRACTION))
    new_body, decisions, proof = compiler.compile_turn(
        "anthropic", body, analysis, repo_graph, TASK, budget=budget,
        body_sha="sha-under-test", enforce=ENFORCE)
    return body, analysis, budget, new_body, decisions, proof


# --- the optimiser -------------------------------------------------------------

def test_plan_books_protected_items_first():
    cands = [compiler.Candidate("message", "m#1", 100, 0.9, protected=True),
             compiler.Candidate("message", "m#2", 100, 0.1),
             compiler.Candidate("message", "m#3", 50, 0.8)]
    p = compiler.plan(cands, 160)
    assert [c.id for c in p["kept"]] == ["m#1", "m#3"]
    assert [c.id for c in p["dropped"]] == ["m#2"]
    assert p["tokens_after"] <= 160 and p["protected_tokens"] == 100


def test_plan_reports_over_budget_instead_of_truncating_the_tail():
    """I3: a budget too small for the sacred set is a report, not a silent cut."""
    tail = compiler.Candidate("message", "m#1", 900, 1.0, protected=True)
    p = compiler.plan([tail, compiler.Candidate("message", "m#2", 10, 0.5)], 500)
    assert p["over_budget"] and not p["kept"] and not p["dropped"]
    assert "protected set needs 900" in p["reason"]
    assert p["kept"] == [] and tail.keep is True


def test_plan_is_deterministic_under_input_order():
    def pool():
        return [compiler.Candidate("tool", t, tok, val) for t, tok, val in
                (("read", 90, 0.4), ("bash", 60, 0.4), ("mcp__x", 30, 0.0), ("grep", 60, 0.42))]
    a = compiler.plan(pool(), 150)
    b = compiler.plan(list(reversed(pool())), 150)
    assert [c.id for c in a["kept"]] == [c.id for c in b["kept"]]
    assert a["tokens_after"] == b["tokens_after"]


# --- the accuracy claim (S23's unit-level half) --------------------------------

def test_joint_budget_keeps_the_evidence_per_slot_drops(compiled):
    body, analysis, budget, new_body, decisions, proof = compiled
    per_slot_drop = [f["index"] for f in
                     heuristics.compact_messages(protocol.normalize_messages(body), budget)
                     if not f["keep"]]
    assert 3 in per_slot_drop, "precondition: per-slot evicts the retry.py read"
    kept_msgs = set(c["id"] for c in proof["kept"] if c["kind"] == "message")
    assert "tool_result#3" in kept_msgs, "the joint budget must buy back the graph's #1 file"
    assert RETRY in protocol.content_to_text(new_body["messages"][2]), \
        "the kept turn must still carry the file's content"
    assert proof["tokens_after"] <= budget


def test_booking_the_evidence_beats_the_ratio_race(repo_graph, monkeypatch):
    """The claim S23 needs: equal budget, equal-or-lower spend, evidence kept.

    A 4.7k-token read has a worse value-per-token ratio than forty 300-token dumps,
    so a pure greedy trades it away. Booking it as evidence is what stops that — and
    this mutation check says so: with the booking cap at zero the same turn loses it.
    """
    body = _session(40, 40)
    budget, on = _compile(body, repo_graph)
    ev = next(c for c in compiler.build_candidates(body, analyze_request(body), repo_graph,
                                                   TASK)[0]
              if c.kind == "message" and c.index == 3)
    assert ev.protected and "evidence" in ev.why, "the read is booked, not merely lucky"
    monkeypatch.setattr(compiler, "PROTECTED_MAX", 0.0)
    _, off = _compile(body, repo_graph)
    assert not on["over_budget"] and not off["over_budget"]
    assert "tool_result#3" in _kept_msgs(on)
    assert "tool_result#3" not in _kept_msgs(off), "precondition: greedy alone starves it"
    assert on["tokens_after"] <= budget and off["tokens_after"] <= budget
    assert on["tokens_after"] <= off["tokens_after"], "keeping the evidence must not cost more"
    assert len(on["dropped"]) >= len(off["dropped"])


def test_a_read_too_big_to_book_does_not_freeze_the_plan(repo_graph, monkeypatch):
    """The cap is what keeps I3 honest instead of self-defeating.

    Booking an evidence read that cannot fit the budget would report `over_budget` and
    cut nothing, so the compiler would be a no-op on exactly the long sessions that
    need it. Unbounded booking is the mutant here.
    """
    body = _session(10, 40)
    budget, proof = _compile(body, repo_graph)
    c3 = next(c for c in compiler.build_candidates(body, analyze_request(body), repo_graph,
                                                   TASK, budget)[0]
              if c.kind == "message" and c.index == 3)
    assert c3.tokens > int(budget * compiler.PROTECTED_MAX), "precondition: too big to book"
    assert "evidence" not in c3.why and not c3.protected
    assert not proof["over_budget"] and proof["dropped"], "the rest still compiles"
    assert "tool_result#3" not in _kept_msgs(proof)
    monkeypatch.setattr(compiler, "PROTECTED_MAX", 100.0)
    _, unbounded = _compile(body, repo_graph)
    assert unbounded["over_budget"] and not unbounded["dropped"], "mutant: a no-op compiler"


def test_the_index_raises_the_value_of_the_turn_that_names_it(symptom_graph):
    """Isolate graph coupling from the `user typed the path` rule: the query below
    names no file at all, so only the index can tell the compiler what this read is."""
    body = _starved_session(SYMPTOM, QUANGLE)
    analysis = analyze_request(body)
    with_g = compiler.build_candidates(body, analysis, symptom_graph, SYMPTOM, 12000)[0]
    without = compiler.build_candidates(body, analysis, None, SYMPTOM, 12000)[0]
    a = next(c for c in with_g if c.kind == "message" and c.index == 3)
    b = next(c for c in without if c.kind == "message" and c.index == 3)
    assert "graph" in a.why and "graph" not in b.why
    assert a.value > b.value, "naming the ranked file must raise the turn's value"
    assert a.protected and not b.protected


def test_the_compiler_harvests_tools_in_one_pass(repo_graph, monkeypatch):
    """CORE_FLOOR is a property of the *list*, so a per-tool call would be wrong twice
    over: O(n) heuristic passes and a `floor` tag on every entry. One call per turn."""
    calls = []
    real = heuristics.tool_gate

    def spy(tools, query):
        calls.append(len(tools))
        return real(tools, query)

    monkeypatch.setattr(heuristics, "tool_gate", spy)
    body = _body()
    _compile(body, repo_graph)
    assert calls == [len(protocol.flatten_tools(body["tools"]))], "exactly one harvest"


def test_a_tool_the_task_names_is_not_traded_away_for_a_bigger_dump(repo_graph):
    """The failure v5 exists to fix runs both ways: the joint plan must not drop the
    tool the kept file needs just because a message had a better ratio."""
    named = {"name": "mcp__db__query",
             "description": "query the payments retry ledger for swallowed "
                            "APIConnectionError rows",
             "input_schema": {"type": "object", "properties": {
                 "sql": {"type": "string"}, "limit": {"type": "string"}}}}
    body = _reads(TASK, [(RETRY, 400)] + [("app/legacy/p%d.py" % i, 40) for i in range(20)])
    body["tools"] = [named] + list(TOOLS)
    _, proof = _compile(body, repo_graph)
    dropped = set(d["id"] for d in proof["dropped"] if d["kind"] == "tool")
    assert "mcp__db__query" not in dropped, "the named tool must survive the budget"
    assert dropped, "precondition: the budget really bit on the unrelated specs"
    assert not proof["over_budget"]


def test_the_tail_spend_counts_against_the_booking_cap(repo_graph):
    """A cap measured from zero would book evidence the tail has already crowded out."""
    budget = 5000
    body = _session(1, 400, retry_lines=150)
    analysis = analyze_request(body)
    cands, _ = compiler.build_candidates(body, analysis, repo_graph, TASK, budget)
    ev = next(c for c in cands if c.kind == "message" and c.index == 3)
    booked = sum(c.tokens for c in cands if c.protected and c is not ev)
    assert ev.tokens <= budget * compiler.PROTECTED_MAX, "it fits the cap on its own"
    assert booked + ev.tokens > budget, "...but the tail has spent the room"
    assert not ev.protected
    _, proof = _compile(body, repo_graph, budget)
    assert not proof["over_budget"] and proof["dropped"], "the turn still compiles"


def _starved_session(task, first):
    """One big read at the bottom of a long session of cheap ones — the shape a pure
    value-per-token greedy loses, so any keep must be explained by evidence booking."""
    return _reads(task, [(first, 400)] + [("app/other/p%d.py" % i, 40) for i in range(40)],
                  tail=_SCROLL)


def test_the_index_is_what_saves_a_read_the_task_never_names(symptom_graph):
    """Mutation check on coupling: same turn, same budget, index in vs index out."""
    body = _starved_session(SYMPTOM, QUANGLE)
    _, with_g = _compile(body, symptom_graph, query=SYMPTOM)
    _, without = _compile(body, None, query=SYMPTOM)
    assert "tool_result#3" in _kept_msgs(with_g), "the ranked file's read is booked"
    assert "tool_result#3" not in _kept_msgs(without), "without it the greedy starves it"
    assert without["files_surfaced"] == []


def test_a_path_the_user_types_is_evidence_even_without_an_index():
    """`The human named this path` has to carry protection on its own: the index can be
    absent (`SUBPROTO_GRAPH` unset), and it still does not cover `.json`/`.csv`-style
    data files. Same-shape evidence, two independent routes to it."""
    body = _starved_session(TASK, RETRY)
    analysis = analyze_request(body)
    c = next(x for x in compiler.build_candidates(body, analysis, None, TASK)[0]
             if x.kind == "message" and x.index == 3)
    assert "names-task-file" in c.why and c.protected
    _, proof = _compile(body, None)
    assert "tool_result#3" in _kept_msgs(proof)


def test_the_index_now_reaches_a_migration_the_task_never_names(schema_graph):
    """S27: `.sql` used to be outside `LANG_BY_EXT`, so a needed migration read had no
    graph signal and R2 was its only rescue. Same turn, index in vs index out."""
    assert schema_graph["nodes"][LEDGER]["lang"] == "sql"
    body = _starved_session(LEDGER_TASK, LEDGER)
    analysis = analyze_request(body)
    c = next(x for x in compiler.build_candidates(body, analysis, schema_graph,
                                                  LEDGER_TASK)[0]
             if x.kind == "message" and x.index == 3)
    assert "graph" in c.why and c.protected, "a DDL object name is the file's symbol now"
    _, with_g = _compile(body, schema_graph, query=LEDGER_TASK)
    _, without = _compile(body, None, query=LEDGER_TASK)
    assert "tool_result#3" in _kept_msgs(with_g)
    assert "tool_result#3" not in _kept_msgs(without), \
        "no index, no signal: the greedy trades the migration away"


@pytest.fixture(scope="module")
def data_graph(tmp_path_factory):
    """S31: an index whose only signal for the read the task needs is a *data file's*
    keys — the case FR-10 documented as unreachable without a human typing the path."""
    root = tmp_path_factory.mktemp("data-repo")
    (root / "fixtures").mkdir()
    (root / "app" / "other").mkdir(parents=True)
    (root / "fixtures" / "refunds.json").write_text(
        '{"refunds": [{"amount_cents": 1200, "gateway_error": "APIConnectionError"}], '
        '"meta": {"generated_by": "seed"}}\n')
    (root / "app" / "other" / "decoy.py").write_text("def unrelated():\n    return 1\n")
    return graph_mod.build(str(root))


def test_the_index_now_reaches_a_fixture_the_task_never_names(data_graph):
    FIXTURE = "fixtures/refunds.json"
    task = ("the refund fixture's amount_cents disagrees with the gateway_error the "
            "ledger row carries, which writer is wrong?")
    assert data_graph["nodes"][FIXTURE]["lang"] == "json"
    assert "amount" in data_graph["nodes"][FIXTURE]["symbols"]
    assert not protocol.extract_paths(task), "the task must name no path, or R2 does the work"
    body = _starved_session(task, FIXTURE)
    c = next(x for x in compiler.build_candidates(body, analyze_request(body),
                                                  data_graph, task)[0]
             if x.kind == "message" and x.index == 3)
    assert "graph" in c.why and c.protected, "a JSON key is the file's symbol now"
    _, with_g = _compile(body, data_graph, query=task)
    _, without = _compile(body, None, query=task)
    assert "tool_result#3" in _kept_msgs(with_g)
    assert "tool_result#3" not in _kept_msgs(without), \
        "no index, no signal: the greedy trades the fixture away"


def test_a_file_the_tail_already_quotes_is_not_paid_for_twice(repo_graph):
    """The note competes with the prose that repeats it — that is the double toll."""
    body = _body()
    plain = {c.id: c for c in compiler.build_candidates(body, analyze_request(body),
                                                        repo_graph, TASK)[0]
             if c.kind == "file"}
    body["messages"].append({"role": "user",
                             "content": "right, and the %s dump above is what I mean" % RETRY})
    cands, _ = compiler.build_candidates(body, analyze_request(body), repo_graph, TASK)
    dupes = [c for c in cands if c.kind == "file" and "already-in-tail" in c.why]
    assert dupes, "retry.py is quoted by the tail, so its note line must be devalued"
    # Compared against the *same* file in the same session, not against other files:
    # a low-ranked unrelated file is cheap for a different reason.
    for c in dupes:
        assert c.value < plain[c.id].value
        assert c.value == pytest.approx(plain[c.id].value * 0.25)
        assert c.protected is False, "a file the tail already quotes is not booked first"


# --- the coupling's cost (S32) -------------------------------------------------

_COUPLING_NEEDLES = ("retry.py", "errors.py")
_COUPLING_TEXTS = [
    "fixed app/payments/retry.py and app/retry.py both",
    "retry.py:12 raises",
    "myretry.py is a different file entirely",
    "try.py is not the file I meant",
    "the note in .../retry.py, done",
    "RETRY.PY mentioned in upper case",
    "xetry.py",
    "app/payments/retry.py.bak is the backup copy",
    "two: retry.py and errors.py here",
    "http://host/app/retry.py?x=1",
    "no paths at all in this one",
    "retry.pyretry.py",
    "src/retry.py and src/errors.py plus a stray retry.txt",
    "a long tail of prose with no file anywhere near it at all " * 4,
]


@pytest.mark.parametrize("text", _COUPLING_TEXTS)
def test_the_windowed_coupling_scan_loses_no_path_a_full_scan_found(text):
    """S32: the compiler stopped regexing whole messages, so prove it gave nothing up.

    Two directions, and together they are exactly the claim that the comparisons
    downstream cannot tell the scans apart: every path the full scan found *and* that
    carries a needle is still found, and everything the windowed scan returns is a path
    the full scan saw too. A coupling can only be formed by a path that carries its
    needle's basename, so the first test alone would be enough — the second is here
    because `paths_named` widens the candidate set on purpose, and a made-up path would
    silently invent a graph boost.
    """
    low = text.lower()
    got = set(protocol.paths_named(low, _COUPLING_NEEDLES))
    full = set(protocol.extract_paths(low))
    want = set(p for p in full if any(n in p for n in _COUPLING_NEEDLES))
    assert want <= got, "lost a coupling: %s" % sorted(want - got)
    assert got <= full, "invented a path: %s" % sorted(got - full)


def test_a_path_that_only_ends_like_the_file_does_not_couple(symptom_graph):
    """`ent_client.py` is a character-suffix of `app/pay/quangle_client.py`; it is not a
    path suffix, and the coupling that saves the read must not fire on the difference."""
    body = {"model": "m", "max_tokens": 64, "stream": False, "messages": [
        {"role": "user", "content": SYMPTOM},
        {"role": "user", "content": "the trace points at ent_client.py line 3"},
        {"role": "user", "content": "the trace points at quangle_client.py line 3"},
        {"role": "assistant", "content": "ok"}] * 3}
    cands, _ = compiler.build_candidates(body, analyze_request(body), symptom_graph,
                                         SYMPTOM)
    by_index = {c.index: c for c in cands if c.kind == "message"}
    assert QUANGLE in [r for r, _s, _w in
                       graph_mod.search(symptom_graph, SYMPTOM, top_k=3)]
    assert "graph" not in by_index[1].why, "a partial name is not the file"
    assert "graph" in by_index[2].why, "the real basename must still couple"


def test_the_coupling_hands_the_path_regex_a_run_not_a_whole_message(repo_graph):
    """S32's claim is a cost property, so it is measured as one, not timed.

    `PATH_RE` over a 10 KB tool result was the compiled arm's whole latency lead over
    the per-slot arm (0.08 ms a message, a dozen messages a decision). The fix confines
    the regex to the path-shaped run around a hit, which is testable without a
    stopwatch: count the characters ever handed to `PATH_RE` for one decision and
    require it to be a fraction of the prompt it read.
    """
    body = _body()
    total = sum(len(m[1]) for m in protocol.normalize_messages(body))
    # `analyze_request` legitimately reads the whole body once for both arms; it is not
    # the compiler's cost, so its counting happens before the patch.
    analysis = analyze_request(body)
    seen = {"chars": 0}
    real = protocol.PATH_RE

    class _Counting(object):
        def findall(self, s):
            seen["chars"] += len(s)
            return real.findall(s)

        def __getattr__(self, name):
            return getattr(real, name)

    protocol.PATH_RE = _Counting()
    try:
        compiler.build_candidates(body, analysis, repo_graph, TASK)
    finally:
        protocol.PATH_RE = real
    assert seen["chars"] > 0, "the regex never ran — the count proves nothing"
    assert seen["chars"] < total / 4.0, \
        "read %d of %d prompt chars with PATH_RE: the whole-message scan is back" % (
            seen["chars"], total)


# --- the retention proof (S21) -------------------------------------------------

def test_proof_names_what_beat_each_drop_and_how_to_restore_it(compiled):
    body, analysis, budget, new_body, decisions, proof = compiled
    assert proof["budget"] == budget and proof["tokens_before"] > proof["tokens_after"]
    assert proof["dropped"]
    for d in proof["dropped"]:
        assert "value/token" in d["reason"] or "does not fit" in d["reason"]
        assert d["reversible"]["body_sha"] == "sha-under-test"
        assert d["reversible"]["pointer"] == "%s:%s" % (d["kind"], d["id"])
        assert d["reversible"]["index"] is not None if d["kind"] == "message" else True
    nd = proof["never_dropped"]
    assert len(nd["tail_indices"]) == compiler.PROTECTED_TAIL
    assert nd["core_tools"] and all(t in ("read", "write", "edit", "bash")
                                   for t in nd["core_tools"])
    assert "I2" in nd["cached_prefix"]


def test_a_drop_reason_never_prints_a_false_comparison(compiled):
    """The proof's whole point is that a reviewer can check the arithmetic it states.

    At four decimals a real strict loss came out as "0.0030 < 0.0030" — a sentence that
    contradicts itself, printed next to the claim it is justifying.
    """
    _, _, _, _, _, proof = compiled
    lost = re.compile(r"value/token ([0-9.]+) < kept floor ([0-9.]+)")
    beat = re.compile(r"value/token ([0-9.]+) beat the kept floor ([0-9.]+)")
    checked = 0
    for d in proof["dropped"]:
        for m, strict_less in ((lost, True), (beat, False)):
            g = m.search(d["reason"])
            if not g:
                continue
            left, right = float(g.group(1)), float(g.group(2))
            if strict_less:
                assert left < right, d["reason"]
            else:
                assert left >= right, d["reason"]
            checked += 1
    assert checked, "this fixture must produce at least one floor comparison"


def test_a_two_thousandths_loss_prints_as_two_thousandths():
    """Mutation gate for the reason formatter: 0.003019 vs 0.003030 is a strict loss
    that four decimals renders as a tie."""
    a = compiler.Candidate("message", "a", 400, 1.2)
    a._floor, a._last_kept, a._left = a.ratio * 1.0036, "b", 0
    reason = compiler._drop_reason(a)
    nums = re.findall(r"value/token ([0-9.]+) [<>] kept floor ([0-9.]+)", reason)
    assert nums, reason
    assert float(nums[0][0]) < float(nums[0][1]), reason


def test_compiled_context_note_lands_after_the_prefix(compiled):
    """I2 on the compiled path: the note never rewrites the cached prefix fields."""
    body, analysis, budget, new_body, decisions, proof = compiled
    ctx = [d for d in decisions if d["slot"] == "context"][0]
    assert ctx["applied"] and new_body is not None
    assert new_body["system"] == body["system"]
    assert compiler.FILE_NOTE_HEADER not in new_body["system"]
    text = protocol.content_to_text(new_body["messages"][-1]["content"])
    assert compiler.FILE_NOTE_HEADER in text
    for f in proof["files_surfaced"]:
        assert text.rstrip().endswith(f) or ("\n" + f) in text[-200:]


def test_absurd_budget_reports_over_budget_and_writes_nothing(repo_graph):
    body = _body()
    new_body, decisions, proof = compiler.compile_turn(
        "anthropic", body, analyze_request(body), repo_graph, TASK, budget=1,
        enforce=ENFORCE)
    assert new_body is None and decisions == []
    assert proof["over_budget"] and "protected set" in proof["reason"]
    assert proof["applied"] is False


# --- engine wiring (S22) -------------------------------------------------------

def test_engine_takes_the_compiled_path_when_enabled(cfg, repo_graph, monkeypatch):
    monkeypatch.setenv("SUBPROTO_COMPILE", "on")
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate,compact,context")
    gpath = os.path.join(cfg.data_dir, "g.json")
    graph_mod.save(repo_graph, gpath)
    monkeypatch.setenv("SUBPROTO_GRAPH", gpath)
    eng = Engine(cfg)
    assert eng.graph is not None
    body = _body()
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg,
                                     body_sha="abc123")
    comp = [d for d in decisions if d.get("compiled")]
    assert {d["slot"] for d in comp} == {"tool_gate", "compact", "context"}
    assert comp[0]["proof"]["dropped"][0]["reversible"]["body_sha"] == "abc123"
    assert new_body is not None
    effort = [d for d in decisions if d["slot"] == "effort"]
    assert effort and effort[0]["applied"] is False, "effort stays advisory (FR-3d)"


def test_engine_degrades_to_per_slot_on_error(cfg, monkeypatch):
    monkeypatch.setenv("SUBPROTO_COMPILE", "on")
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate,compact,context")

    def boom(*a, **k):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(compiler, "compile_turn", boom)
    eng = Engine(cfg)
    body = _body()
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert eng.compile_error and "planner exploded" in eng.compile_error
    assert not [d for d in decisions if d.get("compiled")], "fell back to per-slot"
    assert [d for d in decisions if d["slot"] == "tool_gate"]
    assert eng.model_status()["compiled"] is True


def test_observation_mode_compiles_but_writes_nothing(cfg, monkeypatch):
    """I1: with nothing in SUBPROTO_APPLY the plan is recorded and the bytes are not."""
    monkeypatch.setenv("SUBPROTO_COMPILE", "on")
    monkeypatch.delenv("SUBPROTO_APPLY", raising=False)
    monkeypatch.delenv("SUBPROTO_ENFORCE", raising=False)
    eng = Engine(cfg)
    body = _body()
    new_body, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert new_body is None
    assert [d for d in decisions if d.get("compiled")]
    assert all(d["applied"] is False for d in decisions)


def test_compiled_decisions_stay_dataset_compatible(cfg, repo_graph, monkeypatch):
    """The label loop indexes slot/candidates/target/keep — the plan must keep that shape."""
    monkeypatch.setenv("SUBPROTO_COMPILE", "on")
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate,compact,context")
    eng = Engine(cfg)
    body = _body()
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg, body_sha="ds1")
    for d in decisions:
        assert d["slot"] in ALL_SLOTS
        assert d["savings_est_tok"] >= 0
        for c in d.get("candidates") or []:  # effort carries none, on either path
            assert "target" in c and "keep" in c and "score" in c
    tel = Telemetry(cfg.db_path)
    try:
        tel.record({"ts": 1.0, "api": "anthropic", "path": "/v1/messages", "client": "pytest",
                    "model": "m", "status": 200, "body_sha": "ds1",
                    "features": analyze_request(body), "usage": {"input": 100, "output": 5},
                    "decisions": decisions})
        split = dataset.build_training_split(cfg, tel, write=False)
        assert split["n_examples"] > 0
        assert {e["slot"] for e in split["train"] + split["val"]} <= set(ALL_SLOTS)
        exported = dataset.export(cfg, tel, include_candidates=False)
        assert exported["rows"] > 0
        assert set(exported["slots"]) <= set(ALL_SLOTS)
        with open(exported["path"]) as f:
            first = json.loads(f.readline())
        assert first["schema"] == "subproto/1"
        assert "proof" not in first["outcome"], "the turn proof must not bloat every row"
    finally:
        tel.close()


# --- CLI witness (S24) ---------------------------------------------------------

def test_cli_compile_witness_prints_a_proof(capsys):
    rc = cli.main(["compile", "fix", "the", "retry", "loop", "in", RETRY,
                   "--graph", REPO, "--budget", "6000"])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "compiled turn" in printed and "what was cut" in printed
    assert "reversible pointer" in printed


def test_cli_compile_reports_over_budget_instead_of_lying(capsys):
    rc = cli.main(["compile", "fix retry", "--graph", REPO, "--budget", "5"])
    printed = capsys.readouterr().out
    assert rc == 1 and "over budget" in printed and "nothing was cut" in printed


def test_cli_compile_json_carries_the_whole_proof(capsys):
    rc = cli.main(["compile", "fix", RETRY, "--graph", REPO, "--budget", "6000", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert {"proof", "decisions", "compiled_body"} <= set(doc)
    assert doc["proof"]["tokens_after"] <= doc["proof"]["budget"]


def test_the_cli_witness_drops_are_actually_addressable(capsys):
    """The witness is the public demo of reversibility, so its pointers must resolve:
    a `body_sha: null` would be an address to nowhere."""
    rc = cli.main(["compile", "fix", RETRY, "--graph", REPO, "--budget", "6000", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["proof"]["dropped"]
    for d in doc["proof"]["dropped"]:
        assert len(d["reversible"]["body_sha"] or "") == 12, d
        assert d["reversible"]["pointer"] == "%s:%s" % (d["kind"], d["id"])
