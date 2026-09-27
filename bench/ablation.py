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


def adapter_keeps(adapter, query, names, threshold=0.5, rule="threshold", k=None,
                  probs=None):
    """Keep-set from a real ModelAdapter call, comparable to heuristic_keeps.

    The scoring server keys its vector by the option strings it received, so we
    lowercase to line up with heuristic_keeps' lowercased targets.

    Two read rules, because an uncalibrated distribution and a 0.5 threshold do
    not agree on what a number means. `threshold` is the rule the engine ships (keep the
    options at p >= 0.5); `rank` takes the top k by score, which is how the
    compiler already selects under a token budget. A `choice` answer from real
    Laya is a *distribution* over the options (it sums to 1), so under
    `threshold` only one option can clear 0.5 — reporting that alone would
    describe the rule, not the model, and both are printed for that reason.
    """
    if probs is None:
        probs = adapter.score(query, "keep", list(names)) or {}
    get = lambda n: float(probs.get(n, probs.get(str(n).lower(), 0.0)) or 0.0)
    if rule == "rank":
        ranked = sorted(names, key=lambda n: -get(n))
        return set(n.lower() for n in ranked[:max(0, int(k if k is not None else 0))])
    return set(n.lower() for n in names if get(n) >= threshold)


def corpus_ceiling():
    """How much room this corpus has to separate a scorer from the baseline.

    The gold sets were authored around the heuristic's own rule — keep the core
    file/shell tools, keep anything whose name tokens appear in the turn — so a
    case where the heuristic's answer *is* the answer key cannot move its
    precision either way. That makes the committed 0.972 a property of the set,
    not a measurement of skill, and it bounds any delta this harness can express
    to the few cases where the two disagree. Reporting the number without this
    line is how a rigged test becomes a headline.
    """
    identical, decided, wrong_keeps, missed = 0, 0, 0, 0
    differing = []
    for case in GROUND_TRUTH:
        gold = set(c.lower() for c in case["keep"])
        hk = heuristic_keeps(case["query"], case["tools"])
        decided += len(case["tools"])
        wrong_keeps += len(hk - gold)
        missed += len(gold - hk)
        if gold == hk:
            identical += 1
        else:
            differing.append({"query": case["query"][:44],
                              "only_in_gold": sorted(gold - hk),
                              "only_in_heuristic": sorted(hk - gold)})
    return {"cases": len(GROUND_TRUTH), "gold_equals_heuristic": identical,
            "decisions": decided, "baseline_false_keeps": wrong_keeps,
            "baseline_false_cuts": missed, "differing_cases": differing}


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
                    "adapter": systemone.HTTPScoreAdapter(
                        url, label=key, timeout=systemone.timeout_for(config)),
                    "url": url, "source": source})
    return out, skipped, (stand_in[0] if stand_in else None)


