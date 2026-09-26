"""Heuristic stand-ins for each decision slot.

Every slot here is a *drop-in interface*: laya is meant to replace the scoring,
not the plumbing. Each function returns per-candidate scores plus the tokens the
decision would save, which is what turns a routing guess into a measured number.
"""

import re

from . import protocol

ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|\bERROR\b|\berror:\b|FAILED|"
    r"AssertionError|panic:|exception|exit code [1-9]|\bfailed to\b",
    re.I,
)
ANCHOR_RE = re.compile(
    r"\bmust\b|\brequirement|\bacceptance|\bconstraint\b|\bdo not\b|\bnever\b|"
    r"\bplan\b|\broot cause\b|\bspec\b",
    re.I,
)
DIFF_RE = re.compile(r"^(?:--- |\+\+\+ |@@ |\+\S|-S)", re.M)
COMPLEXITY_HEAVY_RE = re.compile(
    r"\brefactor\b|\bmigrat\w+\b|\barchitect\w+\b|\bconcurrency\b|\brace\b|"
    r"\bdeadlock\b|\bcache\b|\bauth\w*\b|\bschema\b|\btest coverage\b|\bperf\w*\b|"
    r"\bdebug\b|\btrace\b|\bstack trace\b",
    re.I,
)
TRIVIAL_RE = re.compile(
    r"^\s*(fix (the )?typo|rename|add (a )?comment|reformat|format|bump|"
    r"update (the )?(readme|version|date)|say|what is|list|print)",
    re.I,
)
CORE_TOOLS = {
    "read", "read_file", "write", "write_file", "edit", "multiedit", "str_replace",
    "glob", "grep", "grep_search", "bash", "shell", "execute", "local_shell",
    "file_search", "search", "codebase", "todo", "task", "agent", "websearch",
    "web_search", "fetch", "webfetch",
}
CORE_FLOOR = 6


def file_drop_scores(query, candidates, min_score=0.8):
    """candidates: [(rel, score, why)] from graph.score_files. Returns decisions."""
    q_terms = set(protocol.lexical_tokens(query, limit=80))
    out = []
    for rel, score, why in candidates:
        keep = score >= min_score
        out.append({
            "target": rel,
            "keep": keep,
            "score": round(min(1.0, score / 6.0), 3),
            "raw": score,
            "why": why,
            "overlap": round(len(q_terms & set(rel.lower().replace("/", " ").split())) /
                             max(1, len(q_terms)), 2),
        })
    return out


def tool_gate(tools, query, threshold=0.0):
    """Score tool specs against the latest user turn; keep core tools unconditionally."""
    q_terms = set(protocol.lexical_tokens(query, limit=120))
    decisions = []
    for t in tools:
        if not isinstance(t, dict):
            decisions.append({"target": "?", "keep": True, "score": 1.0, "why": ["unparsed"]})
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = (t.get("name") or fn.get("name") or "?").lower()
        desc = " ".join([t.get("description") or fn.get("description") or ""])
        props = list(((fn.get("parameters") or {}).get("properties") or {}).keys())
        blob = " ".join([name.replace("_", " "), desc, " ".join(props)])
        terms = set(w.lower() for w in re.findall(r"[A-Za-z]{3,}", blob))
        hits = len(q_terms & terms)
        core = name in CORE_TOOLS or any(c in name for c in ("read", "edit", "bash", "grep"))
        score = hits / max(1.0, min(len(terms), 12))
        decisions.append({
            "target": name,
            "keep": bool(core) or score > threshold,
            "score": round(score, 3),
            "raw": float(hits),
            "why": (["core"] if core else []) + sorted(q_terms & terms)[:4],
        })
    kept = [d for d in decisions if d["keep"]]
    if len(kept) < CORE_FLOOR:
        for d in sorted(decisions, key=lambda x: -x["score"])[:CORE_FLOOR]:
            d["keep"] = True
            d["why"] = (d["why"] or []) + ["floor"]
    return decisions


def compact_messages(messages, budget_tokens, protected_tail=4):
    """messages: [(role, text, kind)] in request order. Returns per-message keep flags.

    The tail is always protected: dropping the last few turns changes what the
    model can do, which is a correctness risk we are not trading tokens for.
    """
    n = len(messages)
    tail_start = max(0, n - protected_tail)
    out = []
    spent = 0
    # newest first so the budget buys recency
    order = list(range(n - 1, -1, -1))
    keep = [False] * n
    for i in order:
        role, text, kind = messages[i]
        tok = protocol.approx_tokens(text)
        if i >= tail_start:
            # Sacred, not scored: the tail is reported as kept with its own flag so
            # callers get one entry per message (the per-slot path dropped the tail
            # silently, which made the label corpus miss every recent turn).
            keep[i] = True
            spent += tok
            out.append({"index": i, "target": "%s#%d" % (kind, i), "keep": True,
                        "score": 1.0, "tokens": tok, "why": ["tail", "role:%s" % kind]})
            continue
        score = 0.0
        why = []
        if kind in ("user", "system"):
            score += 0.6
            why.append("role:%s" % kind)
        if ERROR_RE.search(text):
            score += 0.45
            why.append("error")
        if DIFF_RE.search(text):
            score += 0.3
            why.append("diff")
        if ANCHOR_RE.search(text):
            score += 0.25
            why.append("anchor")
        if kind == "tool_result":
            score -= 0.25
            why.append("tool_result")
        score += 0.35 * (i / max(1, n - 1))
        if spent + tok <= budget_tokens:
            keep[i] = True
            spent += tok
        else:
            keep[i] = False
        out.append({"index": i, "target": "%s#%d" % (kind, i), "keep": keep[i],
                    "score": round(max(0.0, min(1.0, 0.5 + score / 2)), 3),
                    "tokens": tok, "why": why[:5]})
    out.sort(key=lambda d: d["index"])
    return out


def route_effort(analysis, last_user_text, est_in_tok):
    """Decide the reasoning tier this turn needs (and whether a smaller model fits)."""
    feats = analysis or {}
    heavy = len(COMPLEXITY_HEAVY_RE.findall(last_user_text or ""))
    multi_file = len(set(feats.get("paths") or [])) >= 3
    long_input = est_in_tok > 24000
    many_tool_results = (feats.get("cat_chars") or {}).get("tool_result", 0) > 40000
    trivial = bool(TRIVIAL_RE.search(last_user_text or "")) and not multi_file
    score = 0
    reasons = []
    for cond, pts, label in (
        (heavy >= 2, 2, "complexity-terms:%d" % heavy),
        (multi_file, 1, "multi-file"),
        (long_input, 1, "long-input"),
        (many_tool_results, 1, "tool-heavy"),
        (trivial, -3, "trivial-request"),
        ((feats.get("user_turns") or 0) <= 1 and not heavy, -1, "first-turn-simple"),
    ):
        if cond:
            score += pts
            reasons.append(label)
    tier = "low" if score <= -1 else ("high" if score >= 2 else "medium")
    small_ok = tier == "low" and not multi_file and est_in_tok < 16000
    return {
        "tier": tier,
        "score": score,
        "reasons": reasons,
        "small_model_ok": small_ok,
        "est_in_tok": est_in_tok,
    }
