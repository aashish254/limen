"""T38 — `subproto show <request_id>`: the page the other surfaces point at.

`compile`, `report` and the README all tell a reader to run this command, so these
tests are the difference between a pointer and a dead end. Two things are checked
hard: that a drop's pointer resolves to exactly the item the decision cut (and to
nothing when it cannot be trusted), and that a request recorded without
`--store-bodies` says so rather than inventing a quote.

The traffic is `test_implicit`'s: real `compile_turn` proofs and bodies spooled the
way the proxy spools them, because a hand-written proof would not test the plumbing
this page reads.
"""
import json
import os
import shutil

import pytest

from subproto import cli, dataset, protocol, style
from subproto.config import Config

from test_implicit import RETRY, _compile, _home, _read_pair, _record, _telemetry, \
    _turn_a


@pytest.fixture(autouse=True)
def _colour_choice_does_not_leak():
    """`cli.main` answers `--color`/`--no-color` once and stores it for the process —
    which is the point of the flag, and a leak the moment a test hands it one. Every
    page assertion here runs through main, so the previous answer goes back after it."""
    before = getattr(style, "_FORCE", None)
    yield
    style.override(before)

TOOLS = [
    {"name": "Read", "description": "Read a file from disk."},
    {"name": "KillShell", "description": "Terminate a running background shell session."},
    {"name": "TodoWrite",
     "description": "Create or update the structured task list for this session."},
]


def _session(tmp_path, budget=900):
    """One recorded turn that really cut a read and a tool spec.

    Returns (home, request id, body sha, db path). A budget of 900 against ~4 KB of
    reads is what makes the compiler cut: the proofs are its own, not written here.
    """
    config = _home(tmp_path)
    body = _turn_a()
    body["tools"] = TOOLS
    sha, decisions, _proof = _compile(body, budget=budget)
    telemetry = _telemetry(config)
    _record(config, telemetry, body, decisions, ts=1700000000)
    row = telemetry.query("SELECT id FROM requests ORDER BY id")[0]
    home, db = config.data_dir, config.db_path
    telemetry.close()
    return home, row["id"], sha, db


def test_show_prints_the_request_and_every_cut_it_made(tmp_path, capsys):
    home, rid, _sha, db = _session(tmp_path)
    rc = cli.main(["show", str(rid), "--home", home, "--no-color"])
    assert rc == 0
    text = capsys.readouterr().out
    assert text.startswith("subproto show — request %d, anthropic /v1/messages" % rid)
    for label in ("client", "recorded", "stream", "status", "ttfb", "latency",
                  "billed input", "output", "spend", "body", "decisions"):
        assert label in text, label
    assert "tool_gate" in text
    assert "Contents of /repo/%s" % RETRY in text, "the cut read must be quoted"
    # Both figures, because a lone count over a list of cuts does not add up.
    assert "kept" in text and "cut" in text


def test_a_message_pointer_resolves_to_the_item_the_decision_cut(tmp_path, capsys):
    """The proof's index addresses the flattened list, not `body["messages"]`.

    The two index spaces differ by one entry per content block, so resolving against
    the raw list prints a neighbouring turn as the item that was dropped — and the
    neighbouring read here names a *different file*, which is what makes this a
    witness rather than a smoke test.
    """
    home, rid, sha, _db = _session(tmp_path)
    cli.main(["show", str(rid), "--home", home, "--no-color"])
    text = capsys.readouterr().out

    stored = _stored(home, sha)
    drop = [d for d in _recorded_drops(home, rid) if d["kind"] == "message"][0]
    index = drop["reversible"]["index"]
    # The raw list at that number is a different turn: prove the two spaces diverge
    # before asserting which one the page used.
    assert stored["messages"][index]["content"][0]["type"] != "tool_result"
    flat = protocol.normalize_messages(stored)
    assert flat[index][0] == "user" and "retry.py" in flat[index][1]
    quote = style.clip(" ".join(str(flat[index][1]).split()), 66)
    assert quote in text
    for other in ("app/legacy/notes.md", "app/legacy/flags.md"):
        assert "Contents of /repo/%s" % other not in text


def test_a_tool_pointer_names_the_spec_that_was_cut(tmp_path, capsys):
    home, rid, _sha, _db = _session(tmp_path)
    cli.main(["show", str(rid), "--home", home, "--no-color"])
    text = capsys.readouterr().out
    assert "Create or update the structured task list for this session." in text
    assert "Read a file from disk." not in text, "a kept spec must not be quoted as cut"


