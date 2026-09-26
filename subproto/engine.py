"""Decision engine: wires each slot to a backend and, optionally, to the wire.

Slots run in observation mode by default — they record what *would* be dropped —
so the telemetry corpus is honest before any behaviour change is trusted. Apply
a slot explicitly with SUBPROTO_APPLY=tool_gate,compact,context.
"""

import os
import time

from . import graph as graph_mod
from . import heuristics
from . import protocol
from . import systemone

ALL_SLOTS = ("tool_gate", "compact", "context", "effort")
DEFAULT_APPLY = ("tool_gate", "compact")


def _now_ms():
    return time.time() * 1000.0


def _apply_set():
    raw = os.environ.get("SUBPROTO_APPLY")
    if raw is None:
        return set(DEFAULT_APPLY) if os.environ.get("SUBPROTO_ENFORCE") else set()
    return set(x.strip() for x in raw.split(",") if x.strip() in ALL_SLOTS)


def _ask_backend(client, state, options, key_fn, threshold=0.5):
    """Score candidates with the active System One adapter; {option: prob} or None."""
    if client is None or not client.available:
        return None
    probs = client.score(state, "keep", [key_fn(o) for o in options],
                         cache_key=state[:64])
    return probs


def _last_user(body, dialect):
    msgs = protocol.normalize_messages(body)
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i][2] == "user":
            return i, msgs[i][1]
    return (len(msgs) - 1 if msgs else -1), (msgs[-1][1] if msgs else "")


def _filter_tools(tools, keep):
    """Rebuild the request's tools list keeping only names in `keep` (lowercased).

    Preserves each dialect's shape: OpenAI/Anthropic tools are flat entries, while
    Gemini packs declarations under one wrapper — so we filter the inner list and
    drop the wrapper entirely once it empties.
    """
    out = []
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("functionDeclarations"), list):
            kept = [
                d for d in t["functionDeclarations"]
                if protocol.tool_name(d).lower() in keep
            ]
            if kept:
                out.append(dict(t, functionDeclarations=kept))
            continue
        if protocol.tool_name(t).lower() in keep:
            out.append(t)
    return out


