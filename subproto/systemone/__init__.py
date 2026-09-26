"""The System One adapter seam (ROADMAP §1 / SPEC FR-4).

Every decision slot asks the same kind of question — "which of these options
survives?" — and any small classifier that can answer it in probability form can
drive the engine. This package defines that contract and the registry that picks
an implementation from config, so the model behind the decisions is a
configuration choice rather than an architectural bet.
"""

from .base import HTTPScoreAdapter, ModelAdapter
from .registry import (ADAPTERS, MODEL_SLOTS, REGISTRY, configured_url, resolve,
                       resolve_all, slot_env, status)

__all__ = ["ModelAdapter", "HTTPScoreAdapter", "REGISTRY", "ADAPTERS", "MODEL_SLOTS",
           "resolve", "resolve_all", "status", "configured_url", "slot_env"]
