"""Implicit labels: learn which drops were wrong from outcomes already on disk.

`subproto label` needs a human to look at a request, so it scales with attention.
But agent traffic labels itself, for free, one turn later: if this turn evicted the
read of `app/payments/retry.py` and a later turn *fetches that same file again*, the
eviction cut evidence that was still in live use. That pair of facts — what a plan
threw away, and what the agent then paid to get back — is already in the telemetry DB
plus the recorded bodies. Nothing here uploads or infers a preference; it re-reads
local traffic and reports the contradiction. (I4: no egress; the only reads are
`telemetry.db` and `$SUBPROTO_HOME/bodies/`.)

Three signals, in descending confidence, each carrying its own evidence string:

  re-read      a path evicted at turn i is freshly read again at turn j > i.
               High confidence, and the only one that carries a token cost — and
               that cost is claimed *only* for turns where the drop was actually
               enforced on the wire. In shadow mode the agent re-read anyway (the
               bytes were never lost), so the same observation is reported as
               `would_have_been_live`: still a wrong-drop signal for the label
               corpus, but no double-billing claim.
  correction   the next turn opens with a correction/retry shape. Weak: a complaint
               names the *turn*, not a candidate, so it only ever excludes the
               drops it disliked from supervision — it never teaches the opposite.
  re-run       the same `body_sha` twice inside a window: the first attempt did not
               stick.

The output feeds `dataset.py` (training split + export) and `report.py` (eviction
regret) as `implicit_labels.json`, a store kept separate from the human one so a
`subproto label` verdict is never overwritten by a guess.
"""

import json
import os
import re
import time

from . import dataset, protocol

IMPLICIT_FILE = "implicit_labels.json"

# How far after the eviction to look for a re-read, and how long a turn stays
# interesting. Both are deliberately short: a file fetched 40 turns later is a new
# task, not a consequence of this drop.
WINDOW_TURNS = 20
WINDOW_S = 1800.0

CORRECTION_RE = re.compile(
    r"^\s*(?:no[,.]?\s|wait[, ]|actually[,.]|that(?:'s| is) (?:wrong|not right|incorrect)|"
    r"you (?:didn't|did not|missed|ignored)|wrong (?:file|function|answer|fix)|"
    r"i (?:already|asked|said)|look again|try again|didn'?t work|still (?:broken|failing)|"
    r"read the file|check the file)",
    re.I)

# A re-read is only a re-read if something actually fetched content. A search result
# that lists a path is the agent *looking for* the file, not paying for it again, so
# results from these tools never count. A result whose call we cannot identify falls
# back to the paths in its own text — unknown is treated as fetched, not ignored.
FETCH_EXCLUDED_TOOLS = ("grep", "glob", "ls", "search", "file_search", "codebase",
                        "websearch", "web_search", "webfetch", "web_fetch", "find")


def implicit_path(config):
    return os.path.join(config.data_dir, IMPLICIT_FILE)


