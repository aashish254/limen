"""Router: pick the System One adapter per slot from measured evidence.

Manual selection (`SUBPROTO_MODEL[_<slot>]`) names one model and *hopes* it is good.
The Router instead reads what the system has actually measured — per-adapter slot
precision (`bench/ablation.py`) and, when supplied, per-backend decision latency
(`decision_ms`) — and hands each un-pinned slot's question to the best-scoring
**configured** adapter within an optional latency budget.

Honesty (invariant I6): an adapter with no measured precision is never auto-picked
as "best", and precision ties route to `heuristic` (in-process, no endpoint, no
network hop). On today's synthetic evidence — where the bundled stand-in ties the
heuristic at Δ0 — that means the Router correctly stays on heuristics instead of
inventing an edge for a model we have not actually measured beating the baseline.

Opt-in via `SUBPROTO_ROUTER=on`; explicit per-slot pins are always respected, and a
Router with nothing to rank degrades to the manual registry path rather than failing.
"""

import json
import os

from . import registry
from .registry import MODEL_SLOTS, configured_adapters, is_pinned, resolve, slot_env

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_ABLATION_JSON = os.path.join(_REPO, "bench", "ablation.json")

HEURISTIC = "heuristic"


def load_evidence(path=None):
    """{label: {precision, latency_ms, source}} read from an ablation results file.

    `bench/ablation.py` writes precision per adapter against labelled ground truth;
    that committed artifact is the Router's measured input. A missing or unreadable
    file yields `{}` — which the Router treats as "nothing to rank" and degrades to
    manual selection, so an absent evidence file never silently changes behaviour.
    """
    target = path or DEFAULT_ABLATION_JSON
    try:
        with open(target) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    evidence = {}
    heuristic = data.get("heuristic") or {}
    if heuristic.get("precision") is not None:
        evidence[HEURISTIC] = {"precision": float(heuristic["precision"]),
                               "latency_ms": 0.0,
                               "source": "ablation:heuristic-baseline"}
    for adapter in data.get("adapters") or []:
        if adapter.get("precision") is None or not adapter.get("name"):
            continue
        latency = adapter.get("latency_ms")
        evidence[adapter["name"]] = {
            "precision": float(adapter["precision"]),
            "latency_ms": float(latency) if latency is not None else None,
            "source": "ablation:%s" % (adapter.get("source") or "measured"),
        }
    return evidence


def rank(candidates, evidence, budget_ms=None):
    """Order *measured* candidates best-first; drop those over the latency budget.

    Sorting is deterministic and honesty-first: highest precision wins, ties prefer
    `heuristic` (the cheaper path), then lower known latency, then the label. An
    adapter absent from `evidence` is not ranked at all — an unmeasured model cannot
    be "best" on evidence it has never produced.
    """
    rows = []
    for label in candidates:
        entry = evidence.get(label)
        if not entry or entry.get("precision") is None:
            continue
        latency = entry.get("latency_ms")
        if budget_ms is not None and latency is not None and latency > budget_ms:
            continue
        rows.append({"label": label, "precision": float(entry["precision"]),
                     "latency_ms": latency, "source": entry.get("source")})
    rows.sort(key=lambda r: (
        -r["precision"],
        0 if r["label"] == HEURISTIC else 1,
        r["latency_ms"] if r["latency_ms"] is not None else float("inf"),
        r["label"],
    ))
    return rows


class Router:
    """Choose an adapter per slot from configured backends + measured evidence."""

    def __init__(self, config, evidence=None, budget_ms=None):
        self.config = config
        self.enabled = bool(getattr(config, "router", False))
        self.adapters = configured_adapters(config)
        self.evidence = (evidence if evidence is not None
                         else load_evidence(getattr(config, "router_evidence", None)))
        self.budget_ms = (budget_ms if budget_ms is not None
                          else getattr(config, "router_budget_ms", None))

    @property
    def available(self):
        return self.enabled

    @property
    def candidates(self):
        """Every backend with a live endpoint, plus the always-available heuristic."""
        return sorted(set(self.adapters) | {HEURISTIC})

    def select(self, slot):
        """Return (adapter_or_None, label, reason) for one un-pinned slot."""
        ranking = rank(self.candidates, self.evidence, self.budget_ms)
        if not ranking:
            adapter, label = resolve(self.config, slot=slot)
            return adapter, label, "no measured evidence to rank; manual selection"
        best = ranking[0]
        label = best["label"]
        budget = "" if self.budget_ms is None else " under %sms" % self.budget_ms
        if label == HEURISTIC:
            return None, HEURISTIC, (
                "no configured adapter beats the heuristics' measured precision "
                "(%.4f)%s; no endpoint hop spent" % (best["precision"], budget))
        return self.adapters[label], label, (
            "routed to %s: best measured precision %.4f (%s)%s"
            % (label, best["precision"], best.get("source") or "?", budget))

    def plan(self):
        """{slot: {mode, label, reason}} describing every model-backed slot.

        `pinned` = a human named this slot's model and routing will not touch it;
        `manual` = the router is off, so the registry's normal selection stands;
        `routed` = the router chose from evidence (or degraded to manual with no evidence).
        """
        out = {}
        for slot in MODEL_SLOTS:
            if is_pinned(self.config, slot):
                _, label = resolve(self.config, slot=slot)
                out[slot] = {"mode": "pinned", "label": label,
                             "reason": "explicit %s" % slot_env(slot)}
            elif not self.enabled:
                _, label = resolve(self.config, slot=slot)
                out[slot] = {"mode": "manual", "label": label,
                             "reason": "router off; manual selection"}
            else:
                _, label, reason = self.select(slot)
                out[slot] = {"mode": "routed", "label": label, "reason": reason}
        return out
