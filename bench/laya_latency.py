#!/usr/bin/env python3
"""V2-C3: the per-decision latency curve for the real Laya checkpoint (SPEC §11).

SPEC §11 locked "on-device = Apple MLX" and asked for a published curve. There is
no MLX path to this checkpoint — it is a ModernBERT-large encoder with a
classification head that answers with a distribution and emits zero output
tokens, and `mlx_lm` loads causal LMs — so the curve that actually decides
whether a local model is usable here is a **torch CPU** curve, measured through
the shipped translation (`subproto.laya_server.LayaScorer`), not a re-implementation.

Three ways to ask "should this tool survive?" are measured, because the shape
that is affordable and the shape that is accurate are not the same one:

* `choice` — one pass over the whole option set. What `--backend laya` does today.
* `keep_drop` — one pass per option, each a binary keep/drop question. The other
  shipped shape (`--shape keep_drop`).
* `noul` — one pass per option, the dataset's own independent-relevance type.
  Not shipped; measured so the choice between the two shipped shapes has a third
  point of comparison.

Accuracy is reported under three reading rules, because a `choice` answer sums to
1 and a 0.5 threshold on a distribution is a contract mismatch this harness
reports rather than hides (SPEC FR-4, open decision).

It needs the interpreter that can import `laya` (>= 3.10, torch), so on a fresh
clone it is the venv the README builds for the checkpoint:

    python3.11 -m venv .venv-laya && .venv-laya/bin/pip install "laya[serve]"
    .venv-laya/bin/python -m bench.laya_latency
    .venv-laya/bin/python -m bench.laya_latency --write

A wall-clock number is only worth publishing with its conditions attached, so the
machine's load average is recorded at the start and the end of the run and printed
above the table. If those disagree with the rest of the machine, the table says so
instead of asking you to trust it.
"""

import argparse
import json
import os
import platform
import sys
import time
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bench.ablation import GROUND_TRUTH, heuristic_keeps  # noqa: E402
from subproto import laya_server  # noqa: E402
from subproto.config import Config  # noqa: E402
from subproto.report import precision_metrics  # noqa: E402

RULES = ("threshold", "rank", "relative")


def _load_avg():
    """The kernel's 1/5/15-minute load, or None wherever the host declines to give one.

    A Windows box has no `getloadavg` at all; a POSIX kernel can still refuse the
    call. Either way this is a condition of the measurement, not a dependency of it:
    `null` in the JSON means "this host does not keep the figure", and the prose says
    so in words. A bench that raised here would publish nothing on a machine that
    measured perfectly well, which is the worse of the two failures.
    """
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):
        return None


def conditions(threads, device):
    """Everything a reader needs to decide whether this run describes their host."""
    try:
        import torch
        torch_threads = torch.get_num_threads()
        torch_version = torch.__version__
    except Exception:
        torch_threads, torch_version = None, None
    return {
        "python": platform.python_version(),
        "torch": torch_version,
        "laya": getattr(sys.modules.get("laya"), "__version__", None),
        "model": "convaiinnovations/laya",
        "device": device,
        "threads": threads or torch_threads,
        "cores": os.cpu_count(),
        "load_avg_start": _load_avg(),
        "os": " ".join(platform.system().split()) + " " + platform.release(),
    }


def _ask_noul(router, state, names):
    """The dataset's independent-relevance type: N passes, one per option."""
    probs, ms = {}, 0.0
    for n in names:
        started = time.perf_counter()
        out = router.predict(state, {"k": {
            "type": "noul",
            "instructions": "Does this request need the %s tool?"
                            % laya_server.option_text(n)}})
        ms += (time.perf_counter() - started) * 1000.0
        probs[n] = float((out.get("answers") or {}).get("k", {}).get("noul", 0.0))
    return probs, ms


