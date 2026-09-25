"""T10: the live pass-rate–held harness runs end-to-end against the mock and
reports a numeric token reduction while keeping the correctness floor.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import bench.live as live


def test_harness_runs_and_holds_pass_rate():
    tasks_path = os.path.join(os.path.dirname(os.path.abspath(live.__file__)),
                              "tasks.sample.jsonl")
    tasks = live.load_tasks(tasks_path)
    assert len(tasks) >= 3
    results = live.run_arms(tasks, iters=1, seed=3)
    m = live.compute_metrics(results, bootstrap=200, seed=3)
    # correctness floor: enforcing must not regress the pass rate
    assert m["pass_rate"]["observe"] == 100.0
    assert m["pass_rate"]["enforce"] == 100.0
    assert m["pass_rate"]["delta_pct"] >= -1.0
    # we must actually deliver a numeric input-token reduction
    assert m["input_tok"]["enforce_mean"] < m["input_tok"]["observe_mean"]
    assert 0.0 < m["input_tok"]["reduction_pct"] < 100.0
    assert m["input_tok"]["reduction_ci"][0] <= m["input_tok"]["reduction_ci"][1]


def test_enforced_body_keeps_required_evidence():
    """Invariant I3, directly: the request builder embeds each context file's path,
    so a file the answer depends on is present for the model to see."""
    task = {"id": "x", "task": "fix retry", "grader": "files_present",
            "expected_files": ["app/payments/retry.py"],
            "context": [{"path": "app/legacy/a.py", "stale": True},
                        {"path": "app/payments/retry.py", "stale": False}]}
    body = live.build_request(task, 0)
    text = json.dumps(body)
    assert "app/payments/retry.py" in text
    assert live.grade({"_mock_echo": body}, task) == 1


def test_render_mentions_held_and_ci():
    tasks = live.load_tasks(os.path.join(
        os.path.dirname(os.path.abspath(live.__file__)), "tasks.sample.jsonl"))
    results = live.run_arms(tasks[:2], iters=1, seed=5)
    m = live.compute_metrics(results, bootstrap=100, seed=5)
    text = live.render(m, "tasks.sample.jsonl")
    assert "pass-rate" in text and "95% CI" in text and "HELD" in text