def test_a_pointer_that_cannot_be_trusted_resolves_to_nothing(tmp_path):
    """Printing an adjacent turn as the dropped one is worse than printing no quote."""
    home, rid, sha, _db = _session(tmp_path)
    stored = protocol.load_body(
        dataset.read_body(os.path.join(home, "bodies", "anthropic_%s.json.gz" % sha)),
        "gzip")
    assert cli._excerpt("message", "message:tool_result#3", 3, stored)
    # Right index, wrong kind: the flattened entry is a tool_result, not a user turn.
    assert cli._excerpt("message", "message:user#3", 3, stored) == ""
    assert cli._excerpt("message", "message:tool_result#40", 40, stored) == ""
    assert cli._excerpt("message", "message:tool_result#3", None, stored) == ""
    assert cli._excerpt("tool", "tool:nosuchtool", None, stored) == ""
    assert cli._excerpt("message", "message:tool_result#3", 3, None) == ""


def test_a_request_recorded_without_a_body_says_so_instead_of_inventing_one(
        tmp_path, capsys):
    home, rid, _sha, _db = _session(tmp_path)
    shutil.rmtree(os.path.join(home, "bodies"))
    rc = cli.main(["show", str(rid), "--home", home, "--no-color"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "missing" in text
    # The clause, not just the word: the body row also says "never stored", and a page
    # that drops this sentence stops short of telling the reader what is missing.
    # Matched across the wrap, because this is prose and prose reflows.
    assert "cannot show the text they name" in " ".join(text.split())
    assert "--store-bodies" in text
    # The counts and pointers are still measured, so they stay; no quote is invented.
    assert "tool_result#3" in text
    assert "Contents of /repo" not in text


def test_a_missing_request_id_is_a_state_not_a_traceback(tmp_path, capsys):
    home, _rid, _sha, db = _session(tmp_path)
    rc = cli.main(["show", "4242", "--home", home, "--no-color"])
    assert rc == 1
    text = capsys.readouterr().out
    assert "no request 4242" in text
    assert db in text, "the reader has to see which home was empty"
    assert "Traceback" not in text


def test_json_mode_carries_the_text_the_page_clips(tmp_path, capsys):
    home, rid, _sha, _db = _session(tmp_path)
    rc = cli.main(["show", str(rid), "--home", home, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == rid
    kinds = [d["kind"] for d in payload["drops"]]
    assert "message" in kinds and "tool" in kinds
    msg = [d for d in payload["drops"] if d["kind"] == "message"][0]
    assert msg["excerpt"].startswith("Contents of /repo/%s" % RETRY)
    assert msg["tokens"] > 0 and msg["why"]
    cli.main(["show", "4242", "--home", home, "--json"])
    assert json.loads(capsys.readouterr().out)["drops"] == []


def _is_body_path(line):
    """Does this line carry the stored body's path? Spelled with this host's separator,
    because the page prints whatever ``os.path.join`` produced."""
    return "bodies/" in line or "bodies\\" in line


def test_every_page_line_survives_a_paste(tmp_path, capsys):
    """No frame, no wrap. A line carrying a path may run long: the reader copies it."""
    home, rid, _sha, _db = _session(tmp_path)
    cli.main(["show", str(rid), "--home", home, "--no-color"])
    for line in capsys.readouterr().out.splitlines():
        assert "│" not in line and "───" not in line
        if _is_body_path(line):
            continue
        assert len(style.strip(line)) <= 100, "too wide: %r" % line


def test_a_body_under_the_home_prints_as_a_tilde(tmp_path, monkeypatch, capsys):
    """The one line allowed to run long is the one that no longer has to.

    `show` prints the stored body's path so the reader can open it, and under a real home
    that path broke the page's 100-column measure — in a screenshot, mid-token. Inside the
    home it prints as `~/…`, which is shorter and still pastes into a shell; outside it,
    and in `--json`, the absolute path stands.
    """
    home, rid, sha, _db = _session(tmp_path)
    monkeypatch.setattr(cli.os.path, "expanduser", lambda p: str(home))
    cli.main(["show", str(rid), "--home", home, "--no-color"])
    line = [l for l in capsys.readouterr().out.splitlines() if _is_body_path(l)][0]
    assert "~%sbodies%santhropic_%s.json.gz" % (os.sep, os.sep, sha) in line
    assert len(style.strip(line)) <= 100, "too wide: %r" % line
    # A store on another volume is not under the home, so nothing is elided.
    elsewhere = os.path.join(os.path.abspath(os.sep), "scratch", "bodies", "a.json.gz")
    assert cli._shown_path(elsewhere, home=str(home)) == elsewhere
    # And a sibling directory whose name only starts with the home is not under it either.
    sibling = os.path.join(str(home) + "x", "bodies", "a.json.gz")
    assert cli._shown_path(sibling, home=str(home)) == sibling


def test_a_windows_home_elides_the_same_way(tmp_path, monkeypatch):
    """The elision is the product's answer for a long path, so it has to work with
    backslashes — a test that only ever handed it `/` would let the Windows page print
    the absolute path it was supposed to shorten."""
    monkeypatch.setattr(cli.os, "sep", "\\")
    home = "C:\\Users\\alice"
    assert cli._shown_path(home + "\\bodies\\anthropic_a.json.gz",
                           home=home) == "~\\bodies\\anthropic_a.json.gz"
    # A sibling whose name only starts with the home is not under it.
    assert cli._shown_path(home + "x\\bodies\\a.json.gz",
                           home=home).startswith(home)
    # And the drive root is never shortened to a bare tilde.
    assert cli._shown_path("D:\\scratch\\bodies\\a.json.gz",
                           home=home) == "D:\\scratch\\bodies\\a.json.gz"


def test_show_paints_colour_only_on_state_words(tmp_path, capsys):
    """The grid's rule, checked on this page: dim and bold are structure, colour is a
    verdict — so a chromatic escape may only appear where a state word says it."""
    home, rid, _sha, _db = _session(tmp_path)
    cli.main(["show", str(rid), "--home", home, "--color"])
    lines = capsys.readouterr().out.splitlines()
    chromatic = [l for l in lines if any(c in l for c in (style.GREEN, style.AMBER,
                                                          style.RED))]
    assert chromatic, "a page with three decisions should be reporting a state"
    states = (style.POTENTIAL, style.DELIVERED, style.PRESENT, style.MISSING,
              style.YES, style.OFF, style.BLOCK)
    for line in chromatic:
        assert any(s in line for s in states), line
    # And the plain page is the same page: stripping escapes must be lossless.
    stripped = "\n".join(style.strip(l) for l in lines)
    cli.main(["show", str(rid), "--home", home, "--no-color"])
    assert stripped == capsys.readouterr().out.rstrip("\n"), \
        "the escapes must be the only difference between the two pages"


def test_a_cut_the_slot_named_twice_is_printed_once():
    """S46/B2 — the per-slot record lists a cut twice, and the page used to believe it.

    `dropped` carries names and `candidates` carries the same tools with `keep: false`,
    so a 12-tool request rendered 24 rows under `12 kept 24 cut`. The rows are also
    merged rather than first-wins: the name arrives with no token count, and the price
    and reason only live on the candidate.
    """
    decision = {
        "slot": "tool_gate", "applied": True, "kept": 1,
        "dropped": ["KillShell", "TodoWrite"],
        "candidates": [
            {"target": "Read", "keep": True, "tokens": 41, "why": ["core"]},
            {"target": "KillShell", "keep": False, "tokens": 55, "why": ["off-topic"]},
            {"target": "TodoWrite", "keep": False, "tokens": 60, "why": ["off-topic"]},
        ],
    }
    drops = cli._decision_drops(decision)
    assert sorted(d[1] for d in drops) == ["tool:KillShell", "tool:TodoWrite"]
    assert cli._head_counts(decision, drops) == (1, 2)
    todo = [d for d in drops if d[1] == "tool:TodoWrite"][0]
    assert todo[3] == 60 and todo[4] == "off-topic", \
        "the deduped row kept the poorer of the two records"


def test_a_compiled_slot_prints_only_the_cuts_it_paid_for():
    """S46/B6 — one record carries the whole joint proof, and the page listed all of it.

    The compiler solves the request once and attaches that single result to the first
    slot's record, so the slot that holds it also holds the cuts the other slots paid
    for. On the demo corpus `tool_gate` printed 15 rows summing to 5,345 tok beside its
    own `4,145 tok` label while `compact` listed 3 of those same messages a second time:
    a reader who added the page got 18 items for a request that lost 15, and every block
    disagreed with the number at the top of it.

    The record is that shape — a joint proof spanning two kinds beside candidates that
    name one of them — because no proof bought here buys an overlap: the fixture's
    carrier slot owns every cut it lists.
    """
    message = {"kind": "message", "id": "tool_result#5", "tokens": 381, "reason": "stale",
               "reversible": {"index": 5, "pointer": "message:tool_result#5"}}
    specs = [{"kind": "tool", "id": name, "tokens": 300 + i, "reason": "off-topic",
              "reversible": {"index": None, "pointer": "tool:%s" % name}}
             for i, name in enumerate(("KillShell", "TodoWrite"))]
    decision = {
        "slot": "tool_gate", "applied": True, "compiled": True,
        "savings_est_tok": sum(s["tokens"] for s in specs),
        "proof": {"kept": [{"kind": "tool", "id": "Read", "tokens": 330},
                           {"kind": "message", "id": "user#0", "tokens": 40}] + specs,
                  "dropped": [message] + specs},
        "candidates": [{"target": "Read", "keep": True, "tokens": 330, "why": ["core"]},
                       {"target": "KillShell", "keep": False, "tokens": 300, "why": ["off"]},
                       {"target": "TodoWrite", "keep": False, "tokens": 301, "why": ["off"]}],
    }
    drops = cli._decision_drops(decision)
    assert [d[1] for d in drops] == ["tool:KillShell", "tool:TodoWrite"], \
        "the block lists a cut another slot paid for"
    assert sum(d[3] for d in drops) == decision["savings_est_tok"]
    # 3 kept, not 4: the joint pool's other kind belongs to the sibling that paid for it.
    assert cli._head_counts(decision, drops) == (3, 2), \
        "the kept/cut pair is still the joint pool, not this slot's"
    # The other slot's record of the same solve: it names the message, and it pays for it.
    sibling = {"slot": "compact", "applied": True, "compiled": True,
               "savings_est_tok": 381, "proof": {}, "dropped_count": 1,
               "candidates": [{"target": "tool_result#5", "keep": False, "tokens": 381,
                               "why": ["error"]}]}
    sib_drops = cli._decision_drops(sibling)
    assert [d[1] for d in sib_drops] == ["tool_result#5"]
    keys = {cli._drop_key(*d[:2]) for d in drops + sib_drops}
    assert len(keys) == 3, "one cut was claimed twice on one page"


def test_every_block_adds_up_to_its_own_number(tmp_path):
    """The invariant over a real proof: a block's rows sum to the figure beside its name.

    Asserted against the records rather than the page, because all three of these held
    on every demo request before S46/B6 was found — this is the guard that keeps the
    projection true, and the witness that a new arm's record shape still agrees with the
    page that reads it.
    """
    home, rid, _sha, _db = _session(tmp_path)
    keys = []
    for decision in _recorded_decisions(home, rid):
        shown = cli._decision_drops(decision)
        keys.extend(cli._drop_key(*row[:2]) for row in shown)
        if shown and decision.get("savings_est_tok"):
            assert sum(r[3] or 0 for r in shown) == decision["savings_est_tok"], \
                "%s's rows do not add up to the figure beside its name" % decision["slot"]
        head = cli._head_counts(decision, shown)
        if head and head[1] is not None:
            assert head[1] == len(shown), "%s says %d cut and prints %d rows" % (
                decision["slot"], head[1], len(shown))
    assert len(keys) == len(set(keys)), "one cut appeared under two slots"


def test_the_cache_line_names_the_read_and_not_the_write(tmp_path, capsys):
    """S46/B5 — `0 cached` printed over a request that read 2,464 tokens from cache.

    `cw_tok` is the cache *write*, and only Anthropic's dialect ever has one; the mock
    upstream (and OpenAI, Gemini, `responses`) leaves it at 0 while filling the read
    column. The page read the write column and labelled it "cached", so the one figure
    that shows the prefix cache working was always zero.
    """
    from subproto.telemetry import Telemetry

    config = _home(tmp_path)
    telemetry = _telemetry(config)
    telemetry.record({"ts": 1700000000, "api": "openai", "path": "/v1/chat/completions",
                      "client": "codex", "model": "gpt-5", "status": 200,
                      "usage": {"input_uncached": 11935, "cache_write": 0,
                                "cache_read": 2464, "output": 320},
                      "latency_ms": 5, "ttfb_ms": 0})
    rid = telemetry.query("SELECT id FROM requests")[0]["id"]
    home = config.data_dir
    telemetry.close()
    assert cli.main(["show", str(rid), "--home", home, "--no-color"]) == 0
    text = capsys.readouterr().out
    assert "2,464 cached" in text, text
    assert "0 cached" not in text, text
    # And the header agrees with `report`: billed input is the uncached part plus
    # the write, so the two pages cannot disagree about the same row.
    assert "11,935" in text


def _recorded_decisions(home, rid):
    """The decision records exactly as the page reads them back out of telemetry."""
    config = Config.load(data_dir=home)
    telemetry = _telemetry(config)
    row = telemetry.query("SELECT decisions FROM requests WHERE id = ?", (rid,))[0]
    telemetry.close()
    decisions = json.loads(row["decisions"] or "[]")
    assert decisions, "the fixture recorded no decisions"
    return decisions


def _recorded_drops(home, rid):
    """The recorded proof's dropped entries, as the page reads them."""
    config = Config.load(data_dir=home)
    telemetry = _telemetry(config)
    row = telemetry.query("SELECT decisions FROM requests WHERE id = ?", (rid,))[0]
    telemetry.close()
    out = []
    for decision in json.loads(row["decisions"] or "[]"):
        for d in (decision.get("proof") or {}).get("dropped") or []:
            out.append(d)
    assert out, "the fixture stopped cutting anything"
    return out


def _stored(home, sha):
    return protocol.load_body(
        dataset.read_body(os.path.join(home, "bodies", "anthropic_%s.json.gz" % sha)),
        "gzip")
