import json
import os
import sqlite3

from . import pricing, protocol
from .heuristics import route_effort, tool_gate
from .telemetry import CATEGORIES


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def precision_metrics(gold, pred):
    """Binary-set precision/recall/F1/accuracy — shared by the ablation harness
    (bench/ablation.py) and label-driven slot precision (subproto audit)."""
    gold = set(gold)
    pred = set(pred)
    tp = len(gold & pred)
    fp = len(pred - gold)
    fn = len(gold - pred)
    precision = tp / float(tp + fp) if (tp + fp) else 1.0
    recall = tp / float(tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def summarize(telemetry, since=None, until=None):
    where, args = [], []
    if since:
        where.append("day >= ?")
        args.append(since)
    if until:
        where.append("day <= ?")
        args.append(until)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = telemetry.query(
        "SELECT count(*) n, sum(in_tok) in_tok, sum(cw_tok) cw_tok, sum(cr_tok) cr_tok, "
        "sum(out_tok) out_tok, sum(reasoning_tok) reasoning_tok, sum(cost_usd) cost, "
        "sum(latency_ms) lat, sum(ttfb_ms) ttfb, sum(status>=400) fails, "
        "sum(est_in_tok) est_in, sum(tool_spec_chars) tool_chars, sum(sys_chars) sys_chars "
        "FROM requests " + w, args)
    tot = rows[0] if rows else {}
    by_model = telemetry.query(
        "SELECT model, count(*) n, sum(in_tok+cw_tok) fresh_in, sum(cr_tok) cached_in, "
        "sum(out_tok) out_tok, sum(cost_usd) cost, sum(ttfb_ms)/max(1,count(*)) ttfb "
        "FROM requests " + w + " GROUP BY model ORDER BY cost DESC", args)
    by_client = telemetry.query(
        "SELECT client, count(*) n, sum(cost_usd) cost FROM requests " + w
        + " GROUP BY client ORDER BY cost DESC", args)
    by_cat = telemetry.query(
        "SELECT cat_chars FROM requests " + w, args)
    cats = dict((c, 0) for c in CATEGORIES)
    for r in by_cat:
        try:
            d = json.loads(r["cat_chars"] or "{}")
        except ValueError:
            continue
        for k, v in d.items():
            cats[k] = cats.get(k, 0) + int(v or 0)
    days = telemetry.query(
        "SELECT day, count(*) n, sum(cost_usd) cost, sum(in_tok+cw_tok+cr_tok) in_tok, "
        "sum(out_tok) out_tok FROM requests " + w + " GROUP BY day ORDER BY day", args)
    summary = {"totals": tot, "by_model": by_model, "by_client": by_client,
               "cat_chars": cats, "by_day": days,
               "requests": telemetry.query(
                   "SELECT id, ts, api, client, model, status, in_tok, cw_tok, cr_tok, out_tok, "
                   "cost_usd, latency_ms, ttfb_ms, engine_ms, tool_spec_chars, est_in_tok, "
                   "interventions, err FROM requests " + w + " ORDER BY id DESC LIMIT 500", args)}
    ems = sorted(float(r["engine_ms"]) for r in summary["requests"]
                 if r.get("engine_ms") is not None)
    summary["decision_latency"] = {
        "n": len(ems),
        "p50": _percentile(ems, 0.50),
        "p95": _percentile(ems, 0.95),
        "avg": round(sum(ems) / len(ems), 3) if ems else None,
    }
    return summary


def cache_opportunity(by_model):
    """Input tokens that were billed at the full rate but could have been a cache hit.

    Only counted for models where the provider offers caching and the observed
    cached fraction is far below what the same agent achieves with a stable
    prefix — this is an upper bound, labelled as such by the report.
    """
    total = 0.0
    detail = []
    for row in by_model:
        model = row.get("model") or ""
        if not any(k in model.lower() for k in ("claude", "gpt", "gemini")):
            continue
        fresh = row.get("fresh_in") or 0
        cached = row.get("cached_in") or 0
        if fresh + cached <= 0:
            continue
        ratio = cached / float(fresh + cached)
        target = 0.85 if "claude" in model.lower() else 0.60
        if ratio >= target:
            continue
        base, pcache, _ = pricing.price_for(model)
        reclaim = fresh * (target - ratio)
        usd = reclaim * (base - pcache) / 1e6
        total += usd
        detail.append({"model": model, "cached_ratio": round(ratio, 3),
                       "target": target, "reclaimable_in_tok": int(reclaim),
                       "usd": round(usd, 4)})
    return {"usd": round(total, 4), "by_model": detail}


def opportunity_gaps(summary):
    """Per-slot token headroom, derived from the recorded prompt shape."""
    t = summary["totals"] or {}
    n = max(1, int(t.get("n") or 1))
    cats = summary["cat_chars"]
    tool_tok = int((t.get("tool_chars") or 0) / 3.6)
    tr_tok = int((cats.get("tool_result") or 0) / 3.6)
    return [
        {"slot": "tool_gate", "est_tok_total": tool_tok, "est_tok_per_req": int(tool_tok / n),
         "share_of_input": round(tool_tok / max(1, int(t.get("est_in") or 1)), 3),
         "note": "tool schemas ride along in every request; most are unused per turn"},
        {"slot": "compact", "est_tok_total": tr_tok, "est_tok_per_req": int(tr_tok / n),
         "share_of_input": round(tr_tok / max(1, int(t.get("est_in") or 1)), 3),
         "note": "tool results are the largest replayed block in agent loops"},
    ]


def render_text(summary, since=None, until=None):
    t = summary["totals"] or {}
    n = int(t.get("n") or 0)
    lines = []
    lines.append("subproto report  ·  %s requests%s" % (
        n, ("  ·  %s..%s" % (since, until)) if (since or until) else ""))
    if not n:
        lines.append("")
        lines.append("No telemetry yet. Point an agent at the proxy:")
        lines.append("  ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude")
        return "\n".join(lines)
    fin = int(t.get("in_tok") or 0) + int(t.get("cw_tok") or 0)
    cin = int(t.get("cr_tok") or 0)
    out = int(t.get("out_tok") or 0)
    cost = float(t.get("cost") or 0.0)
    ttft = [r["ttfb_ms"] for r in summary["requests"] if r.get("ttfb_ms")]
    lines.append("")
    lines.append("  billed input        %12d tok" % fin)
    lines.append("  cached input        %12d tok  (%.1f%%)" % (
        cin, 100.0 * cin / max(1, fin + cin)))
    lines.append("  output              %12d tok" % out)
    lines.append("  spend               %12s" % ("$%.2f" % cost))
    lines.append("  failures            %12d" % int(t.get("fails") or 0))
    if ttft:
        ttft.sort()
        lines.append("  time to first token p50 %4dms  p95 %5dms" % (
            _percentile(ttft, 0.50), _percentile(ttft, 0.95)))
    dl = summary.get("decision_latency") or {}
    if dl.get("n"):
        lines.append("  slot decision latency p50 %.1fms  p95 %.1fms  (n=%d)" % (
            dl["p50"], dl["p95"], dl["n"]))
    lines.append("")
    lines.append("  where the input tokens came from (request characters)")
    cats = summary["cat_chars"]
    ctotal = max(1, sum(cats.values()))
    for k in CATEGORIES:
        v = cats.get(k, 0)
        lines.append("    %-18s %8.1f%%  %10d tok est" % (
            k, 100.0 * v / ctotal, int(v / 3.6)))
    lines.append("")
    lines.append("  by model")
    for r in summary["by_model"][:8]:
        lines.append("    %-34s %4d req  in %9d  cached %6.0f%%  $%.2f" % (
            (r.get("model") or "?")[:34], r["n"], int(r.get("fresh_in") or 0),
            100.0 * (r.get("cached_in") or 0) / max(1, (r.get("fresh_in") or 0) + (r.get("cached_in") or 0)),
            float(r.get("cost") or 0)))
    if summary["by_client"]:
        lines.append("")
        lines.append("  by client")
        for r in summary["by_client"][:6]:
            lines.append("    %-16s %4d req  $%.2f" % (
                r["client"] or "?", r["n"], float(r.get("cost") or 0)))
    opp = cache_opportunity(summary["by_model"])
    if opp["by_model"]:
        lines.append("")
        lines.append("  cache-stability headroom (upper bound, not a promise)")
        for d in opp["by_model"]:
            lines.append("    %-34s cached %5.1f%% -> %4.0f%%  reclaim %9d tok  $%.2f" % (
                d["model"][:34], 100 * d["cached_ratio"], 100 * d["target"],
                d["reclaimable_in_tok"], d["usd"]))
        lines.append("    total headroom  $%.2f" % opp["usd"])
    lines.append("")
    lines.append("  slot headroom")
    for g in opportunity_gaps(summary):
        lines.append("    %-10s %9d tok total  %6d/req  %5.1f%% of input" % (
            g["slot"], g["est_tok_total"], g["est_tok_per_req"], 100 * g["share_of_input"]))
        lines.append("               %s" % g["note"])
    lines.append("")
    lines.append("  run `subproto audit` to see what the slots would drop, "
                 "and `subproto export` to build the fine-tuning set.")
    return "\n".join(lines)


def render_json(summary):
    out = dict(summary)
    out["cache_headroom"] = cache_opportunity(summary["by_model"])
    out["slot_headroom"] = opportunity_gaps(summary)
    return out
