#!/usr/bin/env python3
"""laya-vs-heuristic slot ablation on a labelled synthetic corpus (SPEC §6, M2).

`tool_gate` has two candidate backends: the lexical heuristic and the scoring
server behind the laya adapter. This measures both against a small hand-labelled
ground-truth set of (turn, tools, tools-a-human-would-keep) so the *precision
delta* is visible before any real fine-tuned model arrives. The same numbers feed
the report's "slot precision" line.

Honesty note (invariant I6): the ground truth here is synthetic and authored by
us, so this validates the measurement pipeline and the two estimators' behaviour,
not laya's real-world accuracy. The labelled-by-users corpus (subproto label)
replaces it at M4.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subproto import heuristics
from subproto.laya_server import lexical_probabilities
from subproto.report import precision_metrics

# (turn text, candidate tools, gold keep set). Core file/shell tools are kept by
# the heuristic regardless; mcp__* tools test topical relevance.
CORE = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]

GROUND_TRUTH = [
    {"query": "read app/config.py and run the failing tests",
     "tools": CORE + ["mcp__slack__post", "mcp__figma__read", "mcp__jira__comment"],
     "keep": CORE},
    {"query": "post the nightly build status to the #release slack channel",
     "tools": CORE + ["mcp__slack__post", "mcp__github__pr", "mcp__sentry__list"],
     "keep": CORE + ["mcp__slack__post"]},
    {"query": "open a pull request and fetch review comments",
     "tools": CORE + ["mcp__github__pr", "mcp__github__review", "mcp__linear__create"],
     "keep": CORE + ["mcp__github__pr", "mcp__github__review"]},
    {"query": "screenshot the checkout page and click the pay button",
     "tools": CORE + ["mcp__browser__screenshot", "mcp__browser__click", "mcp__db__query"],
     "keep": CORE + ["mcp__browser__screenshot", "mcp__browser__click"]},
    {"query": "query the orders table for pending rows and print the schema",
     "tools": CORE + ["mcp__db__query", "mcp__db__schema", "mcp__jira__issue"],
     "keep": CORE + ["mcp__db__query", "mcp__db__schema"]},
    {"query": "refactor the retry loop, keep interfaces stable, add tests",
     "tools": CORE + ["mcp__slack__post", "mcp__figma__read"],
     "keep": CORE},
    {"query": "create a linear issue for the tracked regression",
     "tools": CORE + ["mcp__linear__create", "mcp__webfetch"],
     "keep": CORE + ["mcp__linear__create"]},
    {"query": "list recent sentry errors for the release",
     "tools": CORE + ["mcp__sentry__list", "mcp__notion__page"],
     "keep": CORE + ["mcp__sentry__list"]},
    {"query": "search the codebase for the deprecated helper and remove it",
     "tools": CORE + ["mcp__jira__comment", "mcp__calendar__book"],
     "keep": CORE},
    {"query": "navigate the browser to staging and fill the login form",
     "tools": CORE + ["mcp__browser__navigate", "mcp__browser__fill", "mcp__db__query"],
     "keep": CORE + ["mcp__browser__navigate", "mcp__browser__fill"]},
]


def _as_specs(names):
    # heuristics.tool_gate reads description/params; keep them empty so relevance
    # is decided by the tool name + query, matching what the laya scorer sees.
    return [{"name": n, "description": ""} for n in names]


def heuristic_keeps(query, names):
    decs = heuristics.tool_gate(_as_specs(names), query)
    # tool_gate already returns target names lowercased
    return set(d["target"] for d in decs if d["keep"])


def laya_keeps(query, names, threshold=0.5):
    # lexical_probabilities keys output by the exact option strings passed in,
    # so lowercase here to line up with heuristic_keeps' lowercased targets.
    probs = lexical_probabilities(query, "keep", names)
    return set(n.lower() for n in names if probs.get(n, 0.0) >= threshold)


def run_ablation(threshold=0.5):
    per_case = []
    agree_n = total_n = 0
    heur_tp = heur_fp = heur_fn = 0
    laya_tp = laya_fp = laya_fn = 0
    for case in GROUND_TRUTH:
        gold = set(c.lower() for c in case["keep"])
        names = case["tools"]
        lower_names = [n.lower() for n in names]
        hk = heuristic_keeps(case["query"], names)
        lk = laya_keeps(case["query"], names, threshold)
        for n in lower_names:
            total_n += 1
            if (n in hk) == (n in lk):
                agree_n += 1
        hm = precision_metrics(gold, hk)
        lm = precision_metrics(gold, lk)
        heur_tp += hm["tp"]; heur_fp += hm["fp"]; heur_fn += hm["fn"]
        laya_tp += lm["tp"]; laya_fp += lm["fp"]; laya_fn += lm["fn"]
        per_case.append({"query": case["query"][:48], "gold": sorted(gold),
                         "heuristic": sorted(hk), "laya": sorted(lk)})

    def _agg(tp, fp, fn):
        p = tp / float(tp + fp) if (tp + fp) else 1.0
        r = tp / float(tp + fn) if (tp + fn) else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}

    heuristic = _agg(heur_tp, heur_fp, heur_fn)
    laya = _agg(laya_tp, laya_fp, laya_fn)
    return {
        "n_cases": len(GROUND_TRUTH),
        "agreement": round(agree_n / float(total_n), 4),
        "heuristic": heuristic,
        "laya": laya,
        "precision_delta_laya_minus_heuristic": round(laya["precision"] - heuristic["precision"], 4),
        "per_case": per_case,
    }


def render(m):
    lines = ["# subproto slot ablation — tool_gate (labelled synthetic)\n"]
    lines.append("Cases: %d · agreement(heuristic vs laya): %.0f%%\n"
                 % (m["n_cases"], 100 * m["agreement"]))
    lines.append("| backend | precision | recall | f1 |")
    lines.append("|---|--:|--:|--:|")
    for k in ("heuristic", "laya"):
        b = m[k]
        lines.append("| %s | %.3f | %.3f | %.3f |" % (k, b["precision"], b["recall"], b["f1"]))
    lines.append("\nlaya − heuristic precision delta: **%+.3f**"
                 % m["precision_delta_laya_minus_heuristic"])
    lines.append("\n*Note: `laya` here is the deterministic lexical scorer behind the "
                 "adapter interface, so it shares the heuristic's core-tool logic and the "
                 "two agree closely on this lexical ground truth — that agreement is "
                 "expected, not a claim of parity with a real model. The delta becomes "
                 "meaningful when the quantized MLX checkpoint (T16/M4) replaces the "
                 "lexical scorer; this harness is the machinery that will report it. "
                 "Ground truth is author-labelled and replaced by the `subproto label` "
                 "corpus at M4 (SPEC §1.2).*")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=os.path.join(here, "ablation_results.md"))
    args = ap.parse_args()
    m = run_ablation()
    if args.json:
        print(json.dumps(m, indent=1))
    else:
        text = render(m)
        print(text)
        with open(args.out, "w") as f:
            f.write(text + "\n")
        with open(os.path.join(here, "ablation.json"), "w") as f:
            json.dump(m, f, indent=1)
        print("\n-> wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
