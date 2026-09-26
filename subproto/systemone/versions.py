"""Model versions: what answered each decision, and how to put it back.

A decision records a backend *label*, which is enough to say "a model answered" but not
enough to answer the question ROADMAP v4 actually asks — *was the fine-tune better than
the foundation model it replaced?* For that a version has to exist as a first-class
thing: selectable, health-gated, roll-back-able, and stamped on every decision it made.

Selection precedence (explicit first, always — invariant I1: nothing recorded here may
change behaviour a human did not ask for):

1. ``SUBPROTO_MODEL[_<SLOT>]`` / config ``model`` / a legacy ``LAYA_URL`` — a live
   selection, so the manifest stands down. It is still health-checked, because a dead
   pin used to degrade to the heuristics *silently*, one decision at a time.
2. ``models.json``'s ``active`` version — **but only if its ``/health`` answers**. A dead
   endpoint is not a backend, so resolution falls to ``previous``, then the heuristics,
   with the reason carried in the status rather than lost.
3. Neither → the heuristics.

The manifest holds ``versions: [{label, url, tier, added, note, version}]`` plus
``active`` and ``previous``. `label` is the key; `version` defaults to it, so two LoRA
checkpoints can be `personal` tier, labels `mlx-lora-v1`/`mlx-lora-v2`, and every
decision says which one produced it.
"""

import json
import os
import time

from . import registry
from .base import HTTPScoreAdapter

MANIFEST_NAME = "models.json"
# Two tiers is the whole vocabulary today: a model you point at, and a model you trained.
# Anything else is kept verbatim and warned about, because a manifest written by a newer
# version of this tool must not be silently rewritten by an older one.
TIERS = ("foundation", "personal")
HEURISTIC = registry.HEURISTIC


def path(config):
    return os.path.join(config.data_dir, MANIFEST_NAME)


def _blank():
    return {"versions": [], "active": None, "previous": None, "warnings": []}


def _entry(raw, warnings):
    """Normalise one manifest row, or None when it is not a usable version."""
    if not isinstance(raw, dict):
        warnings.append("ignored a version that is not an object: %s" % str(raw)[:80])
        return None
    label = (raw.get("label") or "").strip()
    if not label:
        warnings.append("ignored a version with no label")
        return None
    tier = (raw.get("tier") or TIERS[0]).strip()
    if tier not in TIERS:
        warnings.append("version %s has an unknown tier %r (expected one of %s)"
                        % (label, tier, "/".join(TIERS)))
    url = (raw.get("url") or "").strip() or None
    return {"label": label, "url": url, "tier": tier,
            "added": raw.get("added") or "", "note": raw.get("note") or "",
            "version": (raw.get("version") or "").strip() or label}


def load(target):
    """Read a manifest. A missing or corrupt file is an *empty* manifest, never a crash.

    This is a read-only normalisation: an `active` naming a version that is not in the
    list is dropped (with a warning) rather than trusted, because resolution would
    otherwise hand a label to `by_label` and get nothing back — the same dead-endpoint
    bug with a different name.
    """
    try:
        with open(target) as f:
            data = json.load(f)
    except ValueError:
        out = _blank()
        out["warnings"].append("%s is not readable JSON; starting from an empty "
                               "manifest (the file is left alone)" % os.path.basename(target))
        return out
    except OSError:
        return _blank()          # no file yet is the normal first run, not a warning
    out = _blank()
    if not isinstance(data, dict):
        out["warnings"].append("manifest is not a JSON object; starting empty")
        return out
    seen = set()
    for raw in data.get("versions") or []:
        entry = _entry(raw, out["warnings"])
        if entry is None or entry["label"] in seen:
            continue
        seen.add(entry["label"])
        out["versions"].append(entry)
    for key in ("active", "previous"):
        value = data.get(key)
        if value in (None, "", HEURISTIC):
            out[key] = None
            continue
        if value not in seen:
            out["warnings"].append("%s names %r, which is not a registered version; "
                                   "ignored" % (key, value))
            continue
        out[key] = value
    return out