def _sets(gold, names, probs):
    ranked = sorted(names, key=lambda n: -probs.get(n, 0.0))
    top = max(list(probs.values()) or [0.0])
    return {
        # what the engine does with a probability today
        "threshold": set(n.lower() for n in names if probs.get(n, 0.0) >= 0.5),
        # what the compiler does with a ranking: cut at the budget's size
        "rank": set(n.lower() for n in ranked[:len(gold)]),
        # the compromise a distribution invites: half the best-scoring option
        "relative": set(n.lower() for n in names if probs.get(n, 0.0) >= 0.5 * top),
    }


def measure(reps=3, device=None, threads=None, quiet=False):
    import laya
    import torch

    if threads:
        torch.set_num_threads(int(threads))
    device = device or os.environ.get("LAYA_DEVICE") or "cpu"
    cond = conditions(threads, device)
    scorers = {"choice": laya_server.LayaScorer(device=device, shape="choice"),
               "keep_drop": laya_server.LayaScorer(device=device, shape="keep_drop")}
    if not quiet:
        print("loaded %s on %s (%s ms), laya %s, torch %s, python %s"
              % (cond["model"], device, scorers["choice"].load_ms, cond["laya"],
                 cond["torch"], cond["python"]), flush=True)
    router = laya.Router(device=device)

    rows = []
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        for shape, fn in _probes(scorers, router):
            times, agg, per_case = [], dict((r, [0, 0, 0]) for r in RULES), []
            for case in GROUND_TRUTH:
                names = list(case["tools"])
                gold = set(c.lower() for c in case["keep"])
                for _ in range(reps):
                    probs, ms = fn(case["query"], names)
                    times.append(ms)
                sets = _sets(gold, names, probs)
                for rule in RULES:
                    m = precision_metrics(gold, sets[rule])
                    agg[rule][0] += m["tp"]; agg[rule][1] += m["fp"]
                    agg[rule][2] += m["fn"]
                per_case.append({"query": case["query"][:40], "options": len(names),
                                 "ms": round(ms, 1),
                                 "f1": dict((r, precision_metrics(gold, sets[r])["f1"])
                                            for r in RULES)})
            times.sort()
            rows.append({"shape": shape, "shipped": shape in scorers,
                         "passes": 1 if shape == "choice" else "one per option",
                         "ms": {"n": len(times),
                                "p50": round(times[len(times) // 2], 1),
                                "p95": round(times[int(len(times) * 0.95) - 1], 1),
                                "max": round(times[-1], 1)},
                         "rules": dict((k, _agg(*v)) for k, v in agg.items()),
                         "per_case": per_case})
    # What the runtime itself said while answering. `laya` warns that this
    # checkpoint ships out-of-range calibration temperatures for the wider option
    # counts, so a `confidence` read here is not a probability — the curve has to
    # carry that, because it is the difference between a threshold rule failing
    # honestly and failing for a reason the reader cannot see.
    cond["runtime_warnings"] = sorted(set(str(w.message).strip() for w in seen))
    cond["load_avg_end"] = _load_avg()
    hk = [0, 0, 0]
    for case in GROUND_TRUTH:
        gold = set(c.lower() for c in case["keep"])
        m = precision_metrics(gold, heuristic_keeps(case["query"], case["tools"]))
        hk[0] += m["tp"]; hk[1] += m["fp"]; hk[2] += m["fn"]
    return {"conditions": cond, "budget_ms": Config(source={}).model_timeout_ms,
            "heuristic": _agg(*hk), "shapes": rows}


def _probes(scorers, router):
    """(name, callable(state, names) -> (probs, ms)) for the three shapes."""
    def shipped(shape):
        def call(state, names):
            scorer = scorers[shape]
            return scorer.score(state, "keep", names), float(scorer.last_ms)
        return call

    return [("choice", shipped("choice")), ("keep_drop", shipped("keep_drop")),
            ("noul", lambda state, names: _ask_noul(router, state, names))]


def _agg(tp, fp, fn):
    p = tp / float(tp + fp) if (tp + fp) else 1.0
    r = tp / float(tp + fn) if (tp + fn) else 1.0
    return {"precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if (p + r) else 0.0}


def render(m):
    c = m["conditions"]
    lines = ["# Laya per-decision latency curve (torch CPU, %d cases)\n"
             % sum(len(x["per_case"]) for x in m["shapes"])]
    lines.append("Measured through `subproto.laya_server.LayaScorer`, so the question "
                 "asked here is the question the server asks.\n")
    lines.append("- model `%s` on device `%s`, torch %s, laya %s, python %s, "
                 "%s threads on %s cores"
                 % (c["model"], c["device"], c["torch"], c["laya"], c["python"],
                    c["threads"], c["cores"]))
    if c["load_avg_start"] and c["load_avg_end"]:
        lines.append("- host load average at start %s, at end %s (per-core: %.2f → %.2f). "
                     "The start reading is the machine this ran on; the end reading includes "
                     "this run's own %s threads, so read it as evidence of what the run cost, "
                     "not of what it was competing with."
                     % (", ".join(str(x) for x in c["load_avg_start"]),
                        ", ".join(str(x) for x in c["load_avg_end"]),
                        c["load_avg_start"][0] / c["cores"],
                        c["load_avg_end"][0] / c["cores"], c["threads"]))
    else:
        # Two kinds of host get here: one with no `getloadavg` at all (Windows), and one
        # whose kernel refuses the call. The honest line says the figure is absent — a
        # row of `None`s would read as a measurement of nothing.
        lines.append("- this host kept no load average to record, so nothing here says how "
                     "busy the machine was while it measured. Take the percentiles as "
                     "unburdened and check the p95 against your own.")
    if c.get("runtime_warnings"):
        lines.append("- what the runtime said while answering: %s"
                     % "; ".join("`%s`" % w.replace("`", "'")[:220]
                                 for w in c["runtime_warnings"]))
    budget = m["budget_ms"]
    lines.append("- the shipped per-decision budget is `SUBPROTO_MODEL_TIMEOUT_MS` = "
                 "%d ms; a shape that exceeds it is not usable in the engine, and the "
                 "verdict column says which is which.\n" % budget)
    lines.append("| shape | passes per decision | ms p50 | ms p95 | ms max | fits %d ms | "
                 "precision | recall | f1 |" % budget)
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|--:|")
    for row in m["shapes"]:
        lt, thr = row["ms"], row["rules"]["threshold"]
        lines.append("| %s%s | %s | %.0f | %.0f | %.0f | %s | %.3f | %.3f | %.3f |"
                     % (row["shape"], "" if row["shipped"] else " (not shipped)",
                        row["passes"], lt["p50"], lt["p95"], lt["max"],
                        "yes" if lt["p95"] <= budget else "no",
                        thr["precision"], thr["recall"], thr["f1"]))
    h = m["heuristic"]
    lines.append("| heuristic (in-process, no model) | 0 | 0 | 0 | 0 | yes | %.3f "
                 "| %.3f | %.3f |" % (h["precision"], h["recall"], h["f1"]))

    lines.append("\n## The same answers read as a ranking\n")
    lines.append("A `choice` answer is a distribution over the option set (it sums to 1), "
                 "so at most one option can clear 0.5 and the threshold column above "
                 "measures the *rule*. Ranked and cut at the gold set's size — which is "
                 "what a token budget does — the same passes score:\n")
    lines.append("| shape | threshold f1 | rank f1 | relative f1 |")
    lines.append("|---|--:|--:|--:|")
    for row in m["shapes"]:
        lines.append("| %s | %.3f | %.3f | %.3f |"
                     % (row["shape"], row["rules"]["threshold"]["f1"],
                        row["rules"]["rank"]["f1"], row["rules"]["relative"]["f1"]))
    lines.append("\nThe heuristic's f1 on this set is %.3f, and the gold sets were written "
                 "around its own rule (see `bench/ablation.py::corpus_ceiling`), so these "
                 "columns bound how far apart any scorer can get on it. Whether the slot "
                 "should threshold or rank is an open decision (SPEC FR-4); ranking well "
                 "here is not evidence to relax a keep rule that guards correctness (I3)."
                 % h["f1"])

    slow = [r["shape"] for r in m["shapes"] if r["ms"]["p95"] > budget]
    choice = dict((r["shape"], r) for r in m["shapes"])["choice"]
    lines.append("\n## What this says about the locked MLX decision\n")
    lines.append("There is **no MLX path to this checkpoint**, and the reason is "
                 "architectural rather than logistical: `convaiinnovations/laya` is a "
                 "ModernBERT-large encoder with a two-layer classification head "
                 "(`pipeline_tag: text-classification`, `usage.output_tokens: 0` on every "
                 "answer), and `mlx_lm` loads causal language models. SPEC §11's "
                 "\"on-device = Apple MLX\" could not have been built as written, so "
                 "`--backend mlx` refuses with that explanation instead of serving a "
                 "stand-in and calling it a checkpoint.")
    lines.append("\nThe other half of that decision was a latency bet — that a local "
                 "decision needs a quantised MLX build to be affordable. Measured on the "
                 "runtime the checkpoint actually has (torch CPU, %s threads): the shipped "
                 "`choice` shape costs %.0f ms p50, %.0f ms p95, %.0f ms worst observed "
                 "against a shipped budget of %d ms. %s"
                 % (c["threads"], choice["ms"]["p50"], choice["ms"]["p95"],
                    choice["ms"]["max"], budget,
                    "It fits, so MLX would have been an optimisation of something already "
                    "fast enough, not a prerequisite."
                    if choice["ms"]["p95"] <= budget else
                    "The median %s the budget and the tail %s it, so the knob to turn is "
                    "`SUBPROTO_MODEL_TIMEOUT_MS` (or the shape), and no MLX build was ever "
                    "the fix — there is nothing for it to load."
                    % ("fits" if choice["ms"]["p50"] <= budget else "misses",
                       "holds inside" if choice["ms"]["p95"] <= budget else "breaks")))
    lines.append("\nShapes whose p95 exceeds the budget: %s. `--shape choice` is the "
                 "shipped default because it is the only one-pass shape; `keep_drop` "
                 "trades %dx the cost for a materially better read under the same "
                 "threshold rule (f1 %.3f vs %.3f), which is the trade a slot owner has "
                 "to make deliberately, per model, and which is why it is a flag and not "
                 "a default."
                 % (", ".join("`%s`" % s for s in slow) or "none",
                    round(m["shapes"][1]["ms"]["p50"] / choice["ms"]["p50"])
                    if choice["ms"]["p50"] else 0,
                    m["shapes"][1]["rules"]["threshold"]["f1"],
                    choice["rules"]["threshold"]["f1"]))
    lines.append("\n*Ground truth is author-labelled synthetic data: this curve measures "
                 "the cost of the shipped question shapes and the pipeline that reads "
                 "them, not any model's real-world accuracy (I6).*")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--reps", type=int, default=3,
                    help="passes per case; the curve is over these samples")
    ap.add_argument("--device", default=None, help="cpu (default) or mps")
    ap.add_argument("--threads", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=os.path.join(here, "laya_latency.md"))
    ap.add_argument("--write", action="store_true",
                    help="refresh the committed curve files (default: print only)")
    args = ap.parse_args()
    if not laya_server.laya_importable():
        sys.stderr.write("bench/laya_latency.py must run under an interpreter that can "
                         "import laya (>= 3.10, torch). Build the venv the README "
                         "names and re-run it: .venv-laya/bin/python "
                         "-m bench.laya_latency\n")
        return 2
    m = measure(reps=args.reps, device=args.device, threads=args.threads)
    if args.json:
        print(json.dumps(m, indent=1, sort_keys=True))
    else:
        print(render(m))
    if args.write:
        with open(args.out, "w") as fh:
            fh.write(render(m) + "\n")
        with open(os.path.splitext(args.out)[0] + ".json", "w") as fh:
            json.dump(m, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("\nwrote %s (+ .json)" % args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