def load_implicit(config):
    """{request_id(str): {slots:{}, targets:{}, ts}} — {} when nothing was harvested."""
    try:
        with open(implicit_path(config)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_implicit(config, out):
    os.makedirs(config.data_dir, exist_ok=True)
    tmp = implicit_path(config) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(tmp, implicit_path(config))
    return implicit_path(config)


# ---------------------------------------------------------------- request anatomy

def _body_index(config):
    """body_sha -> recorded body path, from the `--store-bodies` spool."""
    out = {}
    for p in dataset.record_bodies(config):
        stem = os.path.basename(p)
        for suffix in (".json.gz", ".json"):
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        _dialect, _, sha = stem.partition("_")
        if sha:
            out[sha] = p
    return out


def _load_body(path):
    try:
        raw = dataset.read_body(path)
        body = protocol.load_body(raw, "gzip" if path.endswith(".gz") else "")
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _msg_calls(m):
    """[(key, lowercased tool name, call payload)] for one raw message, all dialects."""
    out = []
    content = m.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append((b.get("id") or b.get("name") or "?",
                            (b.get("name") or "").lower(), b.get("input")))
    for tc in m.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or ""
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw else raw
            except ValueError:
                args = raw
            out.append((tc.get("id") or fn.get("name") or "?",
                        (fn.get("name") or "").lower(), args))
    for p in m.get("parts") or []:
        if isinstance(p, dict) and isinstance(p.get("functionCall"), dict):
            fc = p["functionCall"]
            out.append((fc.get("name") or "?", (fc.get("name") or "").lower(),
                        fc.get("args")))
    return out


def _messages(body):
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        msgs = body.get("contents") if isinstance(body.get("contents"), list) else []
    return [m for m in msgs if isinstance(m, dict)]


def aligned_messages(body):
    """The body's own turns, index-aligned with `normalize_messages(body)`.

    Needed because `normalize_messages` flattens a message to text, which loses the
    call arguments — and in the OpenAI shape the file path lives *only* there. The
    leading `system` entry (Anthropic's top-level string, Gemini's systemInstruction)
    is mirrored as an empty dict so the indices stay true.
    """
    out = []
    if "contents" in body and "messages" not in body:
        if body.get("systemInstruction") or body.get("system_instruction"):
            out.append({})
    elif body.get("system"):
        out.append({})
    out += _messages(body)
    return out


def _msg_paths(m):
    """(paths, result_text) for one raw message, in wire order.

    `paths` accumulates every path this message's tool calls ask for — including a
    search call's `path: "repo/src"`, which is why the fetch decision is made per
    *result* by `read_counts`, never by "a path appeared".
    """
    paths = []
    for key, name, args in _msg_calls(m):
        paths += [p for p in protocol.extract_paths(
            json.dumps(args, separators=(",", ":")) if isinstance(args, (dict, list))
            else str(args or "")) if p not in paths]
    text = ""
    if isinstance(m.get("content"), (dict, list, str)):
        text = protocol.content_to_text(m.get("content"))
    results = []
    content = m.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                results.append((b.get("tool_use_id") or "?", protocol.block_text(b)))
    elif m.get("role") == "tool" and isinstance(content, str):
        results.append((m.get("tool_call_id") or m.get("name") or "?", content))
    for p in m.get("parts") or []:
        if isinstance(p, dict) and isinstance(p.get("functionResponse"), dict):
            fr = p["functionResponse"]
            results.append((fr.get("name") or "?",
                            json.dumps(fr.get("response") or {})))
    return paths, results, text


def read_counts(body):
    """({path: n_fetches}, {path: tokens of its newest fetch}), merged per *file*.

    A fetch is a tool *result* paired with the call that asked for it; the path comes
    from the call (which is the only place it exists in the OpenAI shape) and, for a
    result whose call we cannot identify, from the returned text. A result from one of
    FETCH_EXCLUDED_TOOLS never counts — a grep that lists a file is the agent looking
    for it, not paying for it again.

    Counts, not sets, and they are **cumulative over the conversation**: agent traffic
    replays every earlier read inside each new request, so a path going from 1 result
    to 2 across turns is what a re-fetch looks like on the wire.

    One file has several spellings on the wire — a call argument says `app/x/retry.py`
    and the result header says `/repo/app/x/retry.py` — so a per-key read count would
    double on its own definition. Everything here is folded onto one canonical key per
    file (the most specific spelling seen), which is what makes "was 1, now 2" true
    whichever spelling arrived.
    """
    counts, toks, stamp = {}, {}, {}
    calls = {}
    seq = 0
    for m in _messages(body):
        _paths, results, _text = _msg_paths(m)
        for key, name, args in _msg_calls(m):
            calls[key] = (name, args)
        for key, result in results:
            seq += 1
            cname, cargs = calls.get(key, ("", None))
            if any(t in cname for t in FETCH_EXCLUDED_TOOLS):
                continue
            own = protocol.extract_paths(result)
            if not own and cargs is not None:
                own = protocol.extract_paths(
                    json.dumps(cargs, separators=(",", ":"))
                    if isinstance(cargs, (dict, list)) else str(cargs))
            for p in own:
                _merge(counts, toks, stamp, p, protocol.approx_tokens(result), seq)
    return counts, toks


def _merge(counts, toks, stamp, path, tokens, seq):
    """Fold one fetch into the per-file tallies, under the most specific spelling so far.

    `tokens` follows the fetch that happened *last* (`seq`), not the largest one — the
    price of a re-read is what the agent paid for the newest copy.
    """
    key = next((q for q in counts if protocol.same_path(q, path)), path)
    if len(path.split("/")) > len(key.split("/")):
        for d in (counts, toks, stamp):
            d[path] = d.pop(key)
        key = path
    counts[key] = counts.get(key, 0) + 1
    if seq > stamp.get(key, -1):
        stamp[key] = seq
        toks[key] = tokens


def last_user_text(body):
    norm = protocol.normalize_messages(body)
    for i in range(len(norm) - 1, -1, -1):
        if norm[i][2] == "user":
            return norm[i][1] or ""
    return ""


def evictions(decisions):
    """[(pointer, target, tokens, index, applied)] for reads a plan threw away.

    Reads from the compiled `proof` when present (it is the only artifact that keeps
    the normalized index) and from the per-slot `compact` candidates otherwise, so
    both engines are auditable with the same call.
    """
    out = []
    applied_turn = any(bool(d.get("applied")) for d in decisions)
    proof = None
    for d in decisions:
        if d.get("proof"):
            proof = d["proof"]
            break
    if proof is not None:
        for row in proof.get("dropped") or []:
            if row.get("kind") != "message":
                continue
            idx = (row.get("reversible") or {}).get("index")
            out.append((row.get("pointer") or "message:%s" % row.get("id"),
                        row.get("id"), int(row.get("tokens") or 0), idx,
                        bool(proof.get("applied"))))
        return out
    for d in decisions:
        if d.get("slot") != "compact":
            continue
        for c in d.get("candidates") or []:
            if c.get("keep"):
                continue
            out.append(("message:%s" % c.get("target"), c.get("target"),
                        int(c.get("tokens") or 0), c.get("index"), applied_turn))
    return out


# ------------------------------------------------------------------- the harvest

def _call_names(raws):
    """{call key: lowercased tool name} over a whole turn, for result attribution."""
    out = {}
    for m in raws:
        for key, name, _args in _msg_calls(m if isinstance(m, dict) else {}):
            out[key] = name
    return out


def _excluded(name):
    return bool(name) and any(t in name for t in FETCH_EXCLUDED_TOOLS)


def _evicted_paths(raws, norm, index, calls=None, lookback=2):
    """Which files the dropped turn was the content *of*.

    The result text often carries the path in its header, but in the OpenAI shape the
    path exists only in the call that preceded it — so the call arguments of this turn
    and the one or two before it are read too.

    Attribution is per *result*, not per message: one message can carry a search hit
    list next to a file's contents, and the flat text of that message names both. A
    dropped turn whose only results were searches is the content of nothing.
    """
    out = []
    if not isinstance(index, int) or not (0 <= index < len(norm)):
        return out
    msg = raws[index] if isinstance(raws[index], dict) else {}
    _p, results, text = _msg_paths(msg)
    names = [(calls or {}).get(k) or "" for k, _r in results]
    if results and all(_excluded(n) for n in names):
        return []
    for (key, body), name in zip(results, names):
        if _excluded(name):
            continue
        for p in protocol.extract_paths(body):
            if p not in out:
                out.append(p)
    if not results:
        out += [p for p in protocol.extract_paths(text) if p not in out]
    for i in range(max(0, index - lookback), min(len(raws), index + 1)):
        for _key, name, args in _msg_calls(raws[i] if isinstance(raws[i], dict) else {}):
            blob = json.dumps(args, separators=(",", ":")) if isinstance(args, (dict, list)) \
                else str(args or "")
            if _excluded(name):
                continue
            for p in protocol.extract_paths(blob):
                if p not in out:
                    out.append(p)
    return out


def harvest(config, telemetry, limit=200, window=WINDOW_TURNS):
    """Re-read recorded traffic and label the drops that were contradicted by it.

    Returns {labels: {request_id: {...}}, summary: {...}}. Only requests whose bodies
    were stored can be judged — the rest are counted in `skipped_no_body`, never
    guessed at.
    """
    rows = telemetry.query(
        "SELECT id, ts, body_sha, decisions FROM requests "
        "WHERE decisions IS NOT NULL ORDER BY id")
    if limit:
        rows = rows[-limit:]
    index = _body_index(config)
    turns = []
    skipped = 0
    for r in rows:
        try:
            decisions = json.loads(r["decisions"] or "[]")
        except ValueError:
            decisions = []
        path = index.get(r["body_sha"])
        body = _load_body(path) if path else None
        if body is None:
            skipped += 1
            turns.append({"id": r["id"], "ts": r["ts"], "body": None,
                          "decisions": decisions})
            continue
        counts, toks = read_counts(body)
        turns.append({"id": r["id"], "ts": r["ts"], "body": body, "decisions": decisions,
                      "counts": counts, "toks": toks, "sha": r["body_sha"],
                      "norm": protocol.normalize_messages(body),
                      "raws": aligned_messages(body),
                      "user": last_user_text(body)})

    labels = {}
    seen_sha = {}
    n_evicted = n_paths = 0
    regret_applied = regret_shadow = 0
    tok_applied = 0

    def note(req_id, slot=None, target=None, pointer=None, **kw):
        entry = labels.setdefault(str(req_id), {"request_id": req_id, "slots": {},
                                                "targets": {}, "ts": kw.pop("ts", None)})
        key = target or pointer or slot or "_"
        bucket = entry["targets"] if (target or pointer) else entry["slots"]
        prev = bucket.get(key)
        # re-read (a fetched file) outranks correction/re-run (turn-level complaints),
        # and within one class the first observation wins.
        if prev is not None and prev.get("source") in ("implicit:re-read",
                                                      "implicit:would-be-live"):
            return
        if prev is not None and kw.get("source") not in ("implicit:re-read",
                                                        "implicit:would-be-live"):
            return
        rec = {"verdict": kw.pop("verdict"), "source": kw.pop("source"),
               "evidence": kw.pop("evidence")}
        rec.update(kw)
        bucket[key] = rec

    ev_by_turn = []
    for i, t in enumerate(turns):
        if t["body"] is None:
            ev_by_turn.append({})
            continue
        # re-run: an identical request arriving twice inside the window
        prev_id, prev_ts = seen_sha.get(t["sha"], (None, None))
        if prev_id is not None and (t["ts"] or 0) - (prev_ts or 0) <= WINDOW_S:
            note(prev_id, slot="turn", verdict="bad", source="implicit:re-run",
                 evidence="identical request body replayed at #%d within %.0fs"
                          % (t["id"], (t["ts"] or 0) - (prev_ts or 0)), ts=t["ts"])
        seen_sha[t["sha"]] = (t["id"], t["ts"])
        # correction: this turn opens by disagreeing with the previous one
        if CORRECTION_RE.search(t["user"] or "") and i > 0:
            prev = turns[i - 1]
            if prev["body"] is not None:
                note(prev["id"], slot="turn", verdict="bad", source="implicit:correction",
                     evidence="next turn (#%d) opened with: %r"
                              % (t["id"], (t["user"] or "").strip()[:70]), ts=t["ts"])
        # what this turn threw away, and which file each piece was the content of
        live = {}
        names = _call_names(t["raws"])
        for pointer, target, tokens, idx, applied in evictions(t["decisions"]):
            for p in _evicted_paths(t["raws"], t["norm"], idx, names):
                # The call says `app/x/retry.py` and the result header says
                # `/repo/app/x/retry.py`: one file, one eviction.
                if any(protocol.same_path(q, p) for q in live):
                    continue
                n_evicted += 1
                live[p] = {"pointer": pointer, "target": target, "tokens": tokens,
                           "applied": applied}
        ev_by_turn.append(live)

    # Re-reads: blame one eviction per *growth event*, and blame the nearest one that
    # is still in force. Every turn replays the same history, so without this a file
    # evicted on 26 consecutive turns and fetched once would be counted 26 times.
    prev_counts = {}
    for j, t in enumerate(turns):
        if t["body"] is None:
            # No body, no counts: keep the last known baseline rather than resetting it,
            # or the next replayed turn would look like every file was fetched again.
            continue
        for p, n in sorted(t["counts"].items()):
            if n <= prev_counts.get(p, 0):
                continue
            base_prev = prev_counts.get(p, 0)
            for i in range(j - 1, max(-1, j - 1 - max(1, window)), -1):
                earlier = turns[i]
                if earlier["body"] is None:
                    continue
                if (t["ts"] or 0) - (earlier["ts"] or 0) > WINDOW_S:
                    break
                match = next((q for q in ev_by_turn[i] if protocol.same_path(q, p)), None)
                if match is None:
                    continue
                meta = ev_by_turn[i][match]
                cost = t["toks"].get(p, 0)
                n_paths += 1
                if meta["applied"]:
                    regret_applied += 1
                    tok_applied += cost
                else:
                    regret_shadow += 1
                note(earlier["id"], slot="compact", target=meta["target"],
                     pointer=meta["pointer"], verdict="keep", path=p,
                     source="implicit:re-read" if meta["applied"]
                            else "implicit:would-be-live",
                     regret_tok=(cost if meta["applied"] else 0),
                     evicted_tok=meta["tokens"],
                     evidence="%s was fetched again at #%d (%s tok, was %d) after #%d "
                              "%s its read"
                              % (p, t["id"], cost, base_prev, earlier["id"],
                                 "cut" if meta["applied"] else "would have cut"),
                     ts=earlier["ts"])
                break
        prev_counts = t["counts"]

    summary = {
        "requests_scanned": len(turns),
        "bodies_available": sum(1 for t in turns if t["body"] is not None),
        "skipped_no_body": skipped,
        "window_turns": max(1, window),
        "window_s": WINDOW_S,
        "harvested_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time())),
        "evicted_reads_seen": n_evicted,
        "wrong_drops": n_paths,
        "wrong_drops_enforced": regret_applied,
        "wrong_drops_shadow": regret_shadow,
        "refetch_tok_paid": tok_applied,
        "labels_written": len(labels),
        "by_source": {},
    }
    for entry in labels.values():
        for rec in list(entry["targets"].values()) + list(entry["slots"].values()):
            src = rec.get("source") or "?"
            row = summary["by_source"].setdefault(src, {"labels": 0, "regret_tok": 0})
            row["labels"] += 1
            row["regret_tok"] += int(rec.get("regret_tok") or 0)
    return {"labels": labels, "summary": summary}


def run(config, telemetry, limit=200, write=True, window=WINDOW_TURNS):
    """Harvest, then persist `implicit_labels.json` (merged with what is already there).

    An empty harvest leaves the store alone: `path` is None rather than a file of
    `{}` pretending the run found something.
    """
    out = harvest(config, telemetry, limit=limit, window=window)
    if not write:
        return out
    existing = load_implicit(config)
    merged = dict(existing)
    changed = False
    for rid, entry in out["labels"].items():
        if merged.get(rid) != entry:
            merged[rid] = entry
            changed = True
    out["summary"]["already_stored"] = len(existing)
    out["summary"]["stored"] = len(merged)
    if not changed:
        # The store already says exactly this. `unchanged` is reported separately from
        # `path` so a caller cannot print "wrote N" for a file it did not touch.
        out["path"] = implicit_path(config) if existing else None
        out["unchanged"] = True
        return out
    out["path"] = save_implicit(config, merged)
    out["summary"]["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return out
