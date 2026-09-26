"""T10: the live pass-rate–held harness runs end-to-end against the mock and
reports a numeric token reduction while keeping the correctness floor.
"""

import json
import os
import sys
import tempfile

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


# --- S23: the accuracy gate, as an executable check ---------------------------

BENCH = os.path.dirname(os.path.abspath(live.__file__))


def _task_big():
    return {"id": "big-needed", "task": "stop the retry helper swallowing the connection error",
            "grader": "files_present",
            "expected_files": ["app/pay/retry.py", "app/pay/errors.py"],
            "context": [{"path": "app/pay/retry.py", "big": True},
                        {"path": "app/legacy/x.py", "stale": True},
                        {"path": "app/legacy/retry_swallow.py", "stale": True},
                        {"path": "app/pay/errors.py"}]}


def test_recall_counts_surviving_content_not_a_mentioned_path():
    """A `tool_use` still names the file it read after its result was evicted, so
    substring-testing the body cannot see evidence loss. This does."""
    task = _task_big()
    body = live.build_request(task, 0)
    totals = live.surviving_dumps(body)
    assert set(totals) >= set(task["expected_files"])
    evicted = dict(body, messages=[
        m for m in body["messages"]
        if not live.protocol.content_to_text(m.get("content")).startswith(
            "@@dump app/pay/retry.py@@")])
    surv = live.surviving_dumps(evicted)
    assert "app/pay/retry.py" not in surv, "the read is gone, the mention is not"
    acc = live.score_dumps(surv, totals, task)
    assert acc["recall"] < 1.0
    assert live.grade({"_mock_echo": evicted}, task) == 0


def test_materialised_repo_ranks_the_file_the_task_is_about():
    """The index the compiled arm buys must be real signal, not a stub: the two files a
    task is about outrank a stale file whose *name* also matches the query."""
    task = _task_big()
    root = os.path.join(tempfile.mkdtemp(prefix="subproto-repo-"), "repo")
    os.makedirs(root)
    live._materialise_repo([task], root)
    g = live.graph_mod.build(root)
    hits = live.graph_mod.search(g, task["task"], top_k=8)
    ranked = {rel: score for rel, score, _ in hits}
    decoy = "app/legacy/retry_swallow.py"
    assert decoy in ranked, "the decoy names a query word, so it must rank somewhere"
    for rel in task["expected_files"]:
        assert ranked[rel] > ranked[decoy], "%s must beat the look-alike" % rel
    top = [rel for rel, _, _ in hits[:2]]
    assert set(top) == set(task["expected_files"])


def test_hard_set_separates_the_arms_on_accuracy():
    """The measurement S23 blocks on: with a needed 2.6k-token read as the oldest turn,
    per-slot evicts it and the joint budget does not — at *lower* spend."""
    tasks = live.load_tasks(os.path.join(BENCH, "tasks.hard.jsonl"))[:4]
    results = live.run_arms(tasks, iters=1, seed=11)
    m = live.compute_metrics(results, bootstrap=200, seed=11)
    cv = m["compiled_vs_enforce"]
    assert cv["recall"]["enforce_pct"] < 50.0, "per-slot loses the needed read"
    assert cv["recall"]["compiled_pct"] == 100.0
    assert cv["precision"]["compiled_pct"] > cv["precision"]["enforce_pct"]
    assert cv["input_tok"]["compiled_mean"] < cv["input_tok"]["enforce_mean"]
    assert cv["verdict"] == "BETTER", cv["verdict_reason"]
    text = live.render(m, "tasks.hard.jsonl")
    assert "VIOLATED" in text and "BETTER" in text
