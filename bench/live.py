#!/usr/bin/env python3
"""Pass-rate–held live A/B harness (mock-first: no API key, $0 to reproduce).

Each task is pushed through the proxy twice — observation (nothing enforced) and
enforcement (tool_gate + compact + context) — against the local mock upstream.
The mock echoes back the request body it *actually received*, so the reduction
is measured on the wire, not projected from prompt shapes.

Three numbers, each with a bootstrap confidence interval:
  * input-token reduction delivered to the model
  * p50 request-latency delta  (mock: a shape proxy, not a real TTFB)
  * task pass-rate delta       (must stay >= -1% — the correctness floor, I3)

The `files_present` grader checks that every file the answer depends on survived
enforcement. A slot that hit tokens by evicting needed evidence would fail here,
which is the whole point: we only claim savings that hold the pass-rate.

This is the M3 skeleton running against `fakeup`; wiring it to a real
SWE-bench-style suite + grader is the only thing between this and the hero
number being a measurement rather than a mock-validated method.
"""

import argparse
import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subproto import demo, protocol
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer, analyze_request
from subproto.telemetry import Telemetry

ENFORCE = "tool_gate,compact,context"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def load_tasks(path):
    tasks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def _filler(rng, path, target_tokens):
    # Deterministic filler so stale tool results dominate the prompt the way a
    # real scrolled-past file does, while still naming the file they came from.
    unit = ("# %s\n  cached tool result, scrolled past — legacy dump line %d "
            "with assorted bytes 0x%x and a trailing pad token.\n")
    out = []
    tok = 0
    i = 0
    while tok < target_tokens:
        line = unit % (path, i, rng.randrange(1 << 16))
        out.append(line)
        tok += len(line) / 4.0
        i += 1
    return "".join(out)


def build_request(task, iters):
    """Anthropic-shaped request: a big system prompt, many tool schemas, and one
    assistant tool_use + user tool_result pair per context file (stale files
    first so the tail-protected relevant files are the ones that must survive)."""
    rng = random.Random(zlib.crc32(task["id"].encode()) & 0xFFFFFFFF)
    ctx = sorted(task["context"], key=lambda c: 0 if c.get("stale") else 1)
    messages = [{
        "role": "user",
        "content": task["task"] + " Use the files already read this session.",
    }]
    for i, c in enumerate(ctx):
        big = c.get("stale")
        content = _filler(rng, c["path"], 2600 if big else 60)
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_%d_%d" % (iters, i), "name": "Read",
             "input": {"file_path": c["path"]}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_%d_%d" % (iters, i),
             "content": content}]})
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "system": demo.system_prompt(iters, jitter=False),
        "tools": demo.tool_specs(24, shuffle=False),
        "messages": messages,
        "stream": False,
        "_mock_echo": True,
    }


def grade(body, task):
    if task.get("grader") != "files_present":
        raise ValueError("unknown grader: %s" % task.get("grader"))
    text = json.dumps(body.get("_mock_echo") or body)
    return int(all(p in text for p in task["expected_files"]))