def save(target, manifest):
    """Write atomically: a process killed mid-save must not cost the active version."""
    body = {"versions": manifest.get("versions") or [],
            "active": manifest.get("active"), "previous": manifest.get("previous")}
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(body, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, target)
    return target


def by_label(manifest):
    return dict((v["label"], v) for v in manifest.get("versions") or [])


def version_of(manifest, label):
    """The version stamp for a backend that answered: registered version, else label."""
    if not label or label == HEURISTIC:
        return HEURISTIC
    entry = by_label(manifest).get(label)
    return (entry or {}).get("version") or label


def add(manifest, label, url=None, tier=None, note=None, version=None, today=None):
    """Insert or update one version. Upsert keeps `added` from the first registration."""
    label = (label or "").strip()
    if not label:
        raise ValueError("a version needs a label")
    if label == HEURISTIC:
        raise ValueError("%r is reserved: it is what the heuristics are called" % HEURISTIC)
    existing = by_label(manifest).get(label)
    tier = (tier or (existing or {}).get("tier") or TIERS[0]).strip()
    if tier not in TIERS:
        raise ValueError("tier must be %s (not %r)" % (" or ".join(TIERS), tier))
    entry = {"label": label, "url": (url or "").strip() or (existing or {}).get("url"),
             "tier": tier,
             "added": (existing or {}).get("added") or (today or time.strftime("%Y-%m-%d")),
             "note": (note if note is not None else (existing or {}).get("note")) or "",
             "version": (version or "").strip() or (existing or {}).get("version") or label}
    rows = manifest.setdefault("versions", [])
    if existing:
        rows[rows.index(existing)] = entry
    else:
        rows.append(entry)
    return manifest, entry


def use(manifest, label):
    """Point `active` at `label`, remembering what it was. Raises on an unknown label.

    Selecting `heuristic` means "run with no model", which is a change an operator
    should be able to undo — so it clears `active` after recording it in `previous`.
    """
    label = (label or "").strip()
    known = sorted(by_label(manifest))
    if label == HEURISTIC:
        if manifest.get("active") is None:
            raise ValueError("already running with no model version")
        manifest["previous"] = manifest.get("active")
        manifest["active"] = None
        return manifest, "switched to the heuristics; %s kept as previous" % (
            manifest["previous"])
    if label not in known:
        raise ValueError("no version %r registered%s"
                         % (label, "; known: " + ", ".join(known) if known else ""))
    if manifest.get("active") == label:
        return manifest, "%s is already active" % label
    manifest["previous"] = manifest.get("active")
    manifest["active"] = label
    return manifest, ("activated %s" % label + ("" if not manifest["previous"] else
                                                " (previous: %s)" % manifest["previous"]))


def rollback(manifest):
    """Undo the last `use`. With nothing behind `active`, that means no model."""
    if manifest.get("active") is None and manifest.get("previous") is None:
        raise ValueError("nothing to roll back to — no version is active or remembered")
    manifest["previous"], manifest["active"] = None, manifest.get("previous")
    return manifest, ("rolled back to %s" % manifest["active"] if manifest["active"]
                      else "rolled back to the heuristics")


def _check_health(adapter, health):
    """(ok, reason) for an adapter. `health` is injectable so tests need no server."""
    if adapter is None:
        return False, "no adapter"
    try:
        report = health(adapter) if health else adapter.health()
    except Exception as exc:  # a /health that raises is a dead endpoint, not a crash
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if report.get("ok"):
        return True, ""
    return False, report.get("error") or "no ok from /health"