def run_ablation(labels=("laya",), threshold=0.5):
    adapters, skipped, _stand_in = build_adapters(list(labels))
    per_case = []
    agree = dict((a["label"], 0) for a in adapters)
    scored = dict((a["label"], 0) for a in adapters)
    rules = dict((a["label"], {"threshold": [0, 0, 0], "rank": [0, 0, 0]})
                 for a in adapters)
    latency = dict((a["label"], []) for a in adapters)
    no_answer = dict((a["label"], 0) for a in adapters)
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
            adapter = entry["adapter"]
            probs = adapter.score(case["query"], "keep", list(names))
            if getattr(adapter, "last_latency_ms", None) is not None:
                latency[label].append(adapter.last_latency_ms)
            if not probs and getattr(adapter, "last_error", None):
                # No answer is not "keep nothing". Scoring a starved or broken
                # call as an empty set hands the table a precision number that
                # describes the transport, and a `rank` read of an empty vector
                # quietly falls back to the order the tools were listed in —
                # which on this corpus looks like a good score.
                no_answer[label] += 1
                row[label] = "no answer: %s" % adapter.last_error
                continue
            probs = probs or {}
            scored[label] += len(names)
            keeps = {"threshold": adapter_keeps(adapter, case["query"], names,
                                               threshold, "threshold", probs=probs),
                     "rank": adapter_keeps(adapter, case["query"], names,
                                           threshold, "rank", k=len(gold), probs=probs)}
            for rule, ak in keeps.items():
                am = precision_metrics(gold, ak)
                acc = rules[label][rule]
                acc[0] += am["tp"]; acc[1] += am["fp"]; acc[2] += am["fn"]
            ak = keeps["threshold"]
            for n in (x.lower() for x in names):
                if (n in hk) == (n in ak):
                    agree[label] += 1
            row[label] = sorted(ak)
            row[label + "_ranked"] = sorted(keeps["rank"])
        per_case.append(row)

    heuristic = _agg(*heur)
    budget_ms = round(systemone.timeout_for(Config(source={})) * 1000)
    entries = []
    for e in adapters:
        label = e["label"]
        # A backend that answered nothing must not be handed a perfect score:
        # _agg() of 0/0/0 is 1.0 by its own convention, and that convention is
        # right for "kept none of none" and wrong for "never replied".
        by_rule = (dict((k, None) for k in rules[label])
                   if scored[label] == 0 else
                   dict((k, _agg(*v)) for k, v in rules[label].items()))
        answered = len(GROUND_TRUTH) - no_answer[label]
        entries.append(dict({"name": label, "source": e["source"],
                             "answered": "%d/%d" % (answered, len(GROUND_TRUTH)),
                             "budget_ms": budget_ms,
                             "agreement": round(agree[label] / float(scored[label]), 4)
                                          if scored[label] else None,
                             "delta_vs_heuristic": round(
                                 by_rule["threshold"]["precision"]
                                 - heuristic["precision"], 4) if scored[label] else None},
                            **(by_rule["threshold"] or {})))
        entries[-1]["rules"] = by_rule
        entries[-1]["latency_ms"] = _pstats(latency[label])
    result = {
        "n_cases": len(GROUND_TRUTH),
        "heuristic": heuristic,
        # No URLs here on purpose: the committed artifact must not churn when a
        # stand-in's ephemeral port or someone's host changes (I6 hygiene).
        "adapters": entries,
        "corpus": corpus_ceiling(),
        "skipped": skipped,
        "per_case": per_case,
    }
    if _stand_in is not None:
        _stand_in.stop()
    return result


