#!/usr/bin/env python3
"""Pass-rate–held live A/B harness (mock-first: no API key, $0 to reproduce).

Each task is pushed through the proxy three times — observation (nothing enforced),
enforcement (tool_gate + compact + context as separate slots), and the v5 compiled
arm (one joint budget over the same slots) — against the local mock upstream. The
mock echoes back the request body it *actually received*, so the reduction is
measured on the wire, not projected from prompt shapes.

Three numbers per arm, each with a bootstrap confidence interval:
  * input-token reduction delivered to the model
  * p50 request-latency delta  (mock: a shape proxy, not a real TTFB)
  * task pass-rate delta       (must stay >= -1% — the correctness floor, I3)

Plus the accuracy pair S23 blocks on: **recall** of the files the answer depends on,
and **precision** (how much of what survives was actually needed). A slot that hit
tokens by evicting needed evidence fails the recall column, which is the whole point:
a cheaper prompt that lost the file is not a contribution, so the compiled arm is
labelled BETTER only when recall holds and precision improves; otherwise the report
says cheaper-but-not-better.

The graph the compiled arm reads is built from `_materialise_repo`, which seeds a
mock tree from the task file's own metadata — so the accuracy columns measure the
*mechanism* (does one joint budget keep what the index ranks first), not recall on a
human repo. Both arms get the same index, so the delta is joint budgeting alone.

This is the M3 skeleton running against `fakeup`; wiring it to a real
SWE-bench-style suite + grader is the only thing between this and the hero
number being a measurement rather than a mock-validated method.
"""

import argparse
import json
import os
import random
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subproto import demo, graph as graph_mod, protocol
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import ProxyServer, analyze_request
from subproto.telemetry import Telemetry

