"""Dataset plumbing: retro-run the slots over recorded traffic, and export the
decisions + any human labels as JSONL. This file is the deliverable that makes
the Laya fine-tune possible, so it is deliberately the boring, stable part.
"""

import gzip
import json
import os
import random
import time

from . import protocol
from .engine import Engine, ALL_SLOTS
from .proxy import analyze_request


LABELS_FILE = "labels.json"


def labels_path(config):
    return os.path.join(config.data_dir, LABELS_FILE)


def load_labels(config):
    try:
        with open(labels_path(config)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_labels(config, labels):
    os.makedirs(config.data_dir, exist_ok=True)
    tmp = labels_path(config) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(labels, f, indent=1, sort_keys=True)
    os.replace(tmp, labels_path(config))
    return labels_path(config)


def record_bodies(config):
    d = config.bodies_dir
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json.gz"))


def read_body(path):
    try:
        with gzip.open(path, "rb") as f:
            raw = f.read()
    except (OSError, ValueError):
        with open(path, "rb") as f:
            raw = f.read()
    return raw


def _merge_verdicts(human, implicit, slot="compact"):
    """(verdict, source, implicit_label) for one request's slot. The human wins.

    `verdict` is the answer the row teaches: `good`/`bad` from a human, `keep` for a
    drop the traffic contradicted, `exclude` when the traffic only complained about the
    turn (a correction names the turn, not a candidate, so it removes supervision
    rather than inventing the opposite answer), and `mixed` where a human and the
    traffic disagree - kept visible instead of silently resolved. `implicit_label` is
    what the traffic said on its own, reported even where a human already answered.
    """
    human = dict(human or {})
    verdict = human.get(slot)
    source = "human" if verdict in ("good", "bad") else None
    targets = (implicit or {}).get("targets") or {}
    turn = (implicit or {}).get("slots") or {}
    kinds = sorted({(v.get("source") or "").split(":")[-1]
                    for v in list(targets.values()) + list(turn.values())} - {""})
    if not kinds:
        return verdict, source, None
    # a re-read names the file a `compact` drop cut, so it speaks about that slot only;
    # a turn-level complaint covers every slot of the turn it landed on
    if slot == "compact":
        flips = [v for v in targets.values() if v.get("verdict") == "keep"]
        implicit_label = "keep" if flips else "exclude"
    else:
        flips, implicit_label = [], ("exclude" if turn else None)
    if implicit_label is None:
        return verdict, source, None
    if verdict == "good":
        return "good", ("human+implicit" if flips else "human"), implicit_label
    if verdict == "bad":
        return "mixed", "mixed", implicit_label
    return implicit_label, "implicit:" + "+".join(kinds), implicit_label


def label_view(config, slot="compact"):
    """{request_id: (verdict, source, implicit_label)} over *both* label stores."""
    from . import implicit          # implicit imports dataset, so this is lazy

    human = load_labels(config)
    merged = implicit.load_implicit(config)
    return dict((rid, _merge_verdicts(human.get(rid), merged.get(rid), slot))
                for rid in set(human) | set(merged))

def audit(config, telemetry, engine=None, limit=200, verbose=False):
    """Re-derive every slot over recorded requests without touching the wire."""
    engine = engine or Engine(config)
    paths = record_bodies(config)
    if not paths:
        return {"error": "no recorded bodies — start the proxy with "
                         "--store-bodies and re-run some agent traffic"}
    out = {
        "requests_scanned": 0,
        "bodies_available": len(paths),
        "by_slot": dict((s, {"decisions": 0, "would_drop": 0, "savings_est_tok": 0})
                        for s in ALL_SLOTS),
        "examples": [],
    }
    for path in paths[-limit:]:
        raw = read_body(path)
        body = protocol.load_body(raw, "gzip" if path.endswith(".gz") else "")
        if not isinstance(body, dict):
            continue
        name = os.path.basename(path)
        dialect = "anthropic" if name.startswith("anthropic") else "openai"
        analysis = analyze_request(body)
        _, decisions = engine.decide(dialect, body, analysis, config)
        out["requests_scanned"] += 1
        for d in decisions:
            slot = out["by_slot"][d["slot"]]
            slot["decisions"] += 1
            n_drop = len(d.get("dropped") or []) or d.get("dropped_count") or 0
            slot["would_drop"] += n_drop
            slot["savings_est_tok"] += int(d.get("savings_est_tok") or 0)
            if verbose and d["slot"] == "tool_gate":
                out["examples"].append({"file": os.path.basename(path),
                                        "dropped": (d.get("dropped") or [])[:12]})
    return out


def export(config, telemetry, path=None, slots=ALL_SLOTS, include_candidates=True):
    # `subproto export` with no --slots reaches here as None; that means "every slot",
    # which is the function's own default - not an error to iterate.
    if slots is None:
        slots = ALL_SLOTS
    labels = load_labels(config)
    views = {}                      # slot -> {request_id: (verdict, source, implicit_label)}
    rows = telemetry.query(
        "SELECT id, ts, api, client, model, in_tok, cw_tok, cr_tok, out_tok, cost_usd, "
        "est_in_tok, tool_spec_chars, tool_names, decisions, features, body_sha "
        "FROM requests WHERE decisions IS NOT NULL ORDER BY id")
    lines = []
    for r in rows:
        try:
            decisions = json.loads(r["decisions"] or "[]")
        except ValueError:
            continue
        label_key = str(r["id"])
        for d in decisions:
            if d.get("slot") not in slots:
                continue
            cand = d.get("candidates") or []
            if d["slot"] not in views:
                views[d["slot"]] = label_view(config, d["slot"])
            verdict, source, ilabel = views[d["slot"]].get(label_key, (None, None, None))
            rec = {
                "schema": "subproto/1",
                "request_id": r["id"],
                "ts": r["ts"],
                "api": r["api"],
                "client": r["client"],
                "model": r["model"],
                "slot": d["slot"],
                "backend": d.get("backend"),
                "model_version": d.get("model_version"),
                "applied": bool(d.get("applied")),
                "in_tok": (r["in_tok"] or 0) + (r["cw_tok"] or 0) + (r["cr_tok"] or 0),
                "out_tok": r["out_tok"],
                "cost_usd": r["cost_usd"],
                "est_in_tok": r["est_in_tok"],
                "tool_spec_chars": r["tool_spec_chars"],
                "features": json.loads(r["features"] or "{}").get("features") if r["features"] else None,
                "body_sha": r["body_sha"],
                "outcome": {k: v for k, v in d.items()
                            if k not in ("candidates", "proof")},
                "candidates": cand if include_candidates else None,
                "label": verdict,
                "label_source": source,
                "implicit_label": ilabel,
                "human_label": labels.get(label_key, {}).get(d["slot"]),
            }
            lines.append(rec)
    target = path or os.path.join(
        config.data_dir, "dataset-%s.jsonl" % time.strftime("%Y%m%d"))
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w") as f:
        for rec in lines:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return {"path": target, "rows": len(lines),
            "labelled": sum(1 for r in lines if r["label"]),
            "labelled_by_human": sum(1 for r in lines if r["human_label"]),
            "labelled_by_traffic": sum(1 for r in lines
                                       if not r["human_label"] and r["label_source"]),
            "sources": dict((v, sum(1 for r in lines if r["label_source"] == v))
                             for v in sorted({r["label_source"] for r in lines} - {None})),
            "slots": dict((s, sum(1 for r in lines if r["slot"] == s)) for s in slots)}


def _state_for(row, bodies):
    """The model's conditioning text: the request's own words when we recorded the
    body, else a stable stand-in derived from the stored features."""
    entry = bodies.get(row.get("body_sha"))
    if entry:
        path, _dialect = entry
        try:
            body = protocol.load_body(read_body(path), "gzip" if path.endswith(".gz") else "")
            a = analyze_request(body)
            q = a.get("query") or ""
            if q:
                return q[-512:]
        except Exception:
            pass
    names = json.loads(row.get("tool_names") or "[]") if row.get("tool_names") else []
    return " ".join(names[:8]) or (row.get("api") or "") + " turn"


def _contradicted(entry):
    """Candidate ids the traffic contradicted (a re-read proved the content was in use)."""
    targets = (entry or {}).get("targets") or {}
    return set(k for k, v in targets.items() if v.get("verdict") == "keep")


def build_training_split(config, telemetry, val_frac=0.2, seed=1337, write=True):
    """Deterministic, de-duplicated train/val split in Laya supervision format.

    Each example is a single narrow keep/drop question — `{state, question,
    options, answer}` — which is exactly the shape `laya.py` asks at inference
    time, so train and serve match. De-duplicated by (body_sha, slot, candidate);
    a decision a human rejected (`bad`), a disagreement between the two stores
    (`mixed`), or a turn the traffic only complained about (`exclude`) is dropped
    rather than taught; a drop the traffic re-read is taught as `keep`. Seeded shuffle
    makes the split reproducible and leak-free (index split of one shuffled list).
    """
    val_frac = max(0.0, min(1.0, float(val_frac)))
    bodies = {}
    for p in record_bodies(config):
        stem = os.path.basename(p)
        for suffix in (".json.gz", ".json"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        dialect, _, sha = stem.partition("_")
        if sha:
            bodies[sha] = (p, dialect)

    rows = telemetry.query(
        "SELECT id, body_sha, decisions, tool_names, api FROM requests "
        "WHERE decisions IS NOT NULL ORDER BY id")
    labels = load_labels(config)
    from . import implicit
    implied = implicit.load_implicit(config)

    seen = set()
    examples = []
    for r in rows:
        try:
            decisions = json.loads(r["decisions"] or "[]")
        except ValueError:
            continue
        state = _state_for(r, bodies)
        rid = str(r["id"])
        hum, imp = labels.get(rid, {}), implied.get(rid)
        contradicted = _contradicted(imp)
        for d in decisions:
            slot = d.get("slot")
            if slot not in ALL_SLOTS:
                continue
            verdict, source, _il = _merge_verdicts(hum, imp, slot)
            # `bad`/`mixed` are a rejected decision; `exclude` is a complaint that
            # names the turn and no candidate, so it can only remove supervision
            if verdict in ("bad", "mixed", "exclude"):
                continue
            taught = verdict is not None
            for cand in d.get("candidates") or []:
                tgt = cand.get("target")
                if tgt is None:
                    continue
                key = (r["body_sha"], slot, tgt)
                if key in seen:
                    continue
                seen.add(key)
                keep = bool(cand.get("keep")) or (slot == "compact" and tgt in contradicted)
                examples.append({
                    "state": state,
                    "question": "keep",
                    "slot": slot,
                    "options": [tgt],
                    "answer": "keep" if keep else "drop",
                    "label_source": source or ("human" if taught else "heuristic"),
                    "backend": d.get("backend"),
                    "model_version": d.get("model_version"),
                    "body_sha": r["body_sha"],
                })

    rng = random.Random(seed)
    rng.shuffle(examples)
    cut = int(round(len(examples) * (1.0 - val_frac)))
    train, val = examples[:cut], examples[cut:]

    def _count(pred):
        return sum(1 for e in examples if pred(e))
    def from_src(e):
        return (e["label_source"] or "").startswith("implicit:")
    out = {"n_examples": len(examples), "n_train": len(train), "n_val": len(val),
           "n_supervised": _count(lambda e: e["label_source"] != "heuristic"),
           "n_from_traffic": _count(from_src),
           "n_traffic_keeps": _count(lambda e: from_src(e) and e["answer"] == "keep"),
           "n_traffic_drops_taught": _count(lambda e: from_src(e) and e["answer"] == "drop"),
           "n_heuristic": _count(lambda e: e["label_source"] == "heuristic"),
           "val_frac": val_frac, "seed": seed, "dedup": True}
    if write:
        d = os.path.join(config.data_dir, "training")
        os.makedirs(d, exist_ok=True)
        for name, part in (("train.jsonl", train), ("val.jsonl", val)):
            with open(os.path.join(d, name), "w") as f:
                for ex in part:
                    f.write(json.dumps(ex, separators=(",", ":")) + "\n")
        out["train_path"] = os.path.join(d, "train.jsonl")
        out["val_path"] = os.path.join(d, "val.jsonl")
    else:
        out["train"], out["val"] = train, val
    return out
