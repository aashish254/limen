"""S30 — consuming the implicit labels: `learn`, the export columns, the split, the report.

The traffic fixture is the one `test_implicit.py` builds (real `compile_turn` proofs,
bodies spooled exactly as the proxy spools them), imported here rather than copied —
under pytest's default import mode the tests dir is on `sys.path`, so the two files
share one definition of "a session that re-reads a file it just lost".
"""

import json
import os
import re

import pytest

from subproto import cli, dataset, implicit, protocol, report
from subproto.config import Config
from subproto.telemetry import Telemetry

from test_implicit import RETRY, _bare_pair, _compile, _home, _read_pair, _record, \
    _session, _telemetry, _turn_a, _turn_b


def _re_read_home(tmp_path):
    """A two-turn session whose second turn re-reads the file the first turn cut."""
    return _session(tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
                    budgets=[1400, 4200])[0:2]


def _printed(text, needle):
    """The numbers on the printed line that starts with `needle`.

    Asserted by parsing rather than by matching spaces: the columns are for humans,
    and a re-aligned report is not a changed measurement.
    """
    line = next(l for l in text.splitlines() if needle in l)
    return [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", line[len(needle):])]


# ---------------------------------------------------------------- the CLI witness

def test_learn_writes_the_store_and_its_text_prints_the_harvest(tmp_path, capsys):
    config, telemetry = _re_read_home(tmp_path)
    rc = cli.main(["learn", "--home", str(tmp_path / "home")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "subproto learn" in out
    assert "implicit:re-read" in out
    assert _printed(out, "evicted reads seen") == [2], "both turns cut a read"
    assert _printed(out, "regrettable drops") == [1, 1, 0], "1 of 2, enforced"
    assert "2954 tokens were paid back" in out,                  "the re-read's price must be the number the harvest holds"
    assert _printed(out, "verdicts by signal") == [1, 2954],     "the same price, attributed to its signal"
    stored = implicit.load_implicit(config)
    assert list(stored) == ["1"], "the blame sits on the turn that cut the read"
    label = list(stored["1"]["targets"].values())[0]
    assert label["verdict"] == "keep" and label["regret_tok"] == 2954
    telemetry.close()


def test_learn_json_is_parseable_and_carries_the_same_numbers(tmp_path, capsys):
    config, telemetry = _re_read_home(tmp_path)
    cli.main(["learn", "--home", str(tmp_path / "home"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["wrong_drops_enforced"] == 1
    assert payload["summary"]["refetch_tok_paid"] == 2954
    assert payload["labels"]["1"]["targets"]
    assert os.path.exists(implicit.implicit_path(config))
    telemetry.close()


def test_learn_dry_run_writes_nothing(tmp_path, capsys):
    config, telemetry = _re_read_home(tmp_path)
    cli.main(["learn", "--home", str(tmp_path / "home"), "--dry-run"])
    out = capsys.readouterr().out
    assert "--dry-run: nothing written" in out
    assert not os.path.exists(implicit.implicit_path(config))
    assert implicit.load_implicit(config) == {}
    telemetry.close()


def test_learn_without_recorded_bodies_says_so_instead_of_inventing(tmp_path, capsys):
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    sha, decisions, _ = _compile(_turn_a())
    _record(config, telemetry, _turn_a(), decisions, 1000.0)
    for name in os.listdir(config.bodies_dir):
        os.remove(os.path.join(config.bodies_dir, name))
    cli.main(["learn", "--home", str(tmp_path / "home")])
    out = capsys.readouterr().out
    assert "Nothing to judge" in out
    assert "--store-bodies" in out
    assert "wrong_drops" not in out, "no harvest, no regret table to admire"
    telemetry.close()


def test_a_second_learn_replaces_the_store_only_when_the_harvest_changed(tmp_path, capsys):
    """The store is written, not re-written: an identical harvest must not touch it.

    The inode is the witness — `save_implicit` swaps a temp file in, so a rewrite
    means a new inode even when the bytes come out the same.
    """
    config, telemetry = _re_read_home(tmp_path)
    first = implicit.run(config, telemetry)
    assert first["path"] == implicit.implicit_path(config)
    inode = os.stat(first["path"]).st_ino
    second = implicit.run(config, telemetry)
    assert second.get("unchanged") and not second.get("written_at")
    assert os.stat(first["path"]).st_ino == inode, "an unchanged harvest must not rewrite"
    assert second["summary"]["already_stored"] == 1 == second["summary"]["stored"]
    capsys.readouterr()
    cli.main(["learn", "--home", str(tmp_path / "home")])
    assert "nothing new to store" in capsys.readouterr().out


def test_a_harvest_that_found_nothing_creates_no_store_at_all(tmp_path):
    """Drops happened; no later traffic contradicted them — that is not a result to file."""
    config, telemetry = _session(tmp_path, [_turn_a()])[0:2]
    out = implicit.run(config, telemetry)
    assert out["summary"]["evicted_reads_seen"] > 0
    assert out["summary"]["wrong_drops"] == 0 and out["labels"] == {}
    assert out["path"] is None
    assert not os.path.exists(implicit.implicit_path(config)), \
        "an empty run must not leave a file that looks like a harvest"
    assert implicit.load_implicit(config) == {}


# --------------------------------------------------------------- the export columns

def _export_lines(config, telemetry):
    out = dataset.export(config, telemetry, path=str(tmp := (config.data_dir + "/ds.jsonl")))
    with open(tmp) as f:
        return out, [json.loads(line) for line in f]


def _export_with(tmp_path, human=None):
    config, telemetry = _re_read_home(tmp_path)
    implicit.run(config, telemetry)
    if human:
        dataset.save_labels(config, human)
    return _export_lines(config, telemetry)


def test_export_rows_carry_the_traffic_verdict_alongside_the_human_one(tmp_path):
    _out, rows = _export_with(tmp_path)
    compact = [r for r in rows if r["slot"] == "compact" and r["request_id"] == 1][0]
    assert compact["label"] == "keep"
    assert compact["label_source"] == "implicit:re-read"
    assert compact["implicit_label"] == "keep"
    assert compact["human_label"] is None, "nobody labelled this; the traffic did"
    later = [r for r in rows if r["request_id"] == 2][0]
    assert later["label"] is None and later["implicit_label"] is None, \
        "the turn that did the re-reading is not itself an verdict about a drop"


def test_a_human_good_verdict_and_a_re_read_agree_without_muddling(tmp_path):
    _out, rows = _export_with(tmp_path, human={"1": {"compact": "good",
                                                    "reason": "human said so"}})
    row = [r for r in rows if r["slot"] == "compact" and r["request_id"] == 1][0]
    assert row["label"] == "good"
    assert row["label_source"] == "human+implicit", "both stores answered the same thing"
    assert row["implicit_label"] == "keep"
    assert row["human_label"] == "good"


def test_a_human_bad_verdict_against_a_re_read_reports_the_disagreement(tmp_path):
    _out, rows = _export_with(tmp_path, human={"1": {"compact": "bad"}})
    row = [r for r in rows if r["slot"] == "compact" and r["request_id"] == 1][0]
    assert row["label"] == "mixed" and row["label_source"] == "mixed"
    assert row["implicit_label"] == "keep", "the traffic's own answer stays visible"


def test_export_counts_labelled_rows_by_who_labelled_them(tmp_path):
    out, _rows = _export_with(tmp_path)
    assert out["labelled_by_traffic"] == 1
    assert out["labelled_by_human"] == 0
    assert out["sources"] == {"implicit:re-read": 1}


# ---------------------------------------------------------------- the training split

def _split(tmp_path, human=None):
    config, telemetry = _re_read_home(tmp_path)
    implicit.run(config, telemetry)
    if human:
        dataset.save_labels(config, human)
    res = dataset.build_training_split(config, telemetry, write=False)
    return res, res["train"] + res["val"]


def test_a_re_read_flips_the_teaching_answer_to_keep(tmp_path):
    _res, examples = _split(tmp_path)
    taught = [e for e in examples if e["slot"] == "compact"
              and (e["label_source"] or "").startswith("implicit:")]
    assert taught, "the contradicted turn's candidates must be supervised"
    assert all(e["answer"] == "keep" for e in taught), \
        "a drop the traffic re-read is never taught as a drop"
    flipped = [e for e in taught if e["options"][0] == "tool_result#3"]
    assert flipped and flipped[0]["answer"] == "keep", \
        "the very candidate the turn cut is now the positive example"


def test_a_turn_the_traffic_only_complained_about_is_never_taught(tmp_path):
    """Correction/re-run names no candidate, so it removes supervision, not adds it."""
    config = _home(tmp_path)
    telemetry = _telemetry(config)
    a = _turn_a()
    sha = _record(config, telemetry, a, _compile(a, budget=1400)[1], 1000.0)
    later = _turn_b([{"role": "user", "content": "no, that's wrong — read the file"}])
    _record(config, telemetry, later, _compile(later, budget=4200)[1], 1030.0)
    implicit.run(config, telemetry)
    stored = implicit.load_implicit(config)
    assert list(stored["1"]["slots"].values())[0]["source"] == "implicit:correction"
    res = dataset.build_training_split(config, telemetry, write=False)
    ex = res["train"] + res["val"]
    assert [e for e in ex if e["body_sha"] == sha] == [], \
        "the complained-about turn contributes no keep/drop answer at all"
    assert ex, "the later turn is still ordinary heuristic supervision"
    assert res["n_from_traffic"] == 0


def test_a_human_bad_verdict_still_beats_the_traffic(tmp_path):
    """The disagreement is `mixed`, and mixed is not supervision."""
    _res, examples = _split(tmp_path, human={"1": {"compact": "bad"}})
    assert [e for e in examples if e["slot"] == "compact"
            and (e["label_source"] or "").startswith("implicit:")] == []


def test_split_stats_separate_the_three_label_sources(tmp_path):
    res, examples = _split(tmp_path)
    assert res["n_supervised"] == res["n_from_traffic"] == len(
        [e for e in examples if e["label_source"] != "heuristic"])
    assert res["n_traffic_drops_taught"] == 0, "I3: never teach a drop the agent undid"
    assert res["n_traffic_keeps"] == sum(1 for e in examples
                                         if e["label_source"] == "implicit:re-read"
                                         and e["answer"] == "keep")


# ---------------------------------------------------------------- the report section

def test_report_prints_eviction_regret_with_its_honest_split(tmp_path, capsys):
    config, telemetry = _re_read_home(tmp_path)
    harvest = implicit.run(config, telemetry)
    summary = report.summarize(telemetry, labels=dataset.load_labels(config),
                               implicit=harvest)
    er = summary["eviction_regret"]
    assert er["regrettable_drops"] == 1 and er["enforced"] == 1 and er["shadow"] == 0
    assert er["refetch_tok_paid"] == 2954
    assert er["bodies_never_stored"] == 0
    text = report.render_text(summary)
    assert "eviction regret" in text
    assert _printed(text, "regrettable drops") == [1, 1, 0]
    assert "2954 tokens were paid back" in text
    assert "verdicts by signal: implicit:re-read: 1 label, 2954 tok" in text
    cov = summary["implicit_coverage"]
    assert cov["covered_by_harvest"] == 1 and cov["coverage"] == 0.5
    assert cov["covered_by_human"] == 0 and cov["disagreements"] == 0
    telemetry.close()


def test_a_shadow_only_harvest_pays_back_zero_tokens(tmp_path):
    config, telemetry = _session(
        tmp_path, [_turn_a(), _turn_b(_read_pair(9, RETRY, "retry"))],
        enforce=(), budgets=[1400, 4200])[0:2]
    harvest = implicit.run(config, telemetry)
    assert harvest["summary"]["wrong_drops_shadow"] == 1
    er = report.eviction_regret(harvest)
    assert er["refetch_tok_paid"] == 0 and er["enforced"] == 0 and er["shadow"] == 1
    text = report.render_text(report.summarize(telemetry, implicit=harvest))
    assert "0 tokens were paid back" in text
    assert "while the 1 shadow drop cost nothing because nothing was cut" in text
    telemetry.close()


def test_the_regret_section_is_absent_when_no_harvest_was_asked(tmp_path):
    config, telemetry = _re_read_home(tmp_path)
    summary = report.summarize(telemetry)
    assert "eviction_regret" not in summary
    assert "eviction regret" not in report.render_text(summary)
    telemetry.close()


def test_render_json_still_exposes_the_new_sections(tmp_path):
    config, telemetry = _re_read_home(tmp_path)
    harvest = implicit.run(config, telemetry)
    payload = report.render_json(report.summarize(telemetry, implicit=harvest))
    assert payload["eviction_regret"]["regrettable_drops"] == 1
    assert payload["cache_headroom"] and payload["slot_headroom"], "old keys intact"
    telemetry.close()


def test_report_cli_learns_by_default_and_skips_on_no_learn(tmp_path, capsys):
    _config, telemetry = _re_read_home(tmp_path)
    telemetry.close()
    assert cli.main(["report", "--home", str(tmp_path / "home")]) == 0
    assert "eviction regret" in capsys.readouterr().out
    assert cli.main(["report", "--home", str(tmp_path / "home"), "--no-learn"]) == 0
    assert "eviction regret" not in capsys.readouterr().out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
