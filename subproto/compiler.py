"""The Context Compiler (v5): one token budget, one optimiser, all editing slots.

The per-slot pipeline spends *three independent* budgets, and the slots cannot see
each other. `compact` scores a message on role, recency and error markers alone, so
a 2.6k-token tool result that the code graph ranks #1 for the current task can be
evicted while `context` pays a second time to name that same file in prose — and
`tool_gate` can drop the tool the kept file needs. The frontier model then loses
evidence it was about to use, which is a correctness cost no token number pays for.

The compiler harvests every editable item — messages, tool specs, candidate files —
into one pool, scores them *together* (graph evidence raises a message's value; a
file already named by a message we must keep is worth less again in the note), and
spends a single budget on the best value per token.

Two things are structural rather than tuned:
  * protected items (the conversational tail, core tools, files the user named by
    path, and the few graph-critical reads that fit a bounded share of the budget)
    are booked *before* the optimiser runs, so I3 holds by construction;
  * the cached prefix is never a candidate and the file note is appended after the
    last turn, so I2 holds by construction.
"""

from . import graph as graph_mod
from . import heuristics
from . import protocol

# The editable pool. `system`/tools-block prefixes are deliberately absent: they are
# the cache key, so a candidate they contain could only be dropped by busting I2.
KINDS = ("message", "tool", "file")
PROTECTED_TAIL = 4
# Same split the compact slot uses, so the joint budget is no *looser* than the sum
# of the per-slot ones — the compiler has to earn its retention on equal or tighter
# spend, not on a friendlier number.
TARGET_FRACTION = 0.45
MIN_BUDGET = 1500
FILE_NOTE_HEADER = ("Relevant files in this repo, ranked by a local code graph "
                    "(not read yet):")
# A message that names a file the graph ranks within EVIDENCE_MIN of the best hit is
# booked like the tail — but only while the booked set stays inside PROTECTED_MAX of the
# budget. An unbounded rule would let one huge read force `over_budget` and turn the
# whole compiler into a no-op, which is a savings cost paid for a claim we can't make.
EVIDENCE_MIN = 0.8
EVIDENCE_MAX = 3
# "The user typed this path into the task" is a strong prior on its own, and it is the
# only signal available for files the index does not cover (see LANG_BY_EXT).
EVIDENCE_NAMED = 0.9
PROTECTED_MAX = 0.6


def default_budget(analysis):
    """The joint budget: the same split the compact slot uses, so the joint plan is no
    looser than the per-slot spend it replaces."""
    est_in = (analysis or {}).get("est_in_tok") or 0
    return max(MIN_BUDGET, int(est_in * TARGET_FRACTION))



class Candidate(object):
    """One item the compiler may keep or drop, scored on a common 0..1+ scale."""

    __slots__ = ("kind", "id", "tokens", "value", "protected", "why", "index", "keep",
                 "_floor", "_last_kept", "_left")

    def __init__(self, kind, id, tokens, value, protected=False, why=None, index=None):
        self.kind = kind
        self.id = id
        self.tokens = max(0, int(tokens))
        self.value = float(value)
        self.protected = bool(protected)
        self.why = list(why or [])
        self.index = index
        self.keep = None

    @property
    def ratio(self):
        """Value per token. A free item is infinitely worth taking."""
        if self.tokens <= 0:
            return float("inf")
        return self.value / float(self.tokens)

    def as_decision(self):
        """The per-candidate shape `dataset.py` and the report already read."""
        return {"target": self.id, "keep": bool(self.keep), "score": round(self.value, 3),
                "tokens": self.tokens, "why": self.why[:5], "protected": self.protected}

    def __repr__(self):
        return "<%s %s tok=%d val=%.2f %s>" % (
            self.kind, self.id, self.tokens, self.value, "PROTECTED" if self.protected else "")