ENFORCE = "tool_gate,compact,context"
ARMS = ("observe", "enforce", "compiled")
# (SUBPROTO_APPLY, SUBPROTO_COMPILE) per arm. The compiled arm enforces the same
# three slots, so the only difference is *who* spends the budget.
ARM_ENV = {"observe": ("", ""), "enforce": (ENFORCE, ""), "compiled": (ENFORCE, "on")}


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
    first so the tail-protected relevant files are the ones that must survive).

    A `"big": true` context entry is the hard case: a needed 2.6k-token read that is
    the *oldest* thing in the session, with two filler turns appended after it — so
    neither recency nor tail protection can be what saves it. Without that flag the
    request is the one the earlier measurements were made on."""
    rng = random.Random(zlib.crc32(task["id"].encode()) & 0xFFFFFFFF)
    ctx = sorted(task["context"],
                 key=lambda c: 0 if c.get("big") else (1 if c.get("stale") else 2))
    hard = any(c.get("big") for c in ctx)
    messages = [{
        "role": "user",
        "content": task["task"] + " Use the files already read this session.",
    }]
    for i, c in enumerate(ctx):
        big = c.get("stale") or c.get("big")
        content = "@@dump %s@@\n" % c["path"] + _filler(rng, c["path"], 2600 if big else 60)
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_%d_%d" % (iters, i), "name": "Read",
             "input": {"file_path": c["path"]}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_%d_%d" % (iters, i),
             "content": content}]})
    if hard:
        for j in range(2):
            p = "app/misc/scrollpad_%d.py" % j
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_%d_pad%d" % (iters, j), "name": "Read",
                 "input": {"file_path": p}}]})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_%d_pad%d" % (iters, j),
                 "content": "@@dump %s@@\n" % p + _filler(rng, p, 40)}]})
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
    """Content-based `files_present`: the file's *read* must still be in the prompt.

    An earlier version substring-tested the whole body, which the `tool_use` blocks
    satisfy even after their `tool_result` was evicted — a floor that could not fail.
    """
    if task.get("grader") != "files_present":
        raise ValueError("unknown grader: %s" % task.get("grader"))
    surv = surviving_dumps(body.get("_mock_echo") or body)
    return int(all(p in surv for p in task["expected_files"]))


def _context_paths(task):
    return [c["path"] for c in task["context"]]


DUMP_MARKER = re.compile(r"@@dump (\S+)@@")


def surviving_dumps(body):
    """path -> tokens, counted on the *tool_result content* the upstream received.

    Deliberately not a substring test on the whole body: a `tool_use` still names the
    file it read even after its result is evicted, so mentioning a path is not the
    same as carrying it. This is what makes the recall column mean something.
    """
    out = {}
    for m in body.get("messages") or []:
        blocks = m.get("content")
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            text = protocol.block_text(b)
            hit = DUMP_MARKER.search(text)
            if hit:
                out[hit.group(1)] = out.get(hit.group(1), 0) + protocol.approx_tokens(text)
    return out


def score_dumps(surv, totals, task):
    """recall = needed content kept; precision = needed share of what was kept."""
    expected = [p for p in task["expected_files"] if p in totals]
    need = sum(totals[p] for p in expected)
    kept_need = sum(surv.get(p, 0) for p in expected)
    kept_all = sum(surv.values())
    return {"recall": kept_need / float(need) if need else 1.0,
            "precision": kept_need / float(kept_all) if kept_all else 1.0,
            "kept_tokens": kept_all}


def _materialise_repo(tasks, root):
    """A repo tree for the mock: every context path exists, and the files a task is
    *about* carry that task's text, so `graph.search` has something real to rank.
    Seeded from the task file's own metadata — see the module docstring."""
    content = {}
    for t in tasks:
        for p in _context_paths(t):
            stem = re.sub(r"\W+", "_", os.path.splitext(os.path.basename(p))[0]) or "x"
            hash_ = "//" if p.endswith((".js", ".jsx", ".ts", ".tsx")) else "#"
            lines = content.setdefault(p, [])
            if any(p == e for e in t["expected_files"]) and not lines:
                lines.append("%s %s\n" % (hash_, p))
            if any(p == e for e in t["expected_files"]):
                lines.append("%s %s\n" % (hash_, t["task"]))
            if p.endswith(".py"):
                safe = re.sub(r"\W+", "_", t["id"])[:8]
                lines.append("def %s_%s():\n    return %r\n" % (stem, safe, p))
    for rel, lines in sorted(content.items()):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("".join(lines) or "# %s\n" % rel)
    return len(content)


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
    """Return per-task {arm: {in_tok, latency_ms, recall, precision, pass}}.

    The graph is loaded once for the whole run and shared by both enforced arms: the
    per-slot `context` slot reads it too, so giving `compiled` the index and `enforce`
    nothing would credit the compiler for information its rival never had.
    """
    home = tempfile.mkdtemp(prefix="subproto-live-")
    repo = os.path.join(home, "repo")
    os.makedirs(repo)
    _materialise_repo(tasks, repo)
    gpath = graph_mod.save(graph_mod.build(repo), os.path.join(home, "graph.json"))
    cfg = Config.load(data_dir=home, port=free_port(), store_bodies=False)
    cfg.ensure_dirs()
    tel = Telemetry(cfg.db_path)
    srv = ProxyServer(("127.0.0.1", cfg.port), cfg, tel, Engine(cfg, graph_path=gpath))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import fakeup.server as mock
    mock_up = mock.MockUpstream(port=free_port()).start()
    cfg.source = dict(cfg.source, anthropic_upstream="http://127.0.0.1:%d" % mock_up.port)
    base = "http://127.0.0.1:%d" % cfg.port

    results = []
    prev = {k: os.environ.get(k) for k in ("SUBPROTO_APPLY", "SUBPROTO_COMPILE")}
    try:
        for task in tasks:
            row = {"id": task["id"]}
            for arm in ARMS:
                apply_val, compile_val = ARM_ENV[arm]
                os.environ["SUBPROTO_APPLY"] = apply_val
                os.environ["SUBPROTO_COMPILE"] = compile_val
                toks, lats, passes, accs = [], [], [], []
                for it in range(iters):
                    body = build_request(task, it)
                    totals = surviving_dumps(body)
                    resp, lat = _post(base, "/v1/messages", body)
                    echo = resp.get("_mock_echo") or {}
                    toks.append(analyze_request(echo)["est_in_tok"])
                    lats.append(lat)
                    passes.append(grade(resp, task))
                    accs.append(score_dumps(surviving_dumps(echo), totals, task))
                row[arm] = {
                    "in_tok": sum(toks) / len(toks),
                    "latency_ms": sorted(lats)[len(lats) // 2],
                    "pass": 1 if all(passes) else 0,
                    "recall": sum(a["recall"] for a in accs) / len(accs),
                    "precision": sum(a["precision"] for a in accs) / len(accs),
                    "kept_dump_tok": sum(a["kept_tokens"] for a in accs) / len(accs),
                }
            results.append(row)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        srv.shutdown()
        srv.server_close()
        mock_up.stop()
        tel.close()
    return results


def _bootstrap_ci(vals, stat, iters, rng):
    boots = [stat([rng.choice(vals) for _ in vals]) for _ in range(iters)]
    boots.sort()
    return boots[int(0.025 * iters)], boots[int(0.975 * iters)]


def _mean(seq):
    return sum(seq) / float(len(seq)) if seq else 0.0


def _pct(seq):
    return 100.0 * _mean(seq)


def compiled_vs_enforce(results, bootstrap, seed):
    """S23's blocking comparison: same slots, same index, one budget vs three.

    The accuracy half decides the label. Token savings without a recall hold is a
    regression dressed as a result, so `verdict` names the condition that failed.
    """
    rng = random.Random(seed)
    e_tok = [r["enforce"]["in_tok"] for r in results]
    c_tok = [r["compiled"]["in_tok"] for r in results]
    e_lat = [r["enforce"]["latency_ms"] for r in results]
    c_lat = [r["compiled"]["latency_ms"] for r in results]
    e_pass = [r["enforce"]["pass"] for r in results]
    c_pass = [r["compiled"]["pass"] for r in results]
    e_rec = [r["enforce"]["recall"] for r in results]
    c_rec = [r["compiled"]["recall"] for r in results]
    e_pre = [r["enforce"]["precision"] for r in results]
    c_pre = [r["compiled"]["precision"] for r in results]

    def _red(seq):
        e = sum(x[0] for x in seq)
        c = sum(x[1] for x in seq)
        return 100.0 * (e - c) / e if e else 0.0

    def _delta(seq):
        return _pct([c - e for e, c in seq])

    tok = [(e, c) for e, c in zip(e_tok, c_tok)]
    rec = [(e, c) for e, c in zip(e_rec, c_rec)]
    pre = [(e, c) for e, c in zip(e_pre, c_pre)]
    pd = [(e, c) for e, c in zip(e_pass, c_pass)]
    out = {
        "input_tok": {"enforce_mean": _mean(e_tok), "compiled_mean": _mean(c_tok),
                      "reduction_pct": _pct([(e - c) / e if e else 0.0 for e, c in tok]),
                      "reduction_ci": _bootstrap_ci(tok, _red, bootstrap, rng)},
        "latency": {"p50_enforce_ms": sorted(e_lat)[len(e_lat) // 2],
                    "p50_compiled_ms": sorted(c_lat)[len(c_lat) // 2],
                    "reduction_ci": _bootstrap_ci(list(zip(e_lat, c_lat)), _red, bootstrap, rng)},
        "pass_rate": {"enforce": _pct(e_pass), "compiled": _pct(c_pass),
                      "delta_ci": _bootstrap_ci(pd, _delta, bootstrap, rng)},
        "recall": {"enforce_pct": _pct(e_rec), "compiled_pct": _pct(c_rec),
                   "delta_ci": _bootstrap_ci(rec, _delta, bootstrap, rng)},
        "precision": {"enforce_pct": _pct(e_pre), "compiled_pct": _pct(c_pre),
                      "delta_ci": _bootstrap_ci(pre, _delta, bootstrap, rng)},
    }
    fails = []
    if out["recall"]["compiled_pct"] < 100.0:
        fails.append("recall of required files is %.1f%%, not 100%%"
                     % out["recall"]["compiled_pct"])
    if out["recall"]["compiled_pct"] < out["recall"]["enforce_pct"]:
        fails.append("recall is below the per-slot arm")
    if out["precision"]["compiled_pct"] <= out["precision"]["enforce_pct"]:
        fails.append("precision did not improve (%.1f%% vs %.1f%%)"
                     % (out["precision"]["compiled_pct"], out["precision"]["enforce_pct"]))
    if _pct([c - e for e, c in pd]) < -1.0:
        fails.append("task pass-rate regressed")
    out["verdict"] = "BETTER" if not fails else "CHEAPER-BUT-NOT-BETTER"
    out["verdict_reason"] = "; ".join(fails) or (
        "recall held at 100%% and precision improved by %.1f pp"
        % (out["precision"]["compiled_pct"] - out["precision"]["enforce_pct"]))
    return out


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
    m = {
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
             "compiled_tok": r["compiled"]["in_tok"],
             "observe_pass": r["observe"]["pass"], "enforce_pass": r["enforce"]["pass"],
             "compiled_pass": r["compiled"]["pass"],
             "enforce_recall": r["enforce"]["recall"],
             "compiled_recall": r["compiled"]["recall"],
             "enforce_precision": r["enforce"]["precision"],
             "compiled_precision": r["compiled"]["precision"]}
            for r in results],
    }
    if all("compiled" in r for r in results):
        m["compiled_vs_enforce"] = compiled_vs_enforce(results, bootstrap, seed)
    return m


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
    cv = m.get("compiled_vs_enforce")
    lines.append("**Correctness floor (I3), per-slot arm: pass-rate delta %.1f pp — %s "
                 "(gate: >= -1.0pp).**"
                 % (pr["delta_pct"], "HELD" if held else "VIOLATED"))
    if cv:
        c_held = cv["pass_rate"]["compiled"] - pr["observe"] >= -1.0
        lines.append("**Correctness floor (I3), compiled arm: %.1f%% vs %.1f%% observed — %s.**"
                     " The exit code follows the arm that would ship."
                     % (cv["pass_rate"]["compiled"], pr["observe"],
                        "HELD" if c_held else "VIOLATED"))
    lines.append("")
    if cv:
        lines.append("## v5 S23: one joint budget vs three per-slot budgets")
        lines.append("")
        lines.append("Same three slots, same code graph, same tasks. `compiled` spends one")
        lines.append("budget over messages + tool specs + file notes; `enforce` spends one per slot.")
        lines.append("")
        lines.append("| metric | per-slot | compiled | delta | 95% CI |")
        lines.append("|---|--:|--:|--:|:--|")
        ct = cv["input_tok"]
        lines.append("| input tokens (mean/task) | %.0f | %.0f | **%.1f%%** | [%.1f, %.1f] |"
                     % (ct["enforce_mean"], ct["compiled_mean"], ct["reduction_pct"],
                        ct["reduction_ci"][0], ct["reduction_ci"][1]))
        cl = cv["latency"]
        lines.append("| p50 request latency (ms) | %.1f | %.1f | — | [%.1f, %.1f] |"
                     % (cl["p50_enforce_ms"], cl["p50_compiled_ms"],
                        cl["reduction_ci"][0], cl["reduction_ci"][1]))
        cp = cv["pass_rate"]
        lines.append("| task pass-rate | %.1f%% | %.1f%% | — | [%+.1f, %+.1f] pp |"
                     % (cp["enforce"], cp["compiled"], cp["delta_ci"][0], cp["delta_ci"][1]))
        cr = cv["recall"]
        lines.append("| **recall of required files** | %.1f%% | %.1f%% | %+.1f pp | [%+.1f, %+.1f] pp |"
                     % (cr["enforce_pct"], cr["compiled_pct"],
                        cr["compiled_pct"] - cr["enforce_pct"],
                        cr["delta_ci"][0], cr["delta_ci"][1]))
        cq = cv["precision"]
        lines.append("| **precision of what survived** | %.1f%% | %.1f%% | %+.1f pp | [%+.1f, %+.1f] pp |"
                     % (cq["enforce_pct"], cq["compiled_pct"],
                        cq["compiled_pct"] - cq["enforce_pct"],
                        cq["delta_ci"][0], cq["delta_ci"][1]))
        lines.append("")
        lines.append("**Verdict (S23 gate: recall holds at 100%% *and* precision improves): "
                     "%s** — %s" % (cv["verdict"], cv["verdict_reason"]))
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
    ap.add_argument("--require-better", action="store_true",
                    help="exit 3 unless the compiled arm clears the S23 accuracy gate "
                         "(recall held at 100% and precision improved). Off by default: "
                         "a cheaper-but-not-better run is a result, not a crash.")
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
    cv = m.get("compiled_vs_enforce") or {}
    # The floor is checked on the arm that would ship. With the compiler on, a
    # per-slot regression is printed above but is not this process's failure; without
    # a compiled arm, the per-slot delta is the shipping number as before.
    if cv:
        ship_delta = cv["pass_rate"]["compiled"] - m["pass_rate"]["observe"]
    else:
        ship_delta = m["pass_rate"]["delta_pct"]
    if ship_delta < -1.0:
        return 2
    if args.require_better and cv.get("verdict") != "BETTER":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
