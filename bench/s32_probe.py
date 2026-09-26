"""S32 witness: the paired decision cost of the compiled arm against the per-slot arm.

`bench/live.py --mock` reports request latency, but a request there spends most of its
time in the mock round trip, and two runs of the two arms drift 10-20 % on a laptop —
more than the compiler overhead being looked for. So this measures the *decision* only,
with both arms in the same process against the same payloads, alternating round by
round, and reports the **minimum** per round: interference can only add time, never
remove it, so the minimum is the robust estimator and the paired difference is the
number that means something.

Exit status is 0 either way — a slower compiled arm is a result to report, not a broken
build. `run_tests.sh` runs it with a low round count and prints the line.

Run with `python3.11 bench/s32_probe.py [rounds]`.
"""
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import live  # noqa  (bench/ is on sys.path, so the harness helpers are reusable)
from subproto import graph as graph_mod  # noqa
from subproto.config import Config  # noqa
from subproto.engine import Engine  # noqa
from subproto.proxy import analyze_request  # noqa

ARMS = ("enforce", "compiled")
REPS = 14


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 9
    tasks = live.load_tasks(os.path.join(HERE, "tasks.sample.jsonl"))
    home = live.tempfile.mkdtemp(prefix="subproto-s32-")
    repo = os.path.join(home, "repo")
    os.makedirs(repo)
    live._materialise_repo(tasks, repo)
    gpath = graph_mod.save(graph_mod.build(repo), os.path.join(home, "graph.json"))
    cfg = Config.load(data_dir=home, port=live.free_port(), store_bodies=False)
    cfg.ensure_dirs()
    eng = Engine(cfg, graph_path=gpath)
    # One analysis per task, reused by every round and both arms: `analyze_request` is
    # shared cost, and timing it inside the loop would charge it to the arms.
    reqs = [(live.build_request(t, 0), analyze_request(live.build_request(t, 0)))
            for t in tasks]

    prev = {k: os.environ.get(k) for k in ("SUBPROTO_APPLY", "SUBPROTO_COMPILE")}
    per_round = {a: [] for a in ARMS}
    try:
        for arm in ARMS:  # warm imports, regex caches and the graph's scoring view
            os.environ["SUBPROTO_APPLY"], os.environ["SUBPROTO_COMPILE"] = live.ARM_ENV[arm]
            for body, analysis in reqs:
                eng.decide("anthropic", body, analysis, cfg, body_sha="x")
        for r in range(rounds):
            for arm in ARMS:
                os.environ["SUBPROTO_APPLY"], os.environ["SUBPROTO_COMPILE"] = live.ARM_ENV[arm]
                best = None
                for _ in range(REPS):
                    start = time.perf_counter()
                    for body, analysis in reqs:
                        eng.decide("anthropic", body, analysis, cfg, body_sha="x")
                    ms = (time.perf_counter() - start) * 1000.0 / len(reqs)
                    best = ms if best is None else min(best, ms)
                per_round[arm].append(best)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("%d rounds x %d reps of %d tasks" % (rounds, REPS, len(reqs)))
    for arm in ARMS:
        v = per_round[arm]
        print("  %-9s best %.2f ms/decision   median %.2f   spread %.2f-%.2f"
              % (arm, min(v), statistics.median(v), min(v), max(v)))
    d = [c - e for c, e in zip(per_round["compiled"], per_round["enforce"])]
    print("  paired delta (compiled - per-slot): best %+.3f  median %+.3f  worst %+.3f"
          % (min(d), statistics.median(d), max(d)))
    print("  gate (compiled <= per-slot): %s"
          % ("MET" if min(per_round["compiled"]) <= min(per_round["enforce"]) else "NOT MET"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
