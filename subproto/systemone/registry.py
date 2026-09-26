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


def resolve(config):
    """Return (adapter_or_None, label) — the label is what slots record."""
    name = (getattr(config, "model", None) or "").strip()
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


def status(config):
    """Describe every selectable backend for `subproto models`."""
    adapter, active = resolve(config)
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
        })
    return {"active": active, "adapters": rows}
