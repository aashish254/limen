"""S40: the latency curve's report is generated from its own data, so the data
has to be able to contradict the prose. These tests hand `render()` both verdicts
without loading a checkpoint — the branch that cannot fail is the branch that
prints a confident sentence about a measurement it never made.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import bench.laya_latency as curve  # noqa: E402


def _row(shape, p50, p95, mx, f1_threshold, f1_rank, shipped=True):
    return {"shape": shape, "shipped": shipped,
            "passes": 1 if shape == "choice" else "one per option",
            "ms": {"n": 30, "p50": p50, "p95": p95, "max": mx},
            "rules": {"threshold": {"precision": 0.9, "recall": 0.1,
                                    "f1": f1_threshold},
                      "rank": {"precision": 0.87, "recall": 0.87, "f1": f1_rank},
                      "relative": {"precision": 0.9, "recall": 0.14, "f1": 0.24}},
            "per_case": []}


def _metric(p95):
    return {"conditions": {"python": "3.11.15", "torch": "2.6.0", "laya": "0.3.20",
                           "model": "convaiinnovations/laya", "device": "cpu",
                           "threads": 4, "cores": 10,
                           "load_avg_start": [1.0, 2.0, 3.0],
                           "load_avg_end": [4.0, 3.0, 3.0],
                           "runtime_warnings": ["laya: uncalibrated temperatures"]},
            "budget_ms": 350,
            "heuristic": {"precision": 0.972, "recall": 0.986, "f1": 0.979},
            "shapes": [_row("choice", 123, p95, max(p95, 139), 0.200, 0.873),
                       _row("keep_drop", 800, 1250, 1383, 0.704, 0.873),
                       _row("noul", 1097, 1581, 2386, 0.081, 0.859, False)]}


def test_the_curve_is_a_table_with_every_shape():
    text = curve.render(_metric(133))
    for shape in ("choice", "keep_drop", "noul"):
        assert "| %s" % shape in text, shape
    assert "| heuristic (in-process, no model) | 0 |" in text
    assert "350 ms" in text, "the budget the verdicts are measured against is printed"
    assert text.count("|---|") == 2, "one rule line per table, and there are two tables"


def test_the_verdict_column_follows_the_data_not_the_hope():
    fits = curve.render(_metric(133))
    over = curve.render(_metric(421))
    assert "| choice | 1 | 123 | 133 | 139 | yes |" in fits
    assert "| choice | 1 | 123 | 421 | 421 | no |" in over
    assert "It fits, so MLX would have been an optimisation" in fits
    assert "It fits" not in over
    assert "no MLX build was ever the fix" in over


def test_the_shape_that_costs_more_is_named_with_its_ratio():
    text = curve.render(_metric(133))
    assert "`keep_drop`, `noul`" in text, "the slow shapes are listed by name"
    assert "7x the cost" in text


def test_conditions_are_reported_rather_than_assumed():
    """A wall-clock claim without its host is the artifact this repo has promised
    never to publish again (SPEC §11.1, I6)."""
    text = curve.render(_metric(133))
    assert "load average at start 1.0, 2.0, 3.0" in text
    assert "uncalibrated temperatures" in text, "the runtime's own warning is carried"
    assert "torch 2.6.0" in text and "laya 0.3.20" in text
    assert "synthetic" in text.lower(), "the ground truth is labelled as such"


def test_a_host_with_no_load_average_still_gets_a_curve(monkeypatch):
    """Windows has no `os.getloadavg`, and a POSIX kernel can still refuse the call.

    Both branches are forced here rather than waited for on a runner that does not
    exist yet: a bench that raises there publishes nothing, and a bench that prints
    the absence as if it were a figure lies about the host it measured.
    """
    monkeypatch.delattr(os, "getloadavg", raising=False)
    cond = curve.conditions(4, "cpu")
    assert cond["load_avg_start"] is None
    m = _metric(133)
    m["conditions"].update(load_avg_start=None, load_avg_end=None)
    text = curve.render(m)
    assert "kept no load average" in text
    load_lines = [l for l in text.splitlines() if "load average" in l]
    assert load_lines and not any("None" in l for l in load_lines), \
        "an absent figure must not print as a value"
    assert "| choice | 1 |" in text, "the table itself is unaffected"


def test_the_conditions_block_reads_the_host_it_ran_on():
    """A `null` load figure is only honest on a machine that has none to give.

    This host has one, so the block has to carry three numbers beside the core count
    they are read against — and a conditions block that quietly stopped reading its
    host would still print the same confident curve.
    """
    assert hasattr(os, "getloadavg"), "this test is the posix half of the branch"
    cond = curve.conditions(4, "cpu")
    assert cond["device"] == "cpu" and cond["cores"] == os.cpu_count()
    assert cond["threads"] == 4
    load = cond["load_avg_start"]
    assert len(load) == 3 and all(isinstance(x, float) for x in load), load
    m = _metric(133)
    m["conditions"] = dict(m["conditions"], **cond)
    text = curve.render(m)
    assert "kept no load average" not in text, "this host answered"
    assert "host load average at start %s" % ", ".join(str(x) for x in load) in text


def test_headings_are_separated_from_the_table_above_them():
    text = curve.render(_metric(133))
    lines = text.splitlines()
    for i, line in enumerate(lines[1:], 1):
        if line.startswith("## "):
            assert lines[i - 1] == "", "heading %r is glued to the previous line" % line


def test_the_noul_shape_is_marked_as_not_shipped():
    text = curve.render(_metric(133))
    assert "noul (not shipped)" in text
    assert "| choice | 1 |" in text and "| keep_drop | one per option |" in text