class Engine:
    def __init__(self, config, graph_path=None):
        self.config = config
        self.graph = None
        self.graph_path = graph_path
        if graph_path and os.path.exists(graph_path):
            self.graph = graph_mod.load(graph_path)
        elif os.environ.get("SUBPROTO_GRAPH"):
            p = os.path.abspath(os.environ["SUBPROTO_GRAPH"])
            if os.path.exists(p):
                self.graph = graph_mod.load(p)
                self.graph_path = p
        if self.graph is None:
            cand = os.path.join(os.getcwd(), ".subproto-graph.json")
            if os.path.exists(cand):
                self.graph = graph_mod.load(cand)
                self.graph_path = cand
        self.backends = systemone.resolve_all(config)
        self.backend, self.backend_label = systemone.resolve(config)
        self.router = systemone.Router(config) if getattr(config, "router", False) else None
        self.router_reasons = {}
        if self.router is not None and self.router.available:
            for slot in systemone.MODEL_SLOTS:
                if systemone.is_pinned(config, slot):
                    continue
                adapter, label, reason = self.router.select(slot)
                self.backends[slot] = (adapter, label)
                self.router_reasons[slot] = reason

    def slot_backend(self, slot):
        """The adapter (and its label) that answers for one slot."""
        return self.backends.get(slot, (None, "heuristic"))

    def model_status(self):
        """Health of the active System One backend, labelled with its own name."""
        slots = dict((s, lbl) for s, (a, lbl) in self.backends.items())
        status = {"configured": bool(self.backend and self.backend.available),
                  "label": self.backend_label, "slots": slots, "graph": bool(self.graph)}
        if self.backend is not None and self.backend.available:
            status.update(self.backend.health())
            status["slots"] = slots
        if self.router is not None:
            status["router"] = {"enabled": self.router.available,
                                "budget_ms": self.router.budget_ms,
                                "evidence": sorted(self.router.evidence),
                                "plan": self.router.plan()}
        return status

    def decide(self, dialect, body, analysis, config):
        apply_set = _apply_set()
        decisions = []
        new_body = None
        est_in = analysis.get("est_in_tok") or 0
        idx, last_user = _last_user(body, dialect)
        msgs_norm = protocol.normalize_messages(body)
        tools = body.get("tools") or []

        flat_tools = protocol.flatten_tools(tools)
        if flat_tools:
            _t0 = _now_ms()
            tg_adapter, tg_label = self.slot_backend("tool_gate")
            hs = heuristics.tool_gate(flat_tools, last_user or analysis.get("query", ""))
            probs = _ask_backend(tg_adapter, last_user[:512], flat_tools,
                                 lambda t: protocol.tool_name(t).lower())
            backend = tg_label if probs else "heuristic"
            if probs:
                for d in hs:
                    if d["target"] in probs:
                        d["score"] = round(probs[d["target"]], 3)
                        d["keep"] = probs[d["target"]] >= 0.5 or "core" in (d["why"] or [])
            for d in hs:
                d.setdefault("backend", backend)
            dropped = [d["target"] for d in hs if not d["keep"]]
            saved_chars = sum(t.get("_spec_chars", 0) for t in flat_tools
                              if protocol.tool_name(t).lower() in set(dropped))
            decisions.append({
                "slot": "tool_gate", "backend": backend, "applied": "tool_gate" in apply_set,
                "dropped": dropped, "kept": len(hs) - len(dropped),
                "savings_est_tok": int(saved_chars / 3.6),
                "candidates": hs,
                "decision_ms": round(_now_ms() - _t0, 3),
            })
            if "tool_gate" in self.router_reasons:
                decisions[-1]["router"] = self.router_reasons["tool_gate"]
            if "tool_gate" in apply_set and dropped:
                keep = set(d["target"] for d in hs if d["keep"])
                new_body = dict(new_body or body)
                new_body["tools"] = _filter_tools(tools, keep)

        if est_in > 4000 and len(msgs_norm) > 8:
            _t0 = _now_ms()
            budget = max(1500, int(est_in * 0.45))
            flags = heuristics.compact_messages(msgs_norm, budget)
            backend = "heuristic"
            cp_adapter, cp_label = self.slot_backend("compact")
            if cp_adapter and cp_adapter.available:
                probs = cp_adapter.score(
                    last_user[:512],
                    "keep",
                    [f["target"] for f in flags if not f["keep"] or f["index"] < len(flags) - 4],
                    cache_key=None)
                if probs:
                    backend = cp_label
                    for f in flags:
                        if f["target"] in probs:
                            f["score"] = round(probs[f["target"]], 3)
                            f["keep"] = probs[f["target"]] >= 0.45
            dropped_idx = [f["index"] for f in flags if not f["keep"]]
            saved_tok = sum(f["tokens"] for f in flags if not f["keep"])
            decisions.append({
                "slot": "compact", "backend": backend, "applied": "compact" in apply_set,
                "dropped_count": len(dropped_idx), "savings_est_tok": saved_tok,
                "budget_tok": budget, "candidates": flags,
                "decision_ms": round(_now_ms() - _t0, 3),
            })
            if "compact" in self.router_reasons:
                decisions[-1]["router"] = self.router_reasons["compact"]
            if "compact" in apply_set and dropped_idx:
                # normalize_messages prepends a top-level `system` entry (Anthropic
                # shape), so normalized indices sit one ahead of body["messages"].
                offset = 1 if isinstance(body.get("system"), str) and body.get("system") else 0
                drop = set(i - offset for i in dropped_idx if i - offset >= 0)
                new_body = dict(new_body or body)
                new_body["messages"] = [
                    m for i, m in enumerate(body.get("messages") or []) if i not in drop]

        if self.graph is not None:
            _t0 = _now_ms()
            query = last_user or analysis.get("query", "")
            hits = graph_mod.search(self.graph, query, top_k=10)
            ds = heuristics.file_drop_scores(query, hits)
            if ds:
                decisions.append({
                    "slot": "context", "backend": "graph+heuristic",
                    "applied": "context" in apply_set,
                    "dropped": [d["target"] for d in ds if not d["keep"]],
                    "candidates": ds,
                    "top": [d["target"] for d in ds[:5]],
                    "cost_est_tok": 0,
                    "decision_ms": round(_now_ms() - _t0, 3),
                })
                if "context" in apply_set:
                    add = ("Relevant files in this repo, ranked by a local code graph "
                           "(not read yet):\n" + "\n".join("- " + d["target"] for d in ds[:6]))
                    new_body = dict(new_body or body)
                    sys_ = new_body.get("system")
                    if isinstance(sys_, str):
                        new_body["system"] = sys_ + "\n\n" + add
                    elif isinstance(new_body.get("messages"), list) and new_body["messages"]:
                        first = new_body["messages"][0]
                        if isinstance(first.get("content"), list):
                            new_body["messages"] = [dict(first, content=first["content"] + [
                                {"type": "text", "text": add}])] + new_body["messages"][1:]

        if analysis:
            _t0 = _now_ms()
            route = heuristics.route_effort(analysis, last_user, est_in)
            decisions.append({
                "slot": "effort", "backend": "heuristic", "applied": False,
                "tier": route["tier"], "small_model_ok": route["small_model_ok"],
                "reasons": route["reasons"], "savings_est_tok": 0,
                "decision_ms": round(_now_ms() - _t0, 3),
            })

        return new_body, decisions