def select_from_manifest(manifest, config, health=None):
    """(adapter, label, version, notes) — the manifest's answer, health-gated.

    `notes` is the surfaced reason for anything that was *not* used, so a status line
    can say "laya-v2 was asked for and its /health refused; laya answered instead".
    """
    notes = []
    if not manifest.get("active"):
        # `previous` is only a spare. An empty `active` is a choice — `--use heuristic`
        # writes exactly this state — so the heuristics answer and nothing is probed.
        return None, HEURISTIC, HEURISTIC, notes
    wanted = [manifest.get("active"), manifest.get("previous")]
    entries = by_label(manifest)
    for i, label in enumerate(wanted):
        if not label:
            continue
        entry = entries.get(label)
        url = (entry or {}).get("url") or registry.configured_url(label, config)
        if not url:
            notes.append("%s has no endpoint to point at; not used" % label)
            continue
        adapter = HTTPScoreAdapter(url, label=label)
        ok, reason = _check_health(adapter, health)
        if ok:
            if i:
                notes.append("active version %s unusable; using %s instead"
                             % (wanted[0], label))
            return adapter, label, entry.get("version") or label, notes
        notes.append("%s: /health failed (%s)" % (label, reason))
    if notes:
        notes.append("answering with the heuristics")
    return None, HEURISTIC, HEURISTIC, notes


def is_explicit(config, slot=None):
    """True when a human named the backend, so the manifest must stand down."""
    if slot and registry.is_pinned(config, slot):
        return True
    return bool((getattr(config, "model", None) or "").strip()
                or (getattr(config, "laya_url", None) or "").strip())


def select(config, slot=None, manifest=None, health=None):
    """The full precedence ladder for one slot. Returns a dict, never raises."""
    manifest = manifest if manifest is not None else load(path(config))
    if is_explicit(config, slot):
        adapter, label = registry.resolve(config, slot=slot)
        notes = []
        if adapter is not None and adapter.available:
            ok, reason = _check_health(adapter, health)
            if not ok:
                # A pin is not swapped out from under the operator, but the dead endpoint
                # has to be *said*: today each decision just records `heuristic`.
                notes.append("pinned %s is unreachable (%s); every decision it would have "
                             "answered says heuristic" % (label, reason))
        return {"adapter": adapter, "label": label,
                "version": version_of(manifest, label), "notes": notes,
                "source": "pin"}
    adapter, label, version, notes = select_from_manifest(manifest, config, health=health)
    return {"adapter": adapter, "label": label, "version": version, "notes": notes,
            "source": "manifest" if manifest.get("active") else "default"}


def select_slots(config, manifest=None, health=None):
    """{slot: (adapter, label)} + {slot: version}, for every model-backed slot."""
    backends, slot_versions, notes = {}, {}, []
    for slot in registry.MODEL_SLOTS:
        choice = select(config, slot=slot, manifest=manifest, health=health)
        backends[slot] = (choice["adapter"], choice["label"])
        slot_versions[slot] = choice["version"]
        notes.extend(choice["notes"])
    return backends, slot_versions, notes


def configured_adapters(config, manifest=None):
    """Registry endpoints *and* manifest versions: the Router's candidate pool."""
    out = registry.configured_adapters(config)
    for entry in (manifest or {}).get("versions") or []:
        url = entry.get("url") or registry.configured_url(entry["label"], config)
        if url and entry["label"] not in out:
            out[entry["label"]] = HTTPScoreAdapter(url, label=entry["label"])
    return out


def describe(manifest, config=None, health=None):
    """The manifest as a printable table, with the health verdict attached."""
    entries = by_label(manifest)
    rows = []
    for entry in manifest.get("versions") or []:
        url = entry.get("url") or (registry.configured_url(entry["label"], config)
                                   if config else None)
        status = "no endpoint"
        if url:
            ok, reason = _check_health(HTTPScoreAdapter(url, label=entry["label"]), health)
            status = "health ok" if ok else "unreachable: %s" % reason
        rows.append(dict(entry, endpoint=url, health=status,
                         active=entry["label"] == manifest.get("active"),
                         previous=entry["label"] == manifest.get("previous")))
    active = manifest.get("active")
    return {"path": None, "active": active, "previous": manifest.get("previous"),
            "active_version": (entries.get(active) or {}).get("version") if active else None,
            "versions": rows, "warnings": list(manifest.get("warnings") or [])}