def plan(candidates, budget):
    """Greedy value-per-token knapsack; protected items are booked first.

    Returns {budget, over_budget, kept[], dropped[], tokens_before, tokens_after,
    protected_tokens, floor_ratio}. `over_budget` means the protected set alone does
    not fit: we report that instead of silently truncating the tail (I3), and the
    caller leaves the request untouched.
    """
    protected = [c for c in candidates if c.protected]
    open_ = [c for c in candidates if not c.protected]
    for c in protected:
        c.keep = True
    p_tok = sum(c.tokens for c in protected)
    before = sum(c.tokens for c in candidates)
    if p_tok > budget:
        for c in open_:
            c.keep = None
        return {"budget": budget, "over_budget": True, "protected_tokens": p_tok,
                "tokens_before": before, "tokens_after": p_tok,
                "kept": [], "dropped": [], "floor_ratio": None,
                "reason": "protected set needs %d tok, budget is %d" % (p_tok, budget)}

    remaining = budget - p_tok
    # Deterministic: best value-per-token, then absolute value, then kind, then id —
    # so the same pool always compiles to the same prompt (a reproducible claim).
    open_.sort(key=lambda c: (-c.ratio, -c.value, c.kind, c.id))
    kept, dropped = list(protected), []
    floor_ratio, last_kept = None, None
    for c in open_:
        if c.tokens <= remaining:
            c.keep = True
            remaining -= c.tokens
            kept.append(c)
            if floor_ratio is None or c.ratio < floor_ratio:
                floor_ratio, last_kept = c.ratio, c.id
        else:
            c.keep = False
            dropped.append(c)
    for c in dropped:
        c._floor = floor_ratio
        c._last_kept = last_kept
        c._left = remaining
    return {"budget": budget, "over_budget": False, "protected_tokens": p_tok,
            "tokens_before": before, "tokens_after": sum(c.tokens for c in kept),
            "kept": kept, "dropped": dropped, "floor_ratio": floor_ratio,
            "reason": None}


def _drop_reason(c):
    """Why this item lost — naming the joint decision that beat it (S21).

    Six decimals, not four: at %.4f a real strict loss printed as "0.0030 < 0.0030",
    which reads as a false statement about the numbers we are claiming to explain.
    """
    floor = getattr(c, "_floor", None)
    if floor is None:
        return "nothing was competitive"
    if c.ratio >= floor:
        return ("value/token %.6f beat the kept floor %.6f but does not fit the %d tok "
                "left after the kept set" % (c.ratio, floor, getattr(c, "_left", 0)))
    return ("value/token %.6f < kept floor %.6f (floor set by %s)"
            % (c.ratio, floor, getattr(c, "_last_kept", "?")))


def build_candidates(body, analysis, graph=None, query="", budget=None):
    """One pool from the *existing* heuristics, cross-informed by the graph.

    Returns (candidates, meta). Scoring stays in `heuristics` so no logic forks; what
    lives here is only the coupling the separate slots cannot express.
    """
    msgs = protocol.normalize_messages(body)
    # A huge budget inside compact_messages: we want its per-message scores, not its
    # own spend decision — that is the joint planner's job from here on.
    total = sum(protocol.approx_tokens(m[1]) for m in msgs) or 1
    flags = heuristics.compact_messages(msgs, total, protected_tail=PROTECTED_TAIL)
    tail_start = max(0, len(msgs) - PROTECTED_TAIL)
    offset = 1 if isinstance(body.get("system"), str) and body.get("system") else 0
    goff = 1 if (body.get("systemInstruction") or body.get("system_instruction")) else 0

    file_scores = {}
    if graph is not None:
        for rel, raw, why in graph_mod.search(graph, query or "", top_k=10):
            file_scores[rel] = (raw, why)

    cands = []
    named_in_tail = set()
    query_paths = set(protocol.extract_paths(query))
    ev_map = {}
    for f in flags:
        role, text, kind = msgs[f["index"]]
        if kind == "system":
            continue  # the cached prefix: never a candidate, never dropped
        if f["index"] >= tail_start:
            for p in protocol.extract_paths(text):
                named_in_tail.add(p)
        value = f["score"]
        why = list(f["why"])
        # Graph coupling: a message that *mentions* a file the code graph ranks highly
        # for this task is carrying evidence, whatever its role or recency says. This
        # is the one thing the per-slot design structurally cannot do.
        best = 0.0
        for p in protocol.extract_paths(text):
            for rel, (raw, _) in file_scores.items():
                if p == rel.lower() or p.endswith("/" + rel.lower()) or rel.lower().endswith(p):
                    best = max(best, min(1.0, raw / 6.0))
                    if "graph" not in why:
                        why.append("graph")
            # And the same for a path the user named in the task itself. The graph can
            # already rank a code, doc, SQL or data read (S31), but a typed path is
            # *direct* evidence rather than a lexical guess, so it floors the value at
            # EVIDENCE_NAMED instead of waiting for the index to agree.
            if any(protocol.same_path(p, q) for q in query_paths):
                if best < EVIDENCE_NAMED:
                    best = EVIDENCE_NAMED
                    if "names-task-file" not in why:
                        why.append("names-task-file")
        value += 0.5 * best
        if best >= EVIDENCE_MIN:
            ev_map[f["index"]] = max(ev_map.get(f["index"], 0.0), best)
        c = Candidate("message", f["target"], f["tokens"], value,
                      protected=f["index"] >= tail_start, why=why, index=f["index"])
        cands.append(c)

    flat_tools = protocol.flatten_tools(body.get("tools") or [])
    # ONE call over the whole tool list, and `core` alone is sacred: CORE_FLOOR tops the
    # keep-list up to six entries, so a `floor` tag means "there were too few keepers",
    # not "the agent breaks without it". Honouring it here would make the joint budget
    # inherit the per-slot artifact and gate nothing.
    for t, d in zip(flat_tools, heuristics.tool_gate(flat_tools, query or "")):
        toks = max(1, int((t.get("_spec_chars") or 0) / 3.6))
        protected = bool(d["keep"]) and "core" in (d["why"] or [])
        cands.append(Candidate("tool", str(d["target"]).lower(), toks, d["score"],
                               protected=protected, why=list(d["why"] or []), index=None))

    for rel, (raw, why) in sorted(file_scores.items(), key=lambda kv: -kv[1][0]):
        line = "- " + rel
        named = any(protocol.same_path(rel, p) for p in query_paths)
        already = any(protocol.same_path(rel, p) for p in named_in_tail)
        value = min(1.0, raw / 6.0)
        w = list(why or []) + (["named-in-task"] if named else [])
        if already:
            # Already in the prompt verbatim: naming it again in the note is a second
            # toll on the same evidence, so it is worth much less here.
            value *= 0.25
            w.append("already-in-tail")
        cands.append(Candidate("file", rel, protocol.approx_tokens(line), value,
                              protected=named and not already, why=w, index=None))

    # Booked last, so it can see the whole sacred set. A turn that names the task's
    # top-ranked file is *evidence*, not filler, and a value-per-token greedy starves
    # it: one 4.7k-token read loses to fifty 90-token turns on ratio every time, which
    # is the mistake the per-slot path also makes. So the strongest few join the
    # protected set — but only while the booked set stays inside PROTECTED_MAX of the
    # budget, because booking an unreadably large read would report `over_budget` and
    # cut nothing at all. Bounded by EVIDENCE_MAX; items that do not fit stay ordinary
    # (still graph-boosted) candidates rather than forcing a no-op.
    budget = budget or default_budget(analysis)
    booked = sum(c.tokens for c in cands if c.protected)
    cap = int(budget * PROTECTED_MAX)
    for i in sorted(ev_map, key=lambda i: (-ev_map[i], i))[:EVIDENCE_MAX]:
        c = next((k for k in cands if k.kind == "message" and k.index == i), None)
        if c is None or c.protected or booked + c.tokens > cap:
            continue
        booked += c.tokens
        c.protected = True
        c.why = (c.why + ["evidence"])[:6]
    return cands, {"offset": offset, "goff": goff, "tail_start": tail_start,
                   "norm_count": len(msgs)}


