import json
import os
import sqlite3

from . import pricing, protocol, style as st
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


def slot_precision(telemetry, labels):
    """Label-driven per-slot approval rate from the `subproto label` corpus.

    Human labels are verdicts on a whole decision (good/bad/uncertain), so the
    honest precision signal is the approval rate over labelled decisions:
    good / (good + bad). Uncertain and unlabelled decisions are excluded, and a
    slot with no labels is reported as `None` rather than a fabricated 1.0.
    """
    rows = telemetry.query(
        "SELECT id, decisions FROM requests WHERE decisions IS NOT NULL ORDER BY id")
    tally = {}
    for r in rows:
        try:
            decisions = json.loads(r["decisions"] or "[]")
        except ValueError:
            continue
        entry = labels.get(str(r["id"])) or {}
        for d in decisions:
            slot = d.get("slot")
            verdict = entry.get(slot)
            if verdict not in ("good", "bad", "uncertain"):
                continue
            t = tally.setdefault(slot, {"good": 0, "bad": 0, "uncertain": 0})
            t[verdict] += 1
    out = {}
    for slot, t in sorted(tally.items()):
        den = t["good"] + t["bad"]
        out[slot] = {
            "labelled": sum(t.values()),
            "good": t["good"], "bad": t["bad"], "uncertain": t["uncertain"],
            "approval_rate": round(t["good"] / float(den), 4) if den else None,
        }
    return out


def version_rollup(telemetry, labels=None):
    """Per model *version*: the decisions it answered, what they saved, how they scored.

    ROADMAP v4 asks "was the fine-tune better than the model it replaced?". That is only
    answerable if every decision carries the checkpoint that produced it, so this groups
    the corpus by that stamp and puts it beside the label-driven approval rate. Decisions
    recorded before the stamp existed group under `unrecorded` and stay visible — a
    version table that quietly omits them would make a new model look cleaner than it is.
    """
    rows = telemetry.query(
        "SELECT id, decisions FROM requests WHERE decisions IS NOT NULL ORDER BY id")
    tally = {}
    for r in rows:
        try:
            decisions = json.loads(r["decisions"] or "[]")
        except ValueError:
            continue
        entry = (labels or {}).get(str(r["id"])) or {}
        for d in decisions:
            version = d.get("model_version") or "unrecorded"
            t = tally.setdefault(version, {
                "decisions": 0, "requests": set(), "saved_tok": 0, "ms": [],
                "slots": {}, "backends": {}, "good": 0, "bad": 0, "uncertain": 0})
            t["decisions"] += 1
            t["requests"].add(r["id"])
            t["saved_tok"] += int(d.get("savings_est_tok") or 0)
            if d.get("decision_ms") is not None:
                t["ms"].append(float(d["decision_ms"]))
            slot = d.get("slot") or "?"
            t["slots"][slot] = t["slots"].get(slot, 0) + 1
            backend = d.get("backend") or "?"
            t["backends"][backend] = t["backends"].get(backend, 0) + 1
            verdict = entry.get(slot)
            if verdict in ("good", "bad", "uncertain"):
                t[verdict] += 1
    out = {}
    for version, t in sorted(tally.items()):
        ms = sorted(t.pop("ms"))
        requests = t.pop("requests")
        den = t["good"] + t["bad"]
        out[version] = dict(t, requests=len(requests),
                            p50_decision_ms=round(_percentile(ms, 0.50), 3) if ms else None,
                            approval_rate=round(t["good"] / float(den), 4) if den else None)
    return out


def _cache_hit_rate(totals):
    """Cached share of input tokens actually read from cache, across all requests."""
    fin = int(totals.get("in_tok") or 0) + int(totals.get("cw_tok") or 0)
    cin = int(totals.get("cr_tok") or 0)
    denom = fin + cin
    return {
        "fresh_in": fin,
        "cached_in": cin,
        "hit_rate": round(cin / float(denom), 4) if denom else None,
    }


def summarize(telemetry, since=None, until=None, labels=None, implicit=None):
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
    summary["cache_hit_rate"] = _cache_hit_rate(summary["totals"] or {})
    summary["by_version"] = version_rollup(telemetry, labels)
    if labels:
        summary["slot_precision"] = slot_precision(telemetry, labels)
    if implicit is not None:
        summary["eviction_regret"] = eviction_regret(implicit)
        summary["implicit_coverage"] = implicit_coverage(summary, implicit, labels or {})
    return summary


