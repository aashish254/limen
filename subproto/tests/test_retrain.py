"""S34 — `subproto retrain`: the cadence policy, and the history it writes.

Three things these tests hold the code to, because each is a way a retrain goes
wrong quietly:

* **the split is measured as it would train** — rebuilt from telemetry, not read
  off a stale file, and validated by the trainer's own loader;
* **a run is refused for a stated reason** — counts, balance, freshness and the
  unreadable-history case all reach the page, and every refusal is recorded;
* **history is an append-only log** — corrupt input is never overwritten, and the
  version numbering never reuses a label.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

import pytest

from subproto import retrain
from subproto.config import Config


def _cfg(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    cfg.ensure_dirs()
    return cfg


def _rows(n_per_slot, slots=("tool_gate", "compact"), answers=("keep", "drop"),
          start=0):
    """`n_per_slot` rows for each (slot, answer) pair, each one distinct."""
    out, i = [], start
    for slot in slots:
        for answer in answers:
            for _ in range(n_per_slot):
                i += 1
                out.append({"state": "s%d" % i, "question": "keep", "slot": slot,
                            "options": ["c%d" % i], "answer": answer})
    return out


def _state(tmp_path, rows, history=(), **kw):
    kw.setdefault("min_per_slot", 8)
    kw.setdefault("min_answers", 2)
    return retrain.readiness(_cfg(tmp_path), train=rows, val=[], history=list(history),
                             trainer=None, trainer_path="train/finetune_mlx.py", **kw)


# ---------------------------------------------------------------- content: what a run would train on

def test_a_missing_history_file_is_empty_not_an_error(tmp_path):
    assert retrain.load_history(_cfg(tmp_path)) == []


def test_a_corrupt_history_is_unreadable_and_says_so(tmp_path):
    cfg = _cfg(tmp_path)
    with open(retrain.history_path(cfg), "w") as f:
        f.write("{not json")
    warn = []
    assert retrain.load_history(cfg, warn) is None
    assert "not readable JSON" in warn[0] and "left alone" in warn[0]


def test_a_refusal_to_write_leaves_the_corrupt_history_byte_identical(tmp_path):
    """The log is append-only, so the one thing it must never do is overwrite a
    file it cannot read — that would delete the history and report success."""
    cfg = _cfg(tmp_path)
    target = retrain.history_path(cfg)
    with open(target, "w") as f:
        f.write("{broken\n")
    before = open(target, "rb").read()
    with pytest.raises(ValueError):
        retrain.append_history(cfg, retrain.record(_state(tmp_path, _rows(8)), "planned"))
    assert open(target, "rb").read() == before
    assert not os.path.exists(target + ".tmp")


def test_appending_keeps_older_rows_and_order(tmp_path):
    cfg = _cfg(tmp_path)
    state = _state(tmp_path, _rows(8))
    retrain.append_history(cfg, retrain.record(state, "planned"))
    retrain.append_history(cfg, retrain.record(state, "refused"))
    rows = retrain.load_history(cfg)
    assert [r["status"] for r in rows] == ["planned", "refused"]
    assert not [f for f in os.listdir(cfg.data_dir) if f.endswith(".tmp")]


def test_the_fingerprint_moves_when_a_row_moves_or_changes(tmp_path):
    rows = _rows(2)
    other = [dict(r) for r in rows]
    other[0]["state"] = "different"
    assert retrain.fingerprint(rows, []) != retrain.fingerprint(other, [])
    assert retrain.fingerprint(rows, []) != retrain.fingerprint([], rows)
    assert retrain.fingerprint(rows, []) == retrain.fingerprint(list(rows), [])


def test_per_slot_counts_both_answers_and_the_total(tmp_path):
    slots = retrain.per_slot(_rows(3, slots=("tool_gate",)), val=[])
    assert slots == {"tool_gate": {"keep": 3, "drop": 3, "total": 6}}


# ---------------------------------------------------------------- the floors

def test_a_slot_under_the_floor_refuses_and_names_the_gap(tmp_path):
    rows = _rows(4, slots=("tool_gate",)) + _rows(1, slots=("compact",))
    state = _state(tmp_path, rows, min_per_slot=8)
    assert not state["ready"]
    assert state["short_total"] == {"compact": 6}
    assert any("under 8 examples per slot" in r and "compact short 6" in r
               for r in state["reasons"])


def test_meeting_the_floor_clears_the_count_reason(tmp_path):
    state = _state(tmp_path, _rows(4))          # 8 rows per slot, 4 of each answer
    assert state["ready"], state["reasons"]
    assert state["slots"]["tool_gate"]["total"] == 8


def test_a_slot_with_only_one_answer_is_not_enough(tmp_path):
    """40 keeps and 0 drops is not 40 examples, it is a constant. Both are counted
    because a model that always answers one way scores well on the majority class."""
    rows = _rows(4, slots=("tool_gate",)) + _rows(1, slots=("compact",),
                                                  answers=("keep",))
    state = _state(tmp_path, rows, min_per_slot=8, min_answers=2)
    assert "compact" in state["unbalanced"]
    assert state["short_answers"] == {"compact": {"keep": 1, "drop": 2}}
    assert any("teaches the answer, not the decision" in r for r in state["reasons"])


def test_a_slot_that_is_mostly_one_answer_is_still_not_a_lesson(tmp_path):
    """Distinct from a constant slot: 10 keeps and 1 drop is *both* answers, so the
    unbalanced rule cannot see it, and only the per-answer floor refuses it — which
    is the difference between a model that learned a ratio and one that learned a
    coin flip."""
    rows = _rows(5, slots=("tool_gate",)) \
        + _rows(10, slots=("compact",), answers=("keep",)) \
        + _rows(1, slots=("compact",), answers=("drop",))
    state = _state(tmp_path, rows, min_per_slot=8, min_answers=2)
    assert state["slots"]["compact"] == {"keep": 10, "drop": 1, "total": 11}
    assert state["unbalanced"] == [], state["unbalanced"]
    assert state["short_total"] == {}
    assert state["short_answers"] == {"compact": {"drop": 1}}
    assert not state["ready"]
    assert any("under 2 in both answers" in r and "compact drop short 1" in r
               for r in state["reasons"])


def test_no_examples_at_all_refuses_with_the_command_to_fix_it(tmp_path):
    state = _state(tmp_path, [])
    assert not state["ready"]
    assert any("subproto export" in r for r in state["reasons"])


def test_the_printed_floor_is_the_floor_the_code_used(tmp_path):
    """The page is the only place an operator reads the rule, so a mismatch
    between what is printed and what is enforced is a false gate."""
    rows = _rows(1, slots=("tool_gate",))
    state = _state(tmp_path, rows, min_per_slot=40, min_answers=8)
    text = retrain.render_text(state, "cmd")
    assert "floor 40 rows, 8 in both answers" in text
    assert "tool_gate short 38" in text


# ---------------------------------------------------------------- the cooldown

def test_an_unchanged_split_refuses_even_after_the_wait(tmp_path):
    """Retraining on a split that has not moved re-derives the same weights, and
    a week is no reason to spend a GPU doing it."""
    rows = _rows(4)
    state = _state(tmp_path, rows)
    run = {"at": "2026-01-01", "version": "lora-v1", "status": "ran", "exit": 0,
           "split_sha": state["split_sha"]}
    again = _state(tmp_path, rows, history=[run], today="2026-06-01")
    assert not again["ready"]
    assert any("has not changed" in r for r in again["reasons"])


def test_a_changed_split_after_the_wait_is_ready(tmp_path):
    rows = _rows(4)
    first = _state(tmp_path, rows)
    run = {"at": "2026-01-01", "version": "lora-v1", "status": "ran", "exit": 0,
           "split_sha": first["split_sha"]}
    moved = _state(tmp_path, rows + _rows(4, slots=("effort",)), history=[run],
                   today="2026-01-12")
    assert moved["ready"], moved["reasons"]
    assert moved["last_run"]["days_ago"] == 11


def test_a_changed_split_too_soon_is_still_a_no(tmp_path):
    rows = _rows(4)
    first = _state(tmp_path, rows)
    run = {"at": "2026-01-08", "version": "lora-v1", "status": "ran", "exit": 0,
           "split_sha": first["split_sha"]}
    soon = _state(tmp_path, rows + _rows(4, slots=("effort",)), history=[run],
                  today="2026-01-12", min_days=7)
    assert not soon["ready"]
    assert any("only 4 of 7 days" in r for r in soon["reasons"])


def test_a_refused_attempt_does_not_start_the_clock(tmp_path):
    """If refusals counted as runs, one early `--run` would lock the cadence for a
    week without a single weight being touched."""
    rows = _rows(4)
    first = _state(tmp_path, rows)
    refused = {"at": "2026-01-08", "version": "planned", "status": "refused",
               "exit": None, "split_sha": "0" * 64}
    state = _state(tmp_path, rows + _rows(4, slots=("effort",)), history=[refused],
                   today="2026-01-09")
    assert state["last_run"] is None
    assert any("no completed run on record" in n for n in state["notes"])
    assert state["ready"], state["reasons"]


def test_a_trainer_that_exited_nonzero_is_not_a_completed_run(tmp_path):
    """On this machine today the trainer stops at T16 with exit 4. Recording that
    as a run would put a week of cooldown on a week in which nothing trained."""
    rows = _rows(4)
    first = _state(tmp_path, rows)
    partial = {"at": "2026-01-08", "version": "lora-v1", "status": "ran", "exit": 4,
               "split_sha": first["split_sha"]}
    state = _state(tmp_path, rows + _rows(4, slots=("effort",)), history=[partial],
                   today="2026-01-09")
    assert state["last_run"] is None
    assert state["ready"], state["reasons"]


def test_no_run_on_record_means_the_wait_cannot_apply(tmp_path):
    state = _state(tmp_path, _rows(4), history=[])
    assert state["last_run"] is None
    assert any("cannot apply" in n for n in state["notes"])


def test_an_unreadable_history_refuses_rather_than_assuming_nothing_ran(tmp_path):
    """`history=None` from the loader means "there might be runs I cannot see".
    Treating that as "none" would skip the cooldown and start a run blind."""
    cfg = _cfg(tmp_path)
    with open(retrain.history_path(cfg), "w") as f:
        f.write("{oops")
    state = retrain.readiness(cfg, train=_rows(4), val=[], today="2026-09-26",
                              trainer=None, trainer_path="x", min_per_slot=8,
                              min_answers=2)
    assert not state["ready"]
    assert any("cadence cannot be evaluated" in r for r in state["reasons"])


# ---------------------------------------------------------------- versions and records

def test_version_numbering_never_reuses_a_label(tmp_path):
    history = [{"version": "lora-v1"}, {"version": "lora-v3"}, {"version": "planned"},
               {"version": None}, {"version": "lora-nonsense"}]
    assert retrain.version_for(history) == "lora-v4"
    assert retrain.version_for([]) == "lora-v1"


def test_a_record_carries_the_provenance_that_makes_it_a_comparable(tmp_path):
    rows = _rows(4)
    state = _state(tmp_path, rows)
    rec = retrain.record(state, "refused", command_line="python train.py",
                         error="mlx not installed")
    assert rec["split_sha"] == state["split_sha"]
    assert rec["n_train"] == 16 and rec["n_val"] == 0
    assert rec["per_slot"] == {"compact": 8, "tool_gate": 8}
    assert rec["thresholds"]["min_per_slot"] == 8
    assert rec["status"] == "refused" and rec["version"] == retrain.PLANNED
    assert rec["error"] == "mlx not installed"
    assert rec["command"] == "python train.py"
    # the reasons a run did not happen are part of the record, not just the console
    assert rec["reasons"] == state["reasons"] and rec["notes"] == state["notes"]


def test_a_record_for_a_run_carries_its_version_and_exit(tmp_path):
    state = _state(tmp_path, _rows(4))
    rec = retrain.record(state, "ran", version="lora-v2", exit_code=0)
    assert (rec["version"], rec["status"], rec["exit"]) == ("lora-v2", "ran", 0)


def test_the_recorded_command_is_the_absolute_one_the_trainer_accepts(tmp_path):
    cfg = _cfg(tmp_path)
    line = retrain.command(cfg, epochs=3, rank=8, python="python3.11")
    parts = shlex.split(line)                      # what a shell would run
    assert parts[0] == "python3.11"
    assert parts[1].endswith(os.path.join("train", "finetune_mlx.py"))
    assert os.path.isabs(parts[1]), "the command must run from anywhere"
    for flag in ("--train", "--val", "--model", "--out", "--epochs", "--rank"):
        assert flag in parts
    # this project's own directory has a space in it; an unquoted command would
    # have split into two arguments right there
    assert os.path.join(cfg.data_dir, "training", "train.jsonl") in parts


def test_a_command_with_a_space_in_a_path_survives_the_shell(tmp_path):
    spaced = os.path.join(str(tmp_path), "a home with spaces")
    cfg = Config(source={"data_dir": spaced})
    parts = shlex.split(retrain.command(cfg, python="python3.11"))
    assert os.path.join(spaced, "training", "val.jsonl") in parts
    assert " ".join(["a", "home"]) not in parts


def test_the_trainer_itself_agrees_the_printed_split_is_a_valid_one(tmp_path):
    """The format gate is the trainer's own `load_split`, borrowed rather than
    restated — a restatement is a second opinion nobody asked for."""
    trainer, path = retrain.load_trainer()
    assert trainer is not None and os.path.isfile(path)
    assert hasattr(trainer, "load_split") and hasattr(trainer, "mlx_available")
    assert trainer.REQUIRED_KEYS == ("state", "question", "options", "answer")


def test_the_trainer_rejects_a_row_that_is_missing_a_key(tmp_path):
    """The negative side of borrowing the contract: if `load_split` never fails,
    the call in `split_rows` is decoration."""
    trainer, _path = retrain.load_trainer()
    cfg = _cfg(tmp_path)
    bad = os.path.join(cfg.data_dir, "bad.jsonl")
    with open(bad, "w") as f:
        f.write(json.dumps({"state": "s", "question": "keep"}) + "\n")
    with pytest.raises(ValueError):
        trainer.load_split(bad)


def test_the_provenance_sha_is_a_real_commit_in_this_tree(tmp_path):
    sha = retrain.git_sha()
    assert sha and re.fullmatch(r"[0-9a-f]{7,40}", sha), sha


def test_the_toolchain_is_read_from_the_machine_not_wished_for(tmp_path):
    """`mlx: true` on a machine without MLX would tell an operator to run a trainer
    that cannot start, and the record would carry that claim into the comparison."""
    trainer, path = retrain.load_trainer()
    toolchain = retrain.toolchain(trainer, path)
    assert toolchain["trainer"] is True, toolchain
    assert toolchain["mlx"] is False
    state = _state(tmp_path, _rows(4))
    assert state["toolchain"]["mlx"] is False
    assert "mlx + mlx-lm  missing" in retrain.render_text(state, "cmd")
    assert retrain.record(state, "refused")["toolchain"]["mlx"] is False


def test_off_a_git_tree_the_sha_is_none_rather_than_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(retrain, "repo_root", lambda: str(tmp_path))
    assert retrain.git_sha() is None


# ---------------------------------------------------------------- the page

def test_the_page_prints_the_numbers_the_decision_rests_on(tmp_path):
    rows = _rows(4)
    state = _state(tmp_path, rows)
    text = retrain.render_text(state, "python3.11 train/finetune_mlx.py --train x")
    assert "decision  ready" in text
    assert "tool_gate    ok          8 rows     4 keep     4 drop" in text
    assert "nothing was run" in text
    assert "python3.11 train/finetune_mlx.py" in text


def test_a_refusal_prints_every_reason_instead_of_just_the_word_no(tmp_path):
    state = _state(tmp_path, _rows(1, slots=("tool_gate",)), min_per_slot=8)
    text = retrain.render_text(state, "cmd")
    assert "decision  not ready — 2 reasons" in text
    assert "! under 8 examples per slot: tool_gate short 6" in text
    assert "! under 2 in both answers: tool_gate drop short 1, keep short 1" in text


def test_a_run_prints_the_exit_it_got(tmp_path):
    state = _state(tmp_path, _rows(4))
    assert "ran it  exit 4" in retrain.render_text(state, "cmd", ran=4)


def test_the_page_distinguishes_no_trainer_from_no_weights(tmp_path):
    """Three different absences, three different next actions — merging them is
    how a machine looks "not ready" forever with nobody able to say what to fix."""
    state = _state(tmp_path, _rows(4))
    state["toolchain"] = {"trainer": False, "mlx": False, "model_dir": "/x/laya-base",
                          "model_present": False,
                          "trainer_path": "/opt/site-packages/train/finetune_mlx.py"}
    text = retrain.render_text(state, "cmd")
    assert "trainer  missing" in text
    assert "mlx + mlx-lm  missing" in text
    assert "missing (T16)" in text


def test_a_missing_trainer_says_where_it_was_supposed_to_be(tmp_path):
    """`trainer  missing` is two different jobs: `git clone` for a wheel install, and
    "your checkout is broken" for a clone. Only the path it looked at tells them apart."""
    state = _state(tmp_path, _rows(4))
    state["toolchain"] = {"trainer": False, "mlx": False, "model_dir": "/x/laya-base",
                          "model_present": False,
                          "trainer_path": "/opt/site-packages/train/finetune_mlx.py"}
    text = retrain.render_text(state, "cmd")
    assert "the trainer comes with a source clone" in text
    assert "pip install subproto" in text
    # the path is the thing a reader copies, so it sits alone rather than mid-sentence
    assert "/opt/site-packages/train/finetune_mlx.py" in [
        line.strip() for line in text.splitlines()]


# ---------------------------------------------------------------- what it must not do

def test_readiness_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    retrain.readiness(cfg, train=_rows(4), val=[], history=[], trainer=None,
                      trainer_path="x", min_per_slot=8, min_answers=2)
    assert not os.path.exists(retrain.history_path(cfg))
    assert not os.path.exists(os.path.join(cfg.data_dir, "training"))


def test_the_dry_run_default_is_the_only_way_to_get_a_planned_record(tmp_path):
    """`--record` writes the plan; a bare call must not, or every status check
    would age the cadence clock by one row."""
    cfg = _cfg(tmp_path)
    state = retrain.readiness(cfg, train=_rows(4), val=[], history=[], trainer=None,
                              trainer_path="x", min_per_slot=8, min_answers=2)
    assert retrain.load_history(cfg) == []
    retrain.append_history(cfg, retrain.record(state, "planned"))
    assert retrain.load_history(cfg)[0]["status"] == "planned"


def test_today_on_this_machine_the_trainer_cannot_finish(tmp_path):
    """The honest state of V2-D, asserted so a future 'it trained' claim cannot
    arrive quietly: no mlx, no weights, so `run_command` exits non-zero."""
    trainer, path = retrain.load_trainer()
    assert trainer.mlx_available() is False
    assert trainer.train(type("A", (), {"model": str(tmp_path), "train": "x",
                                        "val": None, "out": str(tmp_path / "o"),
                                        "epochs": 1, "rank": 4})()) in (3, 4)


def test_a_split_rebuilt_from_no_traffic_is_empty_and_says_why(tmp_path, monkeypatch):
    """End-to-end through `dataset`: an empty data dir yields zero rows, and the
    refusal names the missing step instead of reporting a 0-example split as ready."""
    monkeypatch.setenv("SUBPROTO_NO_VERBOSE_LOG", "1")
    cfg = _cfg(tmp_path)
    train, val, trainer, path = retrain.split_rows(cfg)
    assert (train, val) == ([], [])
    state = retrain.readiness(cfg, history=[], min_per_slot=8)
    assert not state["ready"]
    assert any("no examples" in r for r in state["reasons"])
    assert os.listdir(cfg.data_dir) and not [
        f for f in os.listdir(cfg.data_dir) if f.startswith(".check-")]


def test_timestamps_used_by_the_state_are_today_not_a_hardcoded_day(tmp_path):
    state = _state(tmp_path, _rows(4))
    assert state["at"] == time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- the CLI surface
# Everything above exercises the policy in isolation. These tests run the printed page
# over real telemetry, because the two defects this feature actually shipped with — a
# command that named a split nothing had written, and a failed trainer whose version
# could still be `--use`d — only exist where the CLI meets the disk.

PINS = ("SUBPROTO_MODEL", "SUBPROTO_MODEL_COMPACT", "SUBPROTO_MODEL_TOOL_GATE",
        "LAYA_URL", "OPENJEV_URL", "DJEV_URL", "SEMIF_URL", "SUBPROTO_MLX_URL",
        "SUBPROTO_ROUTER", "SUBPROTO_HOME")


@pytest.fixture(autouse=True)
def _no_pins(monkeypatch):
    for key in PINS:
        monkeypatch.delenv(key, raising=False)


def _server_url():
    import socket
    from subproto.laya_server import LayaServer
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return LayaServer(port=port, backend="lexical").start()


@pytest.fixture
def server():
    srv = _server_url()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.stop()


def _traffic(tmp_path, per_answer=3):
    """Real telemetry rows, so the split is built by `dataset` and not by the test."""
    from subproto.telemetry import Telemetry
    cfg = Config(source={"data_dir": str(tmp_path)})
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    n = 0
    for slot in ("tool_gate", "compact"):
        for keep in (True, False):
            for _ in range(per_answer):
                n += 1
                targets = [("read", keep), ("slack", not keep)]
                tel.record({"ts": float(n), "api": "anthropic", "model": "m",
                            "status": 200,
                            "usage": {"model": "m", "input_uncached": 100,
                                      "cache_write": 0, "cache_read": 0, "output": 5,
                                      "reasoning": 0, "source": "anthropic"},
                            "features": {"cat_chars": {},
                                         "tool_names": [t for t, _ in targets]},
                            "body_sha": "sha-%d" % n,
                            "tool_names": json.dumps([t for t, _ in targets]),
                            "decisions": [{"slot": slot, "backend": "heuristic",
                                           "candidates": [{"target": t, "keep": k}
                                                          for t, k in targets]}]})
    tel.close()
    return cfg


def _cli(tmp_path, *argv):
    from subproto import cli
    return cli.main(["retrain", "--home", str(tmp_path)] + list(argv))


READY = ("--min-per-slot", "2", "--min-answers", "2", "--min-days", "0")


def test_a_plain_call_prints_the_page_and_touches_nothing(tmp_path, capsys):
    _traffic(tmp_path)
    assert _cli(tmp_path) == 0
    out = capsys.readouterr().out
    assert "subproto retrain" in out and "the command this would run" in out
    assert not os.path.exists(tmp_path / "training_history.json")
    assert not os.path.exists(tmp_path / "training")


def test_running_a_cadence_refusal_records_the_reason_and_writes_no_split(tmp_path, capsys):
    _traffic(tmp_path)
    assert _cli(tmp_path, "--run", "--min-per-slot", "400") == 1
    capsys.readouterr()
    row = json.load(open(str(tmp_path / "training_history.json")))[-1]
    assert row["status"] == "refused" and row["exit"] is None
    assert any("under 400 examples per slot" in r for r in row["reasons"])
    assert not (tmp_path / "training").exists()
    assert _cli(tmp_path, "--run", "--min-per-slot", "400") == 1
    assert "nothing was run" in capsys.readouterr().out


def test_a_ready_run_trains_on_the_split_whose_hash_it_printed(tmp_path, capsys):
    """The bug this proves is fixed: a command naming a file no step had written.
    The trainer exits non-zero here for the *honest* reason (no MLX), which it can
    only reach if the two JSONL files exist."""
    _traffic(tmp_path)
    assert _cli(tmp_path, "--run", *READY) == 1
    out = capsys.readouterr().out
    assert "same rows as the page" in out, out
    assert retrain.fingerprint_rows([str(tmp_path / "training" / "train.jsonl"),
                                     str(tmp_path / "training" / "val.jsonl")])[:12] in out, \
        "the page prints the fingerprint of what it wrote, not just a claim"
    assert (tmp_path / "training" / "train.jsonl").exists()
    row = json.load(open(str(tmp_path / "training_history.json")))[-1]
    assert row["status"] == "ran" and row["exit"] != 0 and row["version"] == "lora-v1"
    on_disk = retrain.fingerprint_rows([str(tmp_path / "training" / "train.jsonl"),
                                       str(tmp_path / "training" / "val.jsonl")])
    assert on_disk == row["split_sha"]


def test_a_run_after_a_finished_one_on_the_same_split_is_refused(tmp_path, capsys):
    """A trainer that died for infra reasons must be retryable, so only a *completed*
    run freezes the split it consumed — and then the next version is numbered after it."""
    cfg = _traffic(tmp_path)
    state = retrain.readiness(cfg, min_per_slot=2, min_answers=2, min_days=0)
    retrain.append_history(cfg, retrain.record(state, "ran", version="lora-v1",
                                               exit_code=0))
    _cli(tmp_path, "--run", *READY)
    assert "has not changed" in capsys.readouterr().out
    rows = json.load(open(str(tmp_path / "training_history.json")))
    assert [r["status"] for r in rows] == ["ran", "refused"]
    assert rows[-1]["version"] == "planned"


def test_json_carries_the_decision_the_record_and_the_split_it_wrote(tmp_path, capsys):
    _traffic(tmp_path)
    _cli(tmp_path, "--run", "--json", *READY)
    st = json.loads(capsys.readouterr().out)
    assert st["ready"] and st["recorded"] == "ran" and st["exit"] != 0
    assert st["split_written"]["split_sha"] == st["split_sha"]
    assert st["command"].startswith("/opt") or os.path.isabs(
        shlex.split(st["command"])[0])
    _cli(tmp_path, "--json")
    dry = json.loads(capsys.readouterr().out)
    assert dry["recorded"] is None and dry["split_written"] is None
    # the floors are the code's defaults here, and they are not met by 24 examples
    assert dry["ready"] is False


def test_the_json_document_survives_a_real_pipe(tmp_path):
    """capsys cannot see this: the trainer is a *child* process writing to the
    inherited stdout, so only a pipe proves whether `--json` emits one parseable
    document or a document with a progress line glued to the front of it."""
    import subprocess
    _traffic(tmp_path)
    out = subprocess.run([sys.executable, "-m", "subproto", "retrain",
                          "--home", str(tmp_path), "--run", "--json"] + list(READY),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                             os.path.abspath(__file__)))),
                         universal_newlines=True)
    st = json.loads(out.stdout)
    assert st["recorded"] == "ran" and st["exit"] == 3, out.stdout[:200]
    assert "mlx" in out.stderr


def test_record_plans_without_running_and_the_two_never_merge(tmp_path, capsys):
    _traffic(tmp_path)
    assert _cli(tmp_path, "--record") == 0
    capsys.readouterr()
    row = json.load(open(str(tmp_path / "training_history.json")))[-1]
    assert row["status"] == "planned" and row["version"] == "planned"
    assert not (tmp_path / "training").exists()
    _cli(tmp_path, "--run", *READY)
    rows = json.load(open(str(tmp_path / "training_history.json")))
    assert [r["version"] for r in rows] == ["planned", "lora-v1"]


# ---------------------------------------------------- history -> `models --use`

def _models(tmp_path, *argv):
    from subproto import cli
    return cli.main(["models", "--home", str(tmp_path)] + list(argv))


def test_a_recorded_run_that_finished_becomes_a_selectable_version(tmp_path, capsys):
    cfg = _traffic(tmp_path)
    state = retrain.readiness(cfg, min_per_slot=2, min_answers=2, min_days=0)
    retrain.append_history(cfg, retrain.record(state, "ran", version="lora-v7",
                                               exit_code=0))
    capsys.readouterr()
    assert _models(tmp_path, "--use", "lora-v7") == 0
    out = capsys.readouterr().out
    assert "registered lora-v7 from training_history" in out
    assert "no endpoint to serve it" in out
    manifest = json.load(open(str(tmp_path / "models.json")))
    assert manifest["active"] == "lora-v7"
    assert manifest["versions"][0]["version"] == "lora-v7"
    assert manifest["versions"][0]["tier"] == "personal"


def test_a_recorded_run_that_did_not_finish_is_not_selectable(tmp_path, capsys):
    cfg = _traffic(tmp_path)
    state = retrain.readiness(cfg, min_per_slot=2, min_answers=2, min_days=0)
    retrain.append_history(cfg, retrain.record(state, "ran", version="lora-v3",
                                               exit_code=3))
    retrain.append_history(cfg, retrain.record(state, "planned", version="planned"))
    assert _models(tmp_path, "--use", "lora-v3") != 0
    err = capsys.readouterr().err
    assert "no version 'lora-v3' registered" in err
    assert not os.path.exists(tmp_path / "models.json")


def test_a_completed_record_with_an_endpoint_gets_that_url(tmp_path, capsys, server):
    cfg = _traffic(tmp_path)
    state = retrain.readiness(cfg, min_per_slot=2, min_answers=2, min_days=0)
    retrain.append_history(cfg, retrain.record(state, "ran", version="lora-v8",
                                               exit_code=0))
    capsys.readouterr()
    assert _models(tmp_path, "--use", "lora-v8", "--url", server) == 0
    assert "no endpoint" not in capsys.readouterr().out
    manifest = json.load(open(str(tmp_path / "models.json")))
    assert manifest["versions"][0]["url"] == server