def _post(base, path, obj, timeout=30):
    raw = protocol.dump_body(obj)
    h = {"content-type": "application/json", "x-api-key": "mock-key",
         "anthropic-version": "2023-06-01", "user-agent": "claude-cli/1.0 (bench)"}
    req = urllib.request.Request(base + path, data=raw, headers=h, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    return payload, (time.perf_counter() - t0) * 1000.0


def run_arms(tasks, iters, seed):
    """Return per-task {observe/enforce: {in_tok, latency_ms}}, plus pass flags."""
    cfg = Config.load(data_dir=tempfile.mkdtemp(prefix="subproto-live-"),
                      port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, Engine(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import fakeup.server as mock
    mock_up = mock.MockUpstream(port=free_port()).start()
    cfg.source = dict(cfg.source, anthropic_upstream="http://127.0.0.1:%d" % mock_up.port)
    base = "http://127.0.0.1:%d" % cfg.port

    results = []
    prev_apply = os.environ.get("SUBPROTO_APPLY")
    try:
        for task in tasks:
            row = {"id": task["id"], "observe": {}, "enforce": {}}
            for arm, apply_val in (("observe", ""), ("enforce", ENFORCE)):
                os.environ["SUBPROTO_APPLY"] = apply_val
                toks, lats, passes = [], [], []
                for it in range(iters):
                    body = build_request(task, it)
                    resp, lat = _post(base, "/v1/messages", body)
                    echo = resp.get("_mock_echo") or {}
                    toks.append(analyze_request(echo)["est_in_tok"])
                    lats.append(lat)
                    passes.append(grade(resp, task))
                row[arm] = {
                    "in_tok": sum(toks) / len(toks),
                    "latency_ms": sorted(lats)[len(lats) // 2],
                    "pass": 1 if all(passes) else 0,
                }
            results.append(row)
    finally:
        if prev_apply is None:
            os.environ.pop("SUBPROTO_APPLY", None)
        else:
            os.environ["SUBPROTO_APPLY"] = prev_apply
        srv.shutdown()
        srv.server_close()
        mock_up.stop()
        tel.close()
    return results


def _bootstrap_ci(vals, stat, iters, rng):
    boots = [stat([rng.choice(vals) for _ in vals]) for _ in range(iters)]
    boots.sort()
    return boots[int(0.025 * iters)], boots[int(0.975 * iters)]


def compute_metrics(results, bootstrap, seed):
    rng = random.Random(seed)
    obs = [r["observe"]["in_tok"] for r in results]
    enf = [r["enforce"]["in_tok"] for r in results]
    obs_lat = [r["observe"]["latency_ms"] for r in results]
    enf_lat = [r["enforce"]["latency_ms"] for r in results]
    obs_pass = [r["observe"]["pass"] for r in results]
    enf_pass = [r["enforce"]["pass"] for r in results]

    def _pct_mean(seq):
        return 100.0 * sum(seq) / len(seq)

    per_task_reduction = [(o - e) / o if o else 0.0 for o, e in zip(obs, enf)]
    lat_vals = [(o, e) for o, e in zip(obs_lat, enf_lat)]

    def _lat_red(seq):
        o = sum(x[0] for x in seq)
        e = sum(x[1] for x in seq)
        return 100.0 * (o - e) / o if o else 0.0

    pass_delta = [e - o for o, e in zip(obs_pass, enf_pass)]

    tok_ci = _bootstrap_ci(per_task_reduction, _pct_mean, bootstrap, rng)
    lat_ci = _bootstrap_ci(lat_vals, _lat_red, bootstrap, rng)
    pd_ci = _bootstrap_ci(pass_delta, _pct_mean, bootstrap, rng)
    return {
        "n_tasks": len(results),
        "input_tok": {
            "observe_mean": sum(obs) / len(obs),
            "enforce_mean": sum(enf) / len(enf),
            "reduction_pct": _pct_mean(per_task_reduction),
            "reduction_ci": tok_ci,
        },
        "latency": {
            "p50_observe_ms": sorted(obs_lat)[len(obs_lat) // 2],
            "p50_enforce_ms": sorted(enf_lat)[len(enf_lat) // 2],
            "reduction_pct": _lat_red(lat_vals),
            "reduction_ci": lat_ci,
        },
        "pass_rate": {
            "observe": _pct_mean(obs_pass),
            "enforce": _pct_mean(enf_pass),
            "delta_pct": _pct_mean(pass_delta),
            "delta_ci": pd_ci,
        },
        "per_task": [
            {"id": r["id"], "observe_tok": r["observe"]["in_tok"],
             "enforce_tok": r["enforce"]["in_tok"],
             "observe_pass": r["observe"]["pass"], "enforce_pass": r["enforce"]["pass"]}
            for r in results],
    }


def render(m, tasks_path):
    held = m["pass_rate"]["delta_pct"] >= -1.0
    lines = []
    lines.append("# subproto live harness (mock, pass-rate held)\n")
    lines.append("Tasks: `%s` · n=%d · bootstrap CI · mock upstream, $0, no key.\n"
                 % (os.path.basename(tasks_path), m["n_tasks"]))
    ir = m["input_tok"]
    lines.append("| metric | observe | enforce | delta | 95% CI |")
    lines.append("|---|--:|--:|--:|:--|")
    lines.append("| input tokens (mean/task) | %.0f | %.0f | **%.1f%%** | [%.1f, %.1f] |"
                 % (ir["observe_mean"], ir["enforce_mean"], ir["reduction_pct"],
                    ir["reduction_ci"][0], ir["reduction_ci"][1]))
    la = m["latency"]
    lines.append("| p50 request latency (ms) | %.1f | %.1f | %.1f%% | [%.1f, %.1f] |"
                 % (la["p50_observe_ms"], la["p50_enforce_ms"], la["reduction_pct"],
                    la["reduction_ci"][0], la["reduction_ci"][1]))
    pr = m["pass_rate"]
    lines.append("| task pass-rate | %.1f%% | %.1f%% | **%+.1f pp** | [%+.1f, %+.1f] |"
                 % (pr["observe"], pr["enforce"], pr["delta_pct"],
                    pr["delta_ci"][0], pr["delta_ci"][1]))
    lines.append("")
    lines.append("**Correctness floor (I3): pass-rate delta %.1f pp — %s (gate: >= -1.0pp).**"
                 % (pr["delta_pct"], "HELD" if held else "VIOLATED"))
    lines.append("")
    lines.append("Latency here is a request-shape proxy against the local mock, not a real "
                 "time-to-first-token; it is reported for completeness. The token reduction "
                 "and held pass-rate are the mock-measurable signals. Against a real "
                 "SWE-bench-style suite + grader these become the hero number.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--tasks", default=os.path.join(here, "tasks.sample.jsonl"))
    ap.add_argument("--iters", type=int, default=3, help="repeats per task per arm")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mock", action="store_true", help="accepted for parity; always mock")
    ap.add_argument("--out", default=os.path.join(here, "live_results.md"))
    ap.add_argument("--write", action="store_true",
                    help="refresh the committed results file (default: print only, "
                         "so the gate can run the harness without dirtying tracked "
                         "evidence)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.tasks):
        print("no task file at %s" % args.tasks)
        return 1
    tasks = load_tasks(args.tasks)
    results = run_arms(tasks, args.iters, args.seed)
    m = compute_metrics(results, args.bootstrap, args.seed)
    m["iters"] = args.iters
    if args.json:
        print(json.dumps(m, indent=1))
    else:
        text = render(m, args.tasks)
        print(text)
        if args.write:
            with open(args.out, "w") as f:
                f.write(text + "\n")
            print("\n-> wrote %s" % args.out)
    if m["pass_rate"]["delta_pct"] < -1.0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
