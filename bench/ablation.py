#!/usr/bin/env python3
"""Adapter-vs-heuristic slot ablation on a labelled synthetic corpus (SPEC §6, M2).

`tool_gate` can be answered by the in-process heuristic or by any System One
adapter in the registry, so this measures each one against a small hand-labelled
ground-truth set of (turn, tools, tools-a-human-would-keep) and reports the
*precision delta per adapter*. The same numbers feed the report's
"slot precision" line.

Every adapter column is scored through the `ModelAdapter` interface (a real
`POST /score`), never by calling a scorer directly — so what this harness
measures is the thing the engine actually uses, and dropping a quantized
checkpoint behind one of these labels changes only the number, not this file.

Honesty note (invariant I6): the ground truth here is synthetic and authored by
us, so this validates the measurement pipeline and the estimators' behaviour, not
any model's real-world accuracy. An adapter with no configured URL is reported as
skipped rather than silently falling back to the stand-in. The labelled-by-users
corpus (subproto label) replaces the ground truth at M4.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subproto import heuristics, systemone
from subproto.config import Config
from subproto.laya_server import LayaServer
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
    # is decided by the tool name + query, matching what an adapter is asked.
    return [{"name": n, "description": ""} for n in names]


def heuristic_keeps(query, names):
    decs = heuristics.tool_gate(_as_specs(names), query)
    # tool_gate already returns target names lowercased
    return set(d["target"] for d in decs if d["keep"])


def adapter_keeps(adapter, query, names, threshold=0.5):
    """Keep-set from a real ModelAdapter call, comparable to heuristic_keeps.

    The scoring server keys its vector by the option strings it received, so we
    lowercase to line up with heuristic_keeps' lowercased targets.
    """
    probs = adapter.score(query, "keep", list(names)) or {}
    return set(n.lower() for n in names if probs.get(n, probs.get(n.lower(), 0.0)) >= threshold)


def _agg(tp, fp, fn):
    p = tp / float(tp + fp) if (tp + fp) else 1.0
    r = tp / float(tp + fn) if (tp + fn) else 1.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def build_adapters(labels, config=None):
    """Resolve each requested label to a live adapter, or why it was skipped.

    `laya` with no `LAYA_URL` gets the bundled lexical stand-in (that is what the
    committed evidence was measured against); every other label must point at a
    real endpoint, because inventing a stand-in for a model we do not have would
    put a fake number in a comparison table.
    """
    config = config or Config(source={})
    out, skipped = [], []
    stand_in = None
    for label in labels:
        key = label.lower()
        if key not in systemone.REGISTRY:
            skipped.append({"name": label, "why": "not a registry adapter"})
            continue
        url = systemone.configured_url(key, config)
        source = "configured"
        if not url and key == "laya":
            if stand_in is None:
                srv = LayaServer(port=0, backend="lexical").start()
                stand_in = (srv, "http://127.0.0.1:%d" % srv.server_address[1])
            url, source = stand_in[1], "bundled lexical stand-in"
        if not url:
            skipped.append({"name": label,
                            "why": "no endpoint — export %s=http://host:port"
                                   % systemone.REGISTRY[key]["env"]})
            continue
        out.append({"label": key,
                    "adapter": systemone.HTTPScoreAdapter(url, label=key),
                    "url": url, "source": source})
    return out, skipped, (stand_in[0] if stand_in else None)


def run_ablation(labels=("laya",), threshold=0.5):
    adapters, skipped, _stand_in = build_adapters(list(labels))
    per_case = []
    agree = dict((a["label"], 0) for a in adapters)
    scored = dict((a["label"], 0) for a in adapters)
    tpfpfn = dict((a["label"], [0, 0, 0]) for a in adapters)
    heur = [0, 0, 0]
    for case in GROUND_TRUTH:
        gold = set(c.lower() for c in case["keep"])
        names = case["tools"]
        hk = heuristic_keeps(case["query"], names)
        hm = precision_metrics(gold, hk)
        heur[0] += hm["tp"]; heur[1] += hm["fp"]; heur[2] += hm["fn"]
        row = {"query": case["query"][:48], "gold": sorted(gold), "heuristic": sorted(hk)}
        for entry in adapters:
            label = entry["label"]
            ak = adapter_keeps(entry["adapter"], case["query"], names, threshold)
            am = precision_metrics(gold, ak)
            tpfpfn[label][0] += am["tp"]; tpfpfn[label][1] += am["fp"]; tpfpfn[label][2] += am["fn"]
            for n in (x.lower() for x in names):
                scored[label] += 1
                if (n in hk) == (n in ak):
                    agree[label] += 1
            row[label] = sorted(ak)
        per_case.append(row)

    heuristic = _agg(*heur)
    result = {
        "n_cases": len(GROUND_TRUTH),
        "heuristic": heuristic,
        # No URLs here on purpose: the committed artifact must not churn when a
        # stand-in's ephemeral port or someone's host changes (I6 hygiene).
        "adapters": [dict({"name": e["label"], "source": e["source"],
                           "agreement": round(agree[e["label"]] / float(scored[e["label"]]), 4)
                                         if scored[e["label"]] else None,
                           "delta_vs_heuristic": round(
                               _agg(*tpfpfn[e["label"]])["precision"]
                               - heuristic["precision"], 4)},
                          **_agg(*tpfpfn[e["label"]])) for e in adapters],
        "skipped": skipped,
        "per_case": per_case,
    }
    if _stand_in is not None:
        _stand_in.stop()
    return result


def render(m):
    lines = ["# subproto slot ablation — tool_gate (labelled synthetic)\n"]
    lines.append("Cases: %d · agreement with the heuristic, per adapter:\n" % m["n_cases"])
    for a in m["adapters"]:
        lines.append("- `%s`: %.0f%%" % (a["name"], 100 * a["agreement"]))
    lines.append("\n| backend | precision | recall | f1 | Δprecision vs heuristic |")
    lines.append("|---|--:|--:|--:|--:|")
    h = m["heuristic"]
    lines.append("| heuristic | %.3f | %.3f | %.3f | — |"
                 % (h["precision"], h["recall"], h["f1"]))
    for a in m["adapters"]:
        lines.append("| %s | %.3f | %.3f | %.3f | %+.3f |"
                     % (a["name"], a["precision"], a["recall"], a["f1"],
                        a["delta_vs_heuristic"]))
    for a in m["adapters"]:
        lines.append("\n%s − heuristic precision delta: **%+.3f**"
                     % (a["name"], a["delta_vs_heuristic"]))
    if m["skipped"]:
        lines.append("\nSkipped (no endpoint, so no number was invented for them):")
        for s in m["skipped"]:
            lines.append("- `%s` — %s" % (s["name"], s["why"]))
    lines.append("\n*Note: every adapter column is a real `POST /score` through "
                 "`subproto.systemone.HTTPScoreAdapter`. Today the only endpoint in "
                 "the repo is the bundled deterministic lexical stand-in "
                 "(`python -m subproto.laya_server`), which shares the heuristic's "
                 "core-tool logic, so close agreement and a ~zero delta are the "
                 "expected reading — that is a property of the stand-in, not a claim "
                 "about any real model. Point a registry label at a quantized "
                 "checkpoint (T16/M4) and this table measures it unchanged. Ground "
                 "truth is author-labelled and replaced by the `subproto label` "
                 "corpus at M4 (SPEC §1.2).*")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--adapters", default="laya",
                    help="comma-separated registry labels to score against the "
                         "heuristic (laya, openjev, djev, semif, mlx_lora)")
    ap.add_argument("--out", default=os.path.join(here, "ablation_results.md"))
    ap.add_argument("--write", action="store_true",
                    help="refresh the committed results files (default: print only)")
    args = ap.parse_args()
    m = run_ablation(labels=[x.strip() for x in args.adapters.split(",") if x.strip()])
    text = render(m)
    print(text)
    if args.json:
        print(json.dumps(m, indent=1))
    if args.write:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        with open(os.path.join(here, "ablation.json"), "w") as f:
            json.dump(m, f, indent=1)
        print("\n-> wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