def compile_turn(dialect, body, analysis, graph=None, query="", budget=None,
                 body_sha=None, enforce=None):
    """Compile one turn. Returns (new_body|None, decisions, proof).

    `decisions` keeps the per-slot vocabulary (slot/backend/dropped/savings_est_tok/
    candidates/applied) so report, dataset export and the label loop are unchanged —
    each entry additionally carries `compiled: true` and the shared `budget_tok`.

    `enforce` is the set of slots allowed to touch the wire (I1). The plan is computed
    either way — observation mode shows what *would* be cut — but only slots named
    there rewrite the body. `applied` reports the rewrite, never the intention.
    """
    if budget is None:
        budget = default_budget(analysis)
    cands, meta = build_candidates(body, analysis, graph, query, budget)
    if not cands:
        return None, [], None
    p = plan(cands, budget)
    kept_files = [c for c in p["kept"] if c.kind == "file" and "already-in-tail" not in c.why]

    proof = _proof(p, meta, budget, kept_files, body_sha)
    if p["over_budget"]:
        proof["applied"] = False
        return None, [], proof

    # What the plan *could* change, before the enforcement filter (I1).
    would = set()
    if [c for c in p["dropped"] if c.kind == "tool"]:
        would.add("tool_gate")
    if [c for c in p["dropped"] if c.kind == "message"]:
        would.add("compact")
    if kept_files:
        would.add("context")
    applied = would if enforce is None else (would & set(enforce))
    new_body, mutated = _apply(dialect, body, p, kept_files, applied, meta)
    decisions = _decisions(p, budget, kept_files)
    for d in decisions:
        d["applied"] = d["slot"] in mutated
    proof["applied"] = bool(mutated)
    return (new_body if mutated else None), decisions, proof


