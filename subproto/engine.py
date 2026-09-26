"""Decision engine: wires each slot to a backend and, optionally, to the wire.

Slots run in observation mode by default — they record what *would* be dropped —
so the telemetry corpus is honest before any behaviour change is trusted. Apply
a slot explicitly with SUBPROTO_APPLY=tool_gate,compact,context.
"""

import os
import time

from . import compiler
from . import graph as graph_mod
from . import heuristics
from . import protocol
from . import systemone

ALL_SLOTS = ("tool_gate", "compact", "context", "effort")
DEFAULT_APPLY = ("tool_gate", "compact")
# Slots that only ever record a recommendation. Naming one in SUBPROTO_APPLY changes
# no bytes, so the CLI says so instead of letting the meter imply an intervention.
ADVISORY_SLOTS = ("effort",)
SLOTS_THAT_EDIT = ("tool_gate", "compact", "context")


def _now_ms():
    return time.time() * 1000.0


def compile_enabled():
    """v5's joint optimizer is opt-in (I1): SUBPROTO_COMPILE=on|1|true|yes."""
    return (os.environ.get("SUBPROTO_COMPILE") or "").strip().lower() in (
        "1", "on", "true", "yes")


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
        self.backends = {}
        self.manifest = systemone.load(systemone.path(config))
        global_choice = systemone.select(config, manifest=self.manifest)
        self.backend, self.backend_label = global_choice["adapter"], global_choice["label"]
        # The version that answers *this* engine, per decision — a label can be reused
        # across checkpoints, and "was the fine-tune better" needs the checkpoint.
        self.model_version = global_choice["version"]
        self.model_source = global_choice["source"]
        self.model_notes = list(global_choice["notes"])
        slot_backends, self.slot_versions, slot_notes = systemone.select_slots(
            config, manifest=self.manifest)
        self.backends.update(slot_backends)
        for note in slot_notes:
            if note not in self.model_notes:
                self.model_notes.append(note)
        # Set when a compiled plan raised and we degraded; surfaced, never hidden.
        self.compile_error = None
        self.router = systemone.Router(config, manifest=self.manifest) \
            if getattr(config, "router", False) else None
        self.router_reasons = {}
        if self.router is not None and self.router.available:
            for slot in systemone.MODEL_SLOTS:
                if systemone.is_pinned(config, slot):
                    continue
                adapter, label, reason = self.router.select(slot)
                self.backends[slot] = (adapter, label)
                self.slot_versions[slot] = systemone.version_of(self.manifest, label)
                self.router_reasons[slot] = reason

    def slot_backend(self, slot):
        """The adapter (and its label) that answers for one slot."""
        return self.backends.get(slot, (None, "heuristic"))

    def version_for(self, backend):
        """The version stamp for whatever answered a decision.

        Follows the answer, not the wish: after a health-gated fallback the decision says
        `heuristic`, because the heuristic is what produced it. Stamping the model that
        *should* have answered would break the one comparison this column exists for.
        """
        return systemone.version_of(self.manifest, backend or self.backend_label)

    def model_status(self):
        """Health of the active System One backend, labelled with its own name."""
        slots = dict((s, lbl) for s, (a, lbl) in self.backends.items())
        status = {"configured": bool(self.backend and self.backend.available),
                  "label": self.backend_label, "version": self.model_version,
                  "source": self.model_source, "slots": slots,
                  "slot_versions": dict(self.slot_versions), "graph": bool(self.graph),
                  "compiled": compile_enabled(),
                  "manifest": {"active": self.manifest.get("active"),
                               "previous": self.manifest.get("previous"),
                               "registered": len(self.manifest.get("versions") or [])}}
        if self.model_notes:
            status["notes"] = list(self.model_notes)
        if self.compile_error:
            status["compile_error"] = self.compile_error
        if self.backend is not None and self.backend.available:
            status.update(self.backend.health())
            status["slots"] = slots
        if self.router is not None:
            status["router"] = {"enabled": self.router.available,
                                "budget_ms": self.router.budget_ms,
                                "evidence": sorted(self.router.evidence),
                                "plan": self.router.plan()}
        return status

    def decide(self, dialect, body, analysis, config, body_sha=None):
        """One turn of System One. Returns (new_body|None, decisions).

        Every decision leaves here stamped with the `model_version` that answered it —
        both arms, and the heuristic answers too, so a version is comparable across the
        whole corpus rather than only where a model happened to be wired in.
        """
        apply_set = _apply_set()
        out = None
        if compile_enabled():
            try:
                out = self._decide_compiled(dialect, body, analysis, apply_set, body_sha)
                self.compile_error = None
            except Exception as exc:
                # Never bet the request on one component: a failed plan degrades to the
                # per-slot path, and the failure is recorded rather than swallowed.
                out = None
                self.compile_error = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        if out is None:
            out = self._decide_slots(dialect, body, analysis, config, apply_set)
        new_body, decisions = out
        for d in decisions:
            d["model_version"] = self.version_for(d.get("backend"))
        return new_body, decisions

    def _decide_compiled(self, dialect, body, analysis, apply_set, body_sha):
        """v5: tool_gate/compact/context resolve from one plan over one budget (S22)."""
        _t0 = _now_ms()
        idx, last_user = _last_user(body, dialect)
        est_in = (analysis or {}).get("est_in_tok") or 0
        new_body, decisions, proof = compiler.compile_turn(
            dialect, body, analysis, graph=self.graph,
            query=last_user or (analysis or {}).get("query", ""),
            body_sha=body_sha, enforce=apply_set & set(SLOTS_THAT_EDIT))
        ms = round(_now_ms() - _t0, 3)
        if not decisions:
            # Nothing to plan (empty pool) or the protected set alone blew the budget.
            # Either way the turn must still be legible in the telemetry, and `applied`
            # has to say false: no bytes changed (I6).
            decisions = [{"slot": "compact", "backend": "compiled", "compiled": True,
                          "applied": False, "dropped_count": 0, "savings_est_tok": 0,
                          "over_budget": bool(proof and proof.get("over_budget")),
                          "candidates": [], "decision_ms": ms}]
        else:
            decisions[0]["decision_ms"] = ms
        if proof is not None:
            # A turn-level artifact, and the decisions blob is the only column the
            # telemetry already persists — so the proof rides on the first decision and
            # `subproto show <request_id>` can resolve a drop's pointer from the DB.
            decisions[0]["proof"] = proof
        effort = self._effort_decision(analysis, last_user, est_in)
        if effort:
            decisions.append(effort)
        return new_body, decisions

    def _effort_decision(self, analysis, last_user, est_in):
        """Advisory-only (FR-3d): records a tier, never rewrites the request."""
        if not analysis:
            return None
        _t0 = _now_ms()
        route = heuristics.route_effort(analysis, last_user, est_in)
        return {
            "slot": "effort", "backend": "heuristic", "applied": False,
            "tier": route["tier"], "small_model_ok": route["small_model_ok"],
            "reasons": route["reasons"], "savings_est_tok": 0,
            "decision_ms": round(_now_ms() - _t0, 3),
        }

    def _decide_slots(self, dialect, body, analysis, config, apply_set):
        decisions = []
        # A slot is `applied` only if it actually rewrote the body we forward; the
        # flag is set once, from this set, at the end of the turn.
        mutated = set()
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
                new_body["tools"] = protocol.filter_tools(tools, keep)
                mutated.add("tool_gate")

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
                mutated.add("compact")

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
                    # I2: the note must land *after* the cached prefix. Writing into
                    # the top-level `system` string rewrites the prefix itself, which
                    # busts the provider cache and bills a fresh read every turn — so
                    # it is appended to the end of the conversation instead.
                    candidate, injected = protocol.append_after_prefix(new_body or body, add)
                    if injected:
                        new_body = candidate
                        mutated.add("context")

        effort = self._effort_decision(analysis, last_user, est_in)
        if effort:
            decisions.append(effort)

        # One source of truth for `applied`: the slot rewrote the body we forward.
        # A slot named in SUBPROTO_APPLY that found nothing to do — or that this
        # dialect makes impossible — is not an intervention, and the meter must not
        # claim it was (I6).
        for d in decisions:
            d["applied"] = d["slot"] in mutated
        return new_body, decisions
