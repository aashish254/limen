"""Registry: turn config into "which System One answers my slot questions?".

Selection precedence (highest first):

1. an explicit URL in ``SUBPROTO_MODEL`` — any classifier, no code change needed
2. a named entry in ``REGISTRY`` (``SUBPROTO_MODEL=openjev``), whose URL comes from
   that entry's own env var, then ``SUBPROTO_MODEL_URL``, then the legacy
   ``laya_url``/``LAYA_URL`` for the laya entry
3. no selection but a legacy ``laya_url``/``LAYA_URL`` present → the laya adapter,
   which is what happened before the registry existed
4. anything unknown, disabled, or without a URL → ``None`` and the engine's own
   heuristics, so a missing model degrades to a working proxy instead of an error

A slot may pick its own model with ``SUBPROTO_MODEL_<SLOT>`` (e.g.
``SUBPROTO_MODEL_COMPACT=djev``) or a ``model_by_slot`` map in the config file;
unset slots fall through to the global selection above.

Per-slot *and* versioned selection — the ``models.json`` ladder that decides which
checkpoint answers, and whether it is alive — is ``versions.select_slots``. This module
only turns a name into an endpoint.
"""

import os

from .base import HTTPScoreAdapter

HEURISTIC = "heuristic"

# A model becomes unsupported by this list, never by this file: anything that
# speaks /health + /score is reachable through branch 1 without a release.
REGISTRY = {
    "laya": {
        "label": "laya",
        "env": "LAYA_URL",
        "about": "open ~421M decision model (convaiinnovations/laya)",
    },
    "openjev": {
        "label": "openjev",
        "env": "OPENJEV_URL",
        "about": "open Jev-family fast classifier",
    },
    "djev": {
        "label": "djev",
        "env": "DJEV_URL",
        "about": "open distilled decision classifier",
    },
    "semif": {
        "label": "semif",
        "env": "SEMIF_URL",
        "about": "open sentence-level relevance classifier",
    },
    "mlx_lora": {
        "label": "mlx_lora",
        "env": "SUBPROTO_MLX_URL",
        "about": "locally fine-tuned quantized adapter behind a local runner",
    },
}

DISABLED = ("heuristic", "none", "off", "passthrough")

# Selectable by name; a URL is selectable too, and needs no entry here.
ADAPTERS = ("laya", "openjev", "djev", "semif", "mlx_lora")

# Only these slots ask a model a question today. `context` is answered by the
# code graph and `effort` by request-shape heuristics, so pinning a model to
# them would record a label that never produced the decision.
MODEL_SLOTS = ("tool_gate", "compact")


def slot_env(slot):
    return "SUBPROTO_MODEL_" + slot.upper()


def _slot_name(slot, config):
    """A per-slot override: SUBPROTO_MODEL_TOOL_GATE, then model_by_slot."""
    raw = os.environ.get(slot_env(slot))
    if raw and raw.strip():
        return raw.strip()
    by_slot = getattr(config, "model_by_slot", None) or {}
    return (by_slot.get(slot) or "").strip()


def configured_url(name, config):
    """Where a named registry model is being served, or None when it is not."""
    entry = REGISTRY.get(name) or {}
    env = entry.get("env")
    if env and os.environ.get(env):
        return os.environ[env]
    if getattr(config, "model_url", None):
        return config.model_url
    if name == "laya":
        return getattr(config, "laya_url", None)
    return None


def _select(name, config):
    """Resolve one selection string to (adapter_or_None, label)."""
    if not name:
        legacy = getattr(config, "laya_url", None)
        if legacy:
            return HTTPScoreAdapter(legacy, label="laya"), "laya"
        return None, HEURISTIC
    if name.lower() in DISABLED:
        return None, HEURISTIC
    if "://" in name:
        return HTTPScoreAdapter(name, label="model"), "model"
    key = name.lower()
    if key not in REGISTRY:
        return None, HEURISTIC
    label = REGISTRY[key]["label"]
    url = configured_url(key, config)
    if not url:
        return None, HEURISTIC
    return HTTPScoreAdapter(url, label=label), label


def resolve(config, slot=None):
    """Return (adapter_or_None, label) — the label is what slots record.

    With a `slot`, that slot's override wins and the global selection is the
    fallback, so one model may answer every slot or each slot may pick its own.
    """
    name = _slot_name(slot, config) if slot else ""
    if not name:
        name = (getattr(config, "model", None) or "").strip()
    return _select(name, config)


def configured_adapters(config):
    """{label: adapter} for every named backend that has an endpoint right now.

    These are the candidates a Router may choose between. `heuristic` is not
    included: it is always available as the floor, not a thing that needs wiring.
    """
    out = {}
    for key in ADAPTERS:
        url = configured_url(key, config)
        if url:
            label = REGISTRY[key]["label"]
            out[label] = HTTPScoreAdapter(url, label=label)
    return out


def is_pinned(config, slot):
    """True when a slot's backend was named explicitly, so the Router must not touch it."""
    return bool(_slot_name(slot, config))


def status(config):
    """Describe every selectable backend, and which slot uses which."""
    adapter, active = resolve(config)
    per_slot = dict((slot, resolve(config, slot=slot)[1]) for slot in MODEL_SLOTS)
    rows = [{
        "name": "heuristic",
        "about": "in-process lexical heuristics; always available",
        "env": None,
        "url": None,
        "where": "in-process",
        "configured": True,
        "active": active == HEURISTIC,
    }]
    for key in ADAPTERS:
        entry = REGISTRY[key]
        url = configured_url(key, config)
        rows.append({
            "name": key,
            "about": entry["about"],
            "env": entry["env"],
            "url": url,
            "where": url or "not configured — export %s=http://host:port" % entry["env"],
            "configured": bool(url),
            "active": active == entry["label"] and adapter is not None,
            "slots": sorted(s for s, lbl in per_slot.items()
                            if lbl == entry["label"] and adapter is not None) or None,
        })
    return {"active": active, "slots": per_slot, "adapters": rows}
