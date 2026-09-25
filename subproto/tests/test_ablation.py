"""T15: the tool_gate ablation must compute precision for both backends and a
mutual-agreement rate, and render them."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import bench.ablation as ablation


def _in_range(x):
    return 0.0 <= x <= 1.0


def test_run_ablation_shape():
    m = ablation.run_ablation()
    assert m["n_cases"] == len(ablation.GROUND_TRUTH)
    assert _in_range(m["agreement"])
    for backend in ("heuristic", "laya"):
        b = m[backend]
        for k in ("precision", "recall", "f1"):
            assert _in_range(b[k]), (backend, k)
        assert b["precision"] > 0.5  # the estimators beat chance on this set
    assert "precision_delta_laya_minus_heuristic" in m
    assert len(m["per_case"]) == m["n_cases"]


def test_ground_truth_is_consistent():
    for case in ablation.GROUND_TRUTH:
        assert set(case["keep"]) <= set(case["tools"]), case["query"][:30]
        assert "mcp__" in " ".join(case["tools"])  # every case has a droppable tool


def test_render_includes_both_backends():
    m = ablation.run_ablation()
    text = ablation.render(m)
    assert "heuristic" in text and "laya" in text and "precision" in text