def _pstats(samples):
    """p50/p95 over the round-trips this run actually made, in milliseconds."""
    if not samples:
        return None
    s = sorted(samples)
    return {"n": len(s), "p50": s[len(s) // 2], "p95": s[int(len(s) * 0.95) - 1],
            "max": s[-1]}


def _num(entry, key):
    """A backend that answered nothing has no metric to print, and `—` is the
    honest cell: the row exists, the number does not."""
    v = entry.get(key)
    return "%.3f" % v if v is not None else "—"


def render(m):
    lines = ["# subproto slot ablation — tool_gate (labelled synthetic)\n"]
    lines.append("Cases: %d · agreement with the heuristic, per adapter:\n" % m["n_cases"])
    for a in m["adapters"]:
        lines.append("- `%s`: %s" % (a["name"],
                                     "%.0f%%" % (100 * a["agreement"])
                                     if a["agreement"] is not None
                                     else "not measured (answered 0 of %s)"
                                          % a["answered"].partition("/")[2]))
    lines.append("\n## Read as the engine reads it — keep the options at p ≥ 0.5\n")
    lines.append("| backend | precision | recall | f1 | Δprecision vs heuristic |")
    lines.append("|---|--:|--:|--:|--:|")
    h = m["heuristic"]
    lines.append("| heuristic | %.3f | %.3f | %.3f | — |"
                 % (h["precision"], h["recall"], h["f1"]))
    for a in m["adapters"]:
        lines.append("| %s | %s | %s | %s | %s |"
                     % (a["name"], _num(a, "precision"), _num(a, "recall"),
                        _num(a, "f1"),
                        "%+.3f" % a["delta_vs_heuristic"]
                        if a["delta_vs_heuristic"] is not None else "not measured"))
    for a in m["adapters"]:
        lines.append("\n%s − heuristic precision delta: **%s**"
                     % (a["name"], "%+.3f" % a["delta_vs_heuristic"]
                        if a["delta_vs_heuristic"] is not None else "not measured"))
        if a.get("latency_ms"):
            lt = a["latency_ms"]
            lines.append("%s round trip: p50 %d ms, p95 %d ms, max %d ms over %d calls."
                         % (a["name"], lt["p50"], lt["p95"], lt["max"], lt["n"]))
        if "/" in str(a.get("answered", "")):
            got, _, total = a["answered"].partition("/")
            if int(got) < int(total):
                lines.append(
                    "%s answered %s of %s cases inside the %d ms budget; the %d it did not "
                    "are **excluded**, not scored as empty keep-sets. Raise "
                    "`SUBPROTO_MODEL_TIMEOUT_MS` (or run the model faster) to measure it "
                    "whole." % (a["name"], a["answered"], total,
                                a["budget_ms"], int(total) - int(got)))

    ranked = [a for a in m["adapters"]
              if a["source"] != "bundled lexical stand-in" and a["rules"]["rank"]
              and a["rules"]["threshold"]
              and a["rules"]["rank"]["f1"] != a["rules"]["threshold"]["f1"]]
    if ranked:
        lines.append("\n## Read as a ranking — keep the top k, k = the gold set size\n")
        lines.append("A `choice` answer is a distribution over the options (it "
                     "sums to 1), so at most one option can clear 0.5 and the table above "
                     "measures the *rule*, not the model. The same answers read the way the "
                     "compiler selects — rank them and cut at the token budget — score:\n")
        lines.append("| backend | precision | recall | f1 | Δprecision vs heuristic |")
        lines.append("|---|--:|--:|--:|--:|")
        for a in ranked:
            r = a["rules"]["rank"]
            lines.append("| %s | %.3f | %.3f | %.3f | %+.3f |"
                         % (a["name"], r["precision"], r["recall"], r["f1"],
                            round(r["precision"] - h["precision"], 4)))
        lines.append("\nWhether the slot should threshold or rank is an open decision "
                     "(SPEC FR-4): ranking this well on a synthetic set is not evidence to "
                     "change a keep rule that guards correctness (I3).")

    c = m.get("corpus") or {}
    if c:
        lines.append(
            "\n## What this corpus can and cannot measure\n"
            "\nThe gold sets were written around the heuristic's own rule, so on %d of "
            "%d cases the baseline's answer *is* the answer key (%d of %d tool decisions "
            "disagree: %d wrong keeps, %d misses). A precision delta on this set is bounded "
            "by those few rows and favours the baseline by construction — it is a pipeline "
            "test, not a model comparison. Real labels (`subproto label`, V2-A) or a set "
            "labelled without the rule in view is what makes the two columns mean "
            "something."
            % (c["gold_equals_heuristic"], c["cases"],
               c["baseline_false_keeps"] + c["baseline_false_cuts"], c["decisions"],
               c["baseline_false_keeps"], c["baseline_false_cuts"]))
        for d in c["differing_cases"]:
            lines.append("- `%s` — gold-only %s, heuristic-only %s"
                         % (d["query"], ", ".join(d["only_in_gold"]) or "—",
                            ", ".join(d["only_in_heuristic"]) or "—"))

    if m["skipped"]:
        lines.append("\nSkipped (no endpoint, so no number was invented for them):")
        for s in m["skipped"]:
            lines.append("- `%s` — %s" % (s["name"], s["why"]))

    for a in m["adapters"]:
        if a["source"] == "bundled lexical stand-in":
            lines.append("\n*Note: `%s` here is the bundled deterministic lexical stand-in "
                         "(`python -m subproto.laya_server`), which shares the heuristic's "
                         "core-tool logic, so close agreement and a ~zero delta are the "
                         "expected reading — that is a property of the stand-in, not a claim "
                         "about any real model.*" % a["name"])
        else:
            lines.append("\n*Note: `%s` is a **configured endpoint** (`%s_URL`), not the "
                         "bundled stand-in: every number in its column came from a live "
                         "round trip to a real scorer. To reproduce them, start one "
                         "(`python -m subproto.laya_server --backend laya`, which needs the "
                         "≥ 3.10 venv) and export the URL; without an endpoint this column "
                         "is skipped, never guessed.*"
                         % (a["name"], str(a["name"]).upper()))
    lines.append("\n*Every adapter column is a real `POST /score` through "
                 "`subproto.systemone.HTTPScoreAdapter`, so swapping the scorer behind a "
                 "label changes only the number, not this file. Ground truth is "
                 "author-labelled and replaced by the `subproto label` corpus at M4 "
                 "(SPEC §1.2).*")
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
        # The Router reads its evidence out of the installed package, so that is where
        # the measured file lives; `bench/` keeps only the human-readable page.
        evidence = os.path.join(here, os.pardir, "subproto", "systemone",
                                "evidence", "ablation.json")
        with open(evidence, "w") as f:
            json.dump(m, f, indent=1)
        print("\n-> wrote %s\n-> wrote %s" % (args.out, os.path.normpath(evidence)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