def eviction_regret(harvest):
    """What the harvested traffic said about the drops this telemetry recorded.

    Every number is the harvest's own `summary`, printed with the enforced/shadow
    split kept apart: a shadow-mode drop never reached the wire, so it cannot have
    cost a re-read. `regrettable_per_1k_requests` is the honest rate — it uses every
    request with a recorded decision, not only the ones whose body was spooled.
    """
    h = (harvest or {}).get("summary") or {}
    scanned = int(h.get("requests_scanned") or 0)
    enforced = int(h.get("wrong_drops_enforced") or 0)
    shadow = int(h.get("wrong_drops_shadow") or 0)
    regret = enforced + shadow
    return {
        "requests_with_decisions": scanned,
        "bodies_readable": int(h.get("bodies_available") or 0),
        "bodies_never_stored": int(h.get("skipped_no_body") or 0),
        "evicted_reads_seen": int(h.get("evicted_reads_seen") or 0),
        "regrettable_drops": regret,
        "enforced": enforced,
        "shadow": shadow,
        "refetch_tok_paid": int(h.get("refetch_tok_paid") or 0),
        "regrettable_per_1k_requests": round(regret * 1000.0 / scanned, 1) if scanned else None,
        "by_source": h.get("by_source") or {},
        "window_turns": h.get("window_turns"),
        "window_s": h.get("window_s"),
        "harvested_at": h.get("harvested_at"),
    }


def implicit_coverage(summary, harvest, labels):
    """How many of the *recorded* requests the harvest could speak about at all."""
    implicit = (harvest or {}).get("labels") or {}
    rows = summary["requests"]
    by_id = dict((str(r["id"]), r) for r in rows)
    covered = [rid for rid in by_id
               if (implicit.get(rid) or {}).get("targets")
               or (implicit.get(rid) or {}).get("slots")]
    human = [rid for rid in by_id if (labels or {}).get(rid)]
    from . import dataset
    verdicts = {}
    for rid in covered:
        label = dataset._merge_verdicts((labels or {}).get(rid), implicit.get(rid))[2]
        verdicts[label or "none"] = verdicts.get(label or "none", 0) + 1
    return {
        "requests_in_window": len(rows),
        "covered_by_harvest": len(covered),
        "coverage": round(len(covered) / float(len(rows)), 4) if rows else None,
        "covered_by_human": len(human),
        "human_only": len(set(human) - set(covered)),
        "harvest_only": len(set(covered) - set(human)),
        "disagreements": len([rid for rid in covered
                              if _verdict_of(labels, implicit, rid) == "mixed"]),
        "verdicts": verdicts,
    }


def _verdict_of(labels, implicit, rid):
    from . import dataset
    return dataset._merge_verdicts((labels or {}).get(rid), implicit.get(rid))[0]


def regret_section(er, cov=None, color=False):
    """The eviction-regret block, as lines — shared by `report` and `learn` so the
    two commands cannot print different versions of the same measurement.

    `er` is `eviction_regret(harvest)`; `cov` (report only) adds how many of the
    requests in the window the harvest could speak about at all.
    """
    if not er:
        return []
    cov = cov or {}
    verdicts = cov.get("verdicts") or {}
    out = ["", st.section("eviction regret",
                          "(what the recorded traffic said about the drops)",
                          color=color)]
    if cov:
        out.append(st.detail("requests scanned", er["requests_with_decisions"]))
        out.append(st.prose("bodies readable %d, never stored %d, carrying an implicit "
                            "verdict %d" % (
                                er["bodies_readable"], er["bodies_never_stored"],
                                cov.get("covered_by_harvest", 0)),
                            indent=st.SUB_INDENT + 2, color=color))
    out.append(st.detail("evicted reads seen", er["evicted_reads_seen"]))
    out.append(st.detail("regrettable drops", er["regrettable_drops"],
                         "enforced %d, shadow %d" % (er["enforced"], er["shadow"])))
    out.append(st.detail("tokens paid back", er["refetch_tok_paid"],
                         "re-reads of the %d drop%s that reached the wire" % (
                             er["enforced"], "" if er["enforced"] == 1 else "s")))
    if er["shadow"]:
        out.append(st.prose("the %d shadow drop%s cost nothing: nothing was cut, so "
                            "nothing had to be re-read" % (
                                er["shadow"], "" if er["shadow"] == 1 else "s"),
                            indent=st.SUB_INDENT + 2, color=color))
    if not er["regrettable_drops"]:
        out.append(st.prose("nothing was re-read after it was cut, so nothing is owed "
                            "back — that is a real 0, not a missing measurement",
                            indent=st.SUB_INDENT + 2, color=color))
    out.append(st.detail("window", "%d turns" % er["window_turns"],
                         "%ds of recorded traffic, read at %s" % (
                             er["window_s"], er["harvested_at"])))
    actions = dict((src, row) for src, row in (er["by_source"] or {}).items())
    if actions:
        out.append(st.sub("verdicts by signal", indent=st.SUB_INDENT, color=color))
    for a, row in sorted(actions.items()):
        # The price is the number; how many verdicts it came from is its qualifier.
        out.append(st.detail(a, row["regret_tok"], "%d label%s" % (
            row["labels"], "" if row["labels"] == 1 else "s")))
    if not actions:
        out.append(st.detail("verdicts by signal", "none"))
        out.append(st.note("no drop was contradicted", indent=st.SUB_INDENT + 2,
                           color=color))
    if verdicts:
        out.append(st.sub("implicit verdicts by kind", indent=st.SUB_INDENT,
                          color=color))
    for v, n in sorted(verdicts.items()):
        out.append(st.detail(v, n))
    if verdicts and cov.get("disagreements"):
        out.append(st.detail("human/traffic disagree", cov["disagreements"],
                             "the human verdict wins"))
    return out


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


