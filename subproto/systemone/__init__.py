"""The System One adapter seam (ROADMAP §1 / SPEC FR-4).

Every decision slot asks the same kind of question — "which of these options
survives?" — and any small classifier that can answer it in probability form can
drive the engine. This package defines that contract and the registry that picks
an implementation from config, so the model behind the decisions is a
configuration choice rather than an architectural bet.

`versions` sits on top of that: the same question, but asked of a *checkpoint*
rather than a family, so a fine-tune can be selected, health-gated, rolled back,
and told apart from its predecessor in the telemetry.
"""

from .base import DEFAULT_TIMEOUT_S, HTTPScoreAdapter, ModelAdapter, timeout_for
from .registry import (ADAPTERS, MODEL_SLOTS, REGISTRY, configured_adapters,
                       configured_url, is_pinned, resolve, slot_env, status)
from .router import Router, load_evidence, rank
from .versions import (HEURISTIC, MANIFEST_NAME, TIERS, add, by_label, describe, load, path,
                       rollback, save, select, select_from_manifest, select_slots,
                       use, version_of)

__all__ = ["ModelAdapter", "HTTPScoreAdapter", "DEFAULT_TIMEOUT_S", "timeout_for",
           "REGISTRY", "ADAPTERS", "MODEL_SLOTS",
           "resolve", "status", "configured_url", "configured_adapters",
           "slot_env", "is_pinned", "Router", "load_evidence", "rank",
           "HEURISTIC", "MANIFEST_NAME", "TIERS", "add", "by_label", "describe", "load", "path",
           "rollback", "save", "select", "select_from_manifest", "select_slots", "use",
           "version_of"]