def _apply(dialect, body, p, kept_files, applied, meta):
    """Rebuild the request from the plan, touching only slots in `applied`."""
    mutated = set()
    out = dict(body)
    drop_norm = set(c.index for c in p["dropped"] if c.kind == "message")
    if drop_norm and "compact" in applied:
        _drop_messages(out, body, drop_norm, meta)
        mutated.add("compact")
    tools = body.get("tools") or []
    if tools and "tool_gate" in applied and [c for c in p["dropped"] if c.kind == "tool"]:
        out["tools"] = protocol.filter_tools(
            tools, set(c.id for c in p["kept"] if c.kind == "tool"))
        mutated.add("tool_gate")
    if kept_files and "context" in applied:
        note = FILE_NOTE_HEADER + "\n" + "\n".join(c.id for c in kept_files)
        candidate, injected = protocol.append_after_prefix(out, note)
        if injected:
            out = candidate
            mutated.add("context")
    return (out if mutated else None), mutated


def _drop_messages(out, body, drop_norm_idx, meta):
    """Map normalized indices back onto whichever message list this dialect uses.

    `normalize_messages` prepends a top-level `system` entry (Anthropic shape), so
    normalized indices sit `offset` ahead of the body's own list — the same convention
    the per-slot compact path uses, resolved in one place (build_candidates).
    """
    if isinstance(body.get("messages"), list):
        drop = set(i - meta["offset"] for i in drop_norm_idx if i - meta["offset"] >= 0)
        out["messages"] = [m for i, m in enumerate(body["messages"]) if i not in drop]
        return
    if isinstance(body.get("contents"), list):  # Gemini: 1:1 with normalized order
        drop = set(i - meta["goff"] for i in drop_norm_idx if i - meta["goff"] >= 0)
        out["contents"] = [c for i, c in enumerate(body["contents"]) if i not in drop]


def _decisions(p, budget, kept_files):
    by_kind = {}
    for c in p["kept"] + p["dropped"]:
        by_kind.setdefault(c.kind, []).append(c)
    out = []
    tools = by_kind.get("tool") or []
    if tools:
        dropped = [c.id for c in tools if not c.keep]
        out.append({
            "slot": "tool_gate", "backend": "compiled", "compiled": True,
            "budget_tok": budget, "dropped": dropped,
            "kept": len(tools) - len(dropped),
            "savings_est_tok": sum(c.tokens for c in tools if not c.keep),
            "candidates": [c.as_decision() for c in tools],
            "decision_ms": 0.0,
        })
    msgs = by_kind.get("message") or []
    if msgs:
        dropped = [c for c in msgs if not c.keep]
        out.append({
            "slot": "compact", "backend": "compiled", "compiled": True,
            "budget_tok": budget, "dropped_count": len(dropped),
            "savings_est_tok": sum(c.tokens for c in dropped),
            "candidates": [c.as_decision() for c in msgs],
            "decision_ms": 0.0,
        })
    files = by_kind.get("file") or []
    if files:
        out.append({
            "slot": "context", "backend": "compiled+graph", "compiled": True,
            "budget_tok": budget,
            "dropped": [c.id for c in files if not c.keep],
            "candidates": [c.as_decision() for c in files],
            "top": [c.id for c in files if c.keep][:5],
            "cost_est_tok": sum(c.tokens for c in kept_files),
            "decision_ms": 0.0,
        })
    return out


def _proof(p, meta, budget, kept_files, body_sha):
    """Machine-readable record of what survived, what was cut and how to get it back."""
    pool = p["kept"] + p["dropped"]
    tail = [c.id for c in pool if c.kind == "message" and c.index >= meta["tail_start"]]
    evidence = [c.id for c in pool if c.kind == "message" and c.protected
                and c.index < meta["tail_start"]]
    return {
        "budget": budget,
        "over_budget": p["over_budget"],
        "reason": p["reason"],
        "tokens_before": p["tokens_before"],
        "tokens_after": p["tokens_after"],
        "protected_tokens": p["protected_tokens"],
        "floor_value_per_tok": None if p["floor_ratio"] is None else round(p["floor_ratio"], 6),
        "kept": [{"kind": c.kind, "id": c.id, "tokens": c.tokens,
                  "value": round(c.value, 3)} for c in p["kept"]],
        "dropped": [{"kind": c.kind, "id": c.id, "tokens": c.tokens,
                     "reason": _drop_reason(c) if not p["over_budget"] else p["reason"],
                     "reversible": {"body_sha": body_sha, "index": c.index,
                                    "pointer": "%s:%s" % (c.kind, c.id)}}
                    for c in p["dropped"]],
        "never_dropped": {"tail_indices": tail, "evidence_indices": evidence,
                          "core_tools": [c.id for c in p["kept"]
                                         if c.kind == "tool" and c.protected],
                          "cached_prefix": "system and tools-prefix bytes are not candidates (I2)"},
        "files_surfaced": [c.id for c in kept_files],
        "slots": ["tool_gate", "compact", "context"],
    }