def render_text(summary, since=None, until=None, color=False):
    t = summary["totals"] or {}
    n = int(t.get("n") or 0)
    lines = []
    lines.append(st.header("report", "%s requests" % n,
                           *(["%s..%s" % (since, until)] if (since or until) else []),
                           color=color))
    if not n:
        lines.append("")
        lines.append(st.prose("No telemetry yet. Point an agent at the proxy:",
                              indent=0, color=color))
        lines.append(st.action("ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude",
                               color=color))
        return "\n".join(lines)
    fin = int(t.get("in_tok") or 0) + int(t.get("cw_tok") or 0)
    cin = int(t.get("cr_tok") or 0)
    out = int(t.get("out_tok") or 0)
    cost = float(t.get("cost") or 0.0)
    ttft = [r["ttfb_ms"] for r in summary["requests"] if r.get("ttfb_ms")]
    lines.append("")
    lines.append(_metric("billed input", fin, "tok", color=color))
    lines.append(_metric("cached input", cin, "tok", color=color,
                         tail="%.1f%%" % (100.0 * cin / max(1, fin + cin))))
    lines.append(_metric("output", out, "tok", color=color))
    lines.append(_metric("spend", "$%.2f" % cost, color=color))
    lines.append(_metric("failures", int(t.get("fails") or 0), color=color))
    if ttft:
        ttft.sort()
        lines.append(_metric("time to first token", "p50 %dms  p95 %dms" % (
            _percentile(ttft, 0.50), _percentile(ttft, 0.95)), color=color))
    dl = summary.get("decision_latency") or {}
    if dl.get("n"):
        lines.append(_metric("slot decision latency", "p50 %.1fms  p95 %.1fms" % (
            dl["p50"], dl["p95"]), color=color, tail="n=%d" % dl["n"]))
    chr_ = summary.get("cache_hit_rate") or {}
    if chr_.get("hit_rate") is not None:
        lines.append(_metric("cache hit rate", "%.1f%%" % (100.0 * chr_["hit_rate"]),
                             color=color,
                             tail="%s cached / %s input tok" % (
                                 st.format_num(chr_["cached_in"]),
                                 st.format_num(chr_["cached_in"] + chr_["fresh_in"]))))
    lines.append("")
    lines.append(st.section("where the input tokens came from",
                            "(request characters)", indent=2, color=color))
    cats = summary["cat_chars"]
    ctotal = max(1, sum(cats.values()))
    for k in CATEGORIES:
        v = cats.get(k, 0)
        lines.append(st.table(k, ("%.1f%%" % (100.0 * v / ctotal), "", 7),
                              (int(v / 3.6), "tok est", 10), name_w=20, color=color))
    lines.append("")
    lines.append(st.section("by model", indent=2, color=color))
    for r in summary["by_model"][:8]:
        lines.append(st.table(
            (r.get("model") or "?")[:34],
            (r["n"], "req", 5),
            (int(r.get("fresh_in") or 0), "in", 9),
            ("%.1f%%" % (100.0 * (r.get("cached_in") or 0)
                         / max(1, (r.get("fresh_in") or 0)
                               + (r.get("cached_in") or 0))), "cached", 6),
            ("$%.2f" % float(r.get("cost") or 0), "", 6),
            name_w=36, color=color))
    if summary["by_client"]:
        lines.append("")
        lines.append(st.section("by client", indent=2, color=color))
        for r in summary["by_client"][:6]:
            lines.append(st.table(r["client"] or "?", (r["n"], "req", 5),
                                  ("$%.2f" % float(r.get("cost") or 0), "", 6),
                                  name_w=18, color=color))
    opp = cache_opportunity(summary["by_model"])
    if opp["by_model"]:
        lines.append("")
        lines.append(st.section("cache-stability headroom",
                                "(upper bound, not a promise)", indent=2, color=color))
        for d in opp["by_model"]:
            lines.append(st.table(
                d["model"][:34],
                ("%.1f%%" % (100 * d["cached_ratio"]), "cached", 6),
                ("%.0f%%" % (100 * d["target"]), "-> to", 4),
                (d["reclaimable_in_tok"], "tok", 9),
                ("$%.2f" % d["usd"], "", 6), name_w=36, color=color))
        lines.append(st.detail("total headroom", "$%.2f" % opp["usd"],
                               label_w=16, color=color))
    lines.append("")
    lines.append(st.section("slot headroom", indent=2, color=color))
    for g in opportunity_gaps(summary):
        lines.append(st.table(
            g["slot"], (g["est_tok_total"], "tok", 9),
            (g["est_tok_per_req"], "/req", 6),
            ("%.1f%%" % (100 * g["share_of_input"]), "of input", 5),
            name_w=12, color=color))
        lines.append(st.note(g["note"], indent=st.SUB_INDENT + 2, color=color))
    sp = summary.get("slot_precision")
    if sp:
        lines.append("")
        lines.append(st.section("slot precision", "(from `subproto label` verdicts)",
                                indent=2, color=color))
        lines.append(st.note("approval = good/(good+bad)", indent=st.SUB_INDENT,
                             color=color))
        for slot, m in sorted(sp.items()):
            ar = m["approval_rate"]
            lines.append(st.table(
                slot, (m["labelled"], "labelled", 4), (m["good"], "good", 4),
                (m["bad"], "bad", 4), (m["uncertain"], "uncertain", 4),
                ((("%.1f%%" % (100 * ar)) if ar is not None else "n/a"), "approval", 6),
                name_w=12, color=color))
    bv = summary.get("by_version") or {}
    if bv:
        lines.append("")
        lines.append(st.section("by model version",
                                "(which checkpoint answered; `models.json` owns the list)",
                                indent=2, color=color))
        for version, m in sorted(bv.items(), key=lambda kv: -kv[1]["decisions"]):
            ar = m["approval_rate"]
            lines.append(st.table(
                version[:22],
                (m["decisions"], "decisions", 5),
                (m["saved_tok"], "tok saved", 8),
                ((("%.1f" % m["p50_decision_ms"]) if m["p50_decision_ms"] is not None
                  else "-"), "ms p50", 5),
                ((("%.1f%%" % (100 * ar)) if ar is not None else "n/a"), "approval", 6),
                name_w=22, color=color))
            lines.append(st.note("slots %s — backends %s" % (
                ", ".join("%s=%d" % kv for kv in sorted(m["slots"].items())),
                ", ".join("%s=%d" % kv for kv in sorted(m["backends"].items()))),
                indent=st.SUB_INDENT + 2, color=color))
    lines.extend(regret_section(summary.get("eviction_regret"),
                                summary.get("implicit_coverage"), color=color))
    lines.append("")
    lines.append(st.prose("run `subproto audit` to see what the slots would drop, "
                          "`subproto learn` to let the traffic judge them, and "
                          "`subproto export` to build the fine-tuning set.",
                          indent=0, color=color))
    return "\n".join(lines)


def _metric(label, value, unit=None, tail=None, color=False):
    """One line of the readout: label in the column, number right-aligned, unit
    and qualifier in dim so the figure is the only loud thing."""
    body = st.field(value, color=color)
    if unit:
        body += st.unit(unit, color=color)
    if tail:
        body += st.unit("(%s)" % tail, color=color)
    return st.row(label, body, label_w=st.METRIC_W, color=color, styled=True)


def render_json(summary):
    out = dict(summary)
    out["cache_headroom"] = cache_opportunity(summary["by_model"])
    out["slot_headroom"] = opportunity_gaps(summary)
    return out
