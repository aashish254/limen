#!/usr/bin/env python3
"""Mutation gate: edit a rule in the source, and the suite must fail.

`subproto/implicit.py` decides, from recorded traffic alone, that a drop was wrong;
`subproto/dataset.py` decides whether that verdict reaches the training set;
`subproto/graph.py`/`compiler.py` decide what evidence survives the budget;
`subproto/systemone/versions.py` decides which model answered and what it is called;
`subproto/laya_server.py` decides how a model is asked, and `bench/ablation.py` and
`bench/laya_latency.py` decide what a measurement is allowed to say about itself.
`subproto/doctor.py` decides whether *this machine* can run the thing at all,
`subproto/cli.py` decides which command a typed name means and what stream it prints
to, and `subproto/demo.py` decides when the traffic it replayed is finished arriving.
Each is a file of *rules*, and a test suite over rules can pass while the rule is
inverted — so each rule is broken here, one at a time, and must be caught.

Three properties this script enforces on itself:

1. The baseline must be green before any mutant runs. A mutant tested against a
   already-failing suite "kills" vacuously; that invalidates every result below it.
2. Each anchor must appear **exactly once** in its file. A second occurrence would
   leave the mutated line untouched and report a false kill.
3. The source is restored byte-for-byte, and verified, whether or not the run passed.

Usage: `python3.11 bench/mutation_gate.py` (exit 1 if any mutant survives or patches).
"""

import hashlib
import os
import subprocess
import sys

# (id, file, exact-old, exact-new) — `old` is copied verbatim from the source,
# including indentation, because a fuzzy anchor is an anchor that silently misses.
MUTANTS = [
    # ------------------------------------------------ implicit.py: reading the wire
    ("S29-1 a search result counts as a fetch", "subproto/implicit.py",
     '            if any(t in cname for t in FETCH_EXCLUDED_TOOLS):\n                continue\n',
     '            if False:\n                continue\n'),
    ("S29-2 wire spellings of one file stay separate keys", "subproto/implicit.py",
     "    key = next((q for q in counts if protocol.same_path(q, path)), path)",
     "    key = path"),
    ("S29-3 fetch counts do not accumulate", "subproto/implicit.py",
     "    counts[key] = counts.get(key, 0) + 1",
     "    counts[key] = 1"),
    ("S29-4 the token price follows the largest fetch, not the newest", "subproto/implicit.py",
     "    if seq > stamp.get(key, -1):",
     "    if tokens > toks.get(key, 0):"),
    ("S29-5 a dropped search result is blamed for its listed paths anyway",
     "subproto/implicit.py",
     '    if results and all(_excluded(n) for n in names):\n        return []\n',
     ""),
    ("S29-6 a search result's own text is read as fetched content", "subproto/implicit.py",
     "    for (key, body), name in zip(results, names):\n        if _excluded(name):\n            continue\n",
     "    for (key, body), name in zip(results, names):\n        if False:\n            continue\n"),
    ("S29-7 a neighbouring search call's file is borrowed by this drop", "subproto/implicit.py",
     '            blob = json.dumps(args, separators=(",", ":")) if isinstance(args, (dict, list)) \\\n'
     '                else str(args or "")\n'
     "            if _excluded(name):\n                continue\n",
     '            blob = json.dumps(args, separators=(",", ":")) if isinstance(args, (dict, list)) \\\n'
     '                else str(args or "")\n'),
    ("S29-8 one eviction per spelling instead of per file", "subproto/implicit.py",
     '                if any(protocol.same_path(q, p) for q in live):\n                    continue\n',
     ""),
    ("S29-9 blame every eviction in the window, not the nearest", "subproto/implicit.py",
     '                     ts=earlier["ts"])\n                break',
     '                     ts=earlier["ts"])'),
    ("S29-10 the time window is ignored while walking back", "subproto/implicit.py",
     '                if (t["ts"] or 0) - (earlier["ts"] or 0) > WINDOW_S:\n                    break',
     "                pass"),
    ("S29-11 shadow-mode drops are charged regret tokens", "subproto/implicit.py",
     '                     regret_tok=(cost if meta["applied"] else 0),',
     "                     regret_tok=cost,"),
    ("S29-12 a shadow drop is reported as if it reached the wire", "subproto/implicit.py",
     '                     source="implicit:re-read" if meta["applied"]\n'
     '                            else "implicit:would-be-live",',
     '                     source="implicit:re-read",'),
    ("S29-13 a missing body resets the re-read baseline", "subproto/implicit.py",
     '        if t["body"] is None:\n'
     "            # No body, no counts: keep the last known baseline rather than resetting it,\n"
     "            # or the next replayed turn would look like every file was fetched again.\n"
     "            continue\n",
     '        if t["body"] is None:\n            prev_counts = {}\n            continue\n'),
    ("S29-14 a re-read convicts the turn that re-read, not the turn that cut",
     "subproto/implicit.py",
     '                note(earlier["id"], slot="compact", target=meta["target"],',
     '                note(t["id"], slot="compact", target=meta["target"],'),
    ("S29-15 a correction convicts its own turn", "subproto/implicit.py",
     '                note(prev["id"], slot="turn", verdict="bad", source="implicit:correction",',
     '                note(t["id"], slot="turn", verdict="bad", source="implicit:correction",'),
    ("S29-16 the re-run detector is off", "subproto/implicit.py",
     '        if prev_id is not None and (t["ts"] or 0) - (prev_ts or 0) <= WINDOW_S:',
     "        if False:"),
    ("S29-17 per-slot candidates are read even when a proof exists", "subproto/implicit.py",
     "    if proof is not None:",
     "    if False:"),
    ("S29-18 protected candidates are reported as evictions", "subproto/implicit.py",
     '            if c.get("keep"):\n                continue\n',
     "            if False:\n                continue\n"),

    # --------------------------------------------- implicit.harvest: the counters
    ("S30-1 evicted reads seen are not counted", "subproto/implicit.py",
     "                n_evicted += 1",
     "                pass"),
    ("S30-2 shadow regret is counted in the wrong-drops total", "subproto/implicit.py",
     '                if meta["applied"]:\n                    regret_applied += 1',
     "                if True:\n                    regret_applied += 1"),
    ("S30-3 a contradicted drop is not counted as wrong", "subproto/implicit.py",
     "                n_paths += 1",
     "                pass"),
    ("S30-4 the enforced tally is not reported", "subproto/implicit.py",
     '        "wrong_drops_enforced": regret_applied,',
     '        "wrong_drops_enforced": 0,'),
    ("S30-5 the shadow tally is folded into enforced", "subproto/implicit.py",
     '        "wrong_drops_shadow": regret_shadow,',
     '        "wrong_drops_shadow": 0,'),
    ("S30-6 the paid-back tokens are not reported", "subproto/implicit.py",
     '        "refetch_tok_paid": tok_applied,',
     '        "refetch_tok_paid": 0,'),
    ("S30-7 labels are not attributed to their signal", "subproto/implicit.py",
     '            row = summary["by_source"].setdefault(src, {"labels": 0, "regret_tok": 0})',
     '            row = summary["by_source"].setdefault("_", {"labels": 0, "regret_tok": 0})'),
    ("S30-8 per-signal regret tokens are dropped", "subproto/implicit.py",
     '            row["regret_tok"] += int(rec.get("regret_tok") or 0)',
     "            pass"),

    # ------------------------------------------------------ implicit.run: the store
    ("S30-9 an unchanged harvest rewrites the store every run", "subproto/implicit.py",
     "    if not changed:",
     "    if False:"),
    ("S30-10 an empty harvest still reports a written file", "subproto/implicit.py",
     '        out["path"] = implicit_path(config) if existing else None',
     '        out["path"] = implicit_path(config)'),
    ("S30-11 --dry-run writes anyway", "subproto/implicit.py",
     "    if not write:\n        return out",
     "    if False:\n        return out"),
    ("S30-12 a re-harvest cannot replace an entry it now reads differently",
     "subproto/implicit.py",
     "        if merged.get(rid) != entry:",
     "        if rid in merged:"),

    # -------------------------------------------------- dataset.py: who gets taught
    ("S30-13 a re-read flips the teaching answer even for a non-compact slot",
     "subproto/dataset.py",
     '                keep = bool(cand.get("keep")) or (slot == "compact" and tgt in contradicted)',
     '                keep = bool(cand.get("keep")) or (tgt in contradicted)'),
    ("S30-14 the traffic evidence never reaches the split", "subproto/dataset.py",
     '                keep = bool(cand.get("keep")) or (slot == "compact" and tgt in contradicted)',
     '                keep = bool(cand.get("keep"))'),
    ("S30-15 a rejected or disagreed verdict is taught anyway", "subproto/dataset.py",
     '            if verdict in ("bad", "mixed", "exclude"):',
     '            if verdict in ("bad",):'),
    ("S30-16 a turn-level complaint is treated as a candidate verdict", "subproto/dataset.py",
     '        contradicted = _contradicted(imp)',
     "        contradicted = set((imp or {}).get('slots') or {})"),
    ("S30-17 a human `good` no longer hides the traffic's own label", "subproto/dataset.py",
     '        return "good", ("human+implicit" if flips else "human"), implicit_label',
     '        return "good", "human", None'),
    ("S30-18 a human/traffic disagreement is resolved silently toward the human",
     "subproto/dataset.py",
     '        return "mixed", "mixed", implicit_label',
     '        return "good", "human", implicit_label'),
    ("S30-19 the implicit store is ignored by the audit view", "subproto/dataset.py",
     "    merged = implicit.load_implicit(config)",
     "    merged = {}"),

    # ---------------------------------------------------- report.py: what is printed
    ("S30-20 a shadow drop disappears from the regrettable count", "subproto/report.py",
     '        "regrettable_drops": regret,',
     '        "regrettable_drops": enforced,'),
    ("S30-21 the regret rate is computed over readable bodies, not decided requests",
     "subproto/report.py",
     '    scanned = int(h.get("requests_scanned") or 0)',
     '    scanned = int(h.get("bodies_available") or 0)'),
    ("S30-22 the shadow split is hidden behind the enforced number", "subproto/report.py",
     '        "shadow": shadow,',
     '        "shadow": 0,'),
    ("S30-23 the section vanishes when the honest answer is zero", "subproto/report.py",
     "    if not er:\n        return []",
     "    if not er or not er[\"regrettable_drops\"]:\n        return []"),
    ("S30-24 a human/traffic disagreement is not surfaced in the report",
     "subproto/report.py",
     '    if verdicts and cov.get("disagreements"):',
     '    if verdicts and False:'),

    # -------------------------------------------------------- cli.py: the two doors
    ("S30-25 `report --no-learn` still re-harvests", "subproto/cli.py",
     "    harvest = None if args.no_learn else implicit.harvest(config, telemetry)",
     "    harvest = implicit.harvest(config, telemetry)"),
    ("S30-26 `learn --dry-run` writes the store", "subproto/cli.py",
     "    out = implicit.run(config, telemetry, limit=args.limit, write=not args.dry_run)",
     "    out = implicit.run(config, telemetry, limit=args.limit, write=True)"),
    ("S30-27 `learn` claims a write for a store it left alone", "subproto/cli.py",
     '    elif out.get("unchanged"):',
     "    elif False:"),
    ("S30-28 the unchanged flag is never set", "subproto/implicit.py",
     '        out["unchanged"] = True',
     "        pass"),
    # ------------------------------------------- graph.py S31: a data file's own rules
    ("S31-1 `.json`/`.csv`/`.yaml` are back outside the index (FR-10's hole)", "subproto/graph.py",
     '''    ".json": "json", ".csv": "csv", ".tsv": "tsv", ".yaml": "yaml", ".yml": "yaml",''',
     ""),
    ("S31-2 a nested JSON key is not walked, so only the top level is a symbol", "subproto/graph.py",
     '        for k, v in obj.items():\n            path = "%s.%s" % (prefix, k) if prefix else str(k)\n'
     '            out.append(path)\n            _json_key_paths(v, path, out, limit)',
     '        for k, v in obj.items():\n            path = "%s.%s" % (prefix, k) if prefix else str(k)\n'
     '            out.append(path)'),
    ("S31-3 a JSON list is not read, so a fixture array contributes nothing", "subproto/graph.py",
     '    elif isinstance(obj, list):\n        for item in obj[:1]:',
     '    elif isinstance(obj, list):\n        for item in []:'),
    ("S31-4 malformed JSON loses the whole file instead of the keys already written",
     "subproto/graph.py",
     '        except ValueError:\n            paths = [m.group(1) for m in JSON_KEY_RE.finditer(src)]',
     '        except ValueError:\n            paths = []'),
    ("S31-5 a YAML sibling becomes a child (the indent stack pops too eagerly)", "subproto/graph.py",
     "        while stack and stack[-1][0] >= indent:",
     "        while stack and stack[-1][0] > indent:"),
    ("S31-6 YAML paths lose their parents (flat keys, no shape)", "subproto/graph.py",
     "        paths = [k for _i, k in stack] + [key]",
     "        paths = [key]"),
    ("S31-7 a TSV is split on commas, so its header is one giant column", "subproto/graph.py",
     '        cols = _csv_columns(src, delim or ("\\t" if lang == "tsv" else ","))',
     '        cols = _csv_columns(src, delim or ("," if lang == "tsv" else "\\t"))'),
    ("S31-8 a non-header first line is believed (the guard is off)", "subproto/graph.py",
     '    return [c.strip() for c in (rows[0] if rows else [])\n            if COLUMN_RE.match(c.strip())][:200]',
     "    return [c.strip() for c in (rows[0] if rows else []) if c.strip()][:200]"),
    ("S31-9 a data file's *values* leak into its hints", "subproto/graph.py",
     """        words = ([k.lower() for k in keys] if lang in DATA_LANGS else
                 [x.lower() for x in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", src)])""",
     """        words = ([x.lower() for x in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", src)]
                 if lang not in DATA_LANGS else
                 [k.lower() for k in keys] + [x.lower() for x in
                     re.findall(r'"([^"\\n]{1,40})"\\s*: "[^"]*"', src)])"""),
    ("S31-10 a key name is a symbol only if the query says it whole (no word split)",
     "subproto/graph.py",
     "    names = list(dict.fromkeys(paths))\n    syms = []\n    for name in names:\n        for w in _title_words(name):",
     "    names = list(dict.fromkeys(paths))\n    syms = []\n    for name in names:\n        for w in [name.lower()]:"),
    ("S31-11 the doc line names one key, so the file's shape is lost", "subproto/graph.py",
     '    doc = "keys: " + ", ".join(names[:12])',
     '    doc = "keys: " + ", ".join(names[:1])'),
    # ------------------------------------------- S32: the compiler's own latency work
    ("S32-1 the coupling scan is confined to a run, and that confinement is the fix",
     "subproto/protocol.py",
     "                    for p in PATH_RE.findall(run.group(0)):",
     "                    for p in PATH_RE.findall(text):"),
    ("S32-2 a hit is read as a whole path instead of the run around it",
     "subproto/protocol.py",
     "            start = at\n            while start > 0:",
     "            start = at\n            while False:"),
    ("S32-3 the derived view is cached *inside* the index, so the index cannot be written",
     "subproto/graph.py",
     '    _VIEW["view"] = view',
     '    graph["_scoring_view"] = _VIEW["view"] = view'),
    ("S32-4 a rebuilt index reuses the previous index's scoring view", "subproto/graph.py",
     '    if nodes is _VIEW["nodes"] and graph.get("built_at") == _VIEW["built_at"]:',
     '    if _VIEW["view"]:'),
    ("S32-5 a path the human typed is never searched for in the traffic",
     "subproto/compiler.py",
     '    for q in query_paths:\n        needles.append(q.rsplit("/", 1)[-1])',
     "    for q in query_paths:\n        pass"),
    ("S32-6 a ranked file's basename is never searched for in the traffic",
     "subproto/compiler.py",
     "        needles.append(base)",
     "        pass"),
    ("S32-7 graph evidence is scored but never booked into the protected set",
     "subproto/compiler.py",
     "        if best >= EVIDENCE_MIN:\n            ev.append((best, c))",
     "        if False:\n            pass"),
    ("S32-8 a partial name couples: the suffix test loses its `/` anchor",
     "subproto/compiler.py",
     '                if p == r or p.endswith(slash) or r.endswith("/" + p):',
     "                if p == r or p.endswith(slash) or r.endswith(p):"),
    ("S32-9 the coupling is computed and then thrown away", "subproto/compiler.py",
     "        value += 0.5 * best",
     "        value += 0.0"),
    ("S32-10 the hint tier of scoring disappears with the set rewrite", "subproto/graph.py",
     "        score += 0.6 * len((rest - path_hits) & hints)",
     "        score += 0.0 * len((rest - path_hits) & hints)"),
    # ------------------------------------------- S33: versions, health gate, stamps
    ("S33-1 `use` forgets which version it replaced", "subproto/systemone/versions.py",
     '    manifest["previous"] = manifest.get("active")\n    manifest["active"] = label\n',
     '    manifest["active"] = label\n'),
    ("S33-2 rolling back with nothing behind it pretends to succeed", "subproto/systemone/versions.py",
     '    if manifest.get("active") is None and manifest.get("previous") is None:\n',
     '    if False:\n'),
    ("S33-3 an active naming an unregistered version is trusted", "subproto/systemone/versions.py",
     '        if value not in seen:\n',
     '        if False:\n'),
    ("S33-4 a corrupt manifest is swallowed instead of reported", "subproto/systemone/versions.py",
     '        out["warnings"].append("%s is not readable JSON; starting from an empty "\n'
     '                               "manifest (the file is left alone)" % os.path.basename(target))\n',
     ''),
    ("S33-5 the atomic rename is skipped, so the write never lands", "subproto/systemone/versions.py",
     '    os.replace(tmp, target)\n',
     ''),
    ("S33-6 re-registering a version rewrites its history", "subproto/systemone/versions.py",
     '             "added": (existing or {}).get("added") or (today or time.strftime("%Y-%m-%d")),\n',
     '             "added": (today or time.strftime("%Y-%m-%d")),\n'),
    ("S33-7 a failed /health is ignored and the dead version is used", "subproto/systemone/versions.py",
     '        if ok:\n            if i:\n',
     '        if True:\n            if i:\n'),
    ("S33-8 previous is preferred over a live active version", "subproto/systemone/versions.py",
     '    wanted = [manifest.get("active"), manifest.get("previous")]\n',
     '    wanted = [manifest.get("previous"), manifest.get("active")]\n'),
    ("S33-9 degradation happens without saying what failed", "subproto/systemone/versions.py",
     '        notes.append("%s: /health failed (%s)" % (label, reason))\n',
     ''),
    ("S33-10 the manifest outranks a globally pinned model", "subproto/systemone/versions.py",
     '    return bool((getattr(config, "model", None) or "").strip()\n'
     '                or (getattr(config, "laya_url", None) or "").strip())\n',
     '    return False\n'),
    ("S33-11 the manifest outranks a per-slot pin", "subproto/systemone/versions.py",
     '    if slot and registry.is_pinned(config, slot):\n        return True\n',
     '    if slot and registry.is_pinned(config, slot):\n        return False\n'),
    ("S33-12 a dead pin degrades silently, one decision at a time", "subproto/systemone/versions.py",
     '                notes.append("pinned %s is unreachable (%s); every decision it would have "\n'
     '                             "answered says heuristic" % (label, reason))\n',
     ''),
    ("S33-13 the version column degenerates into the label", "subproto/systemone/versions.py",
     '    return (entry or {}).get("version") or label\n',
     '    return label\n'),
    ("S33-14 any tier string is accepted", "subproto/systemone/versions.py",
     '    if tier not in TIERS:\n        raise ValueError("tier must be %s (not %r)" % (" or ".join(TIERS), tier))\n',
     '    if False:\n        raise ValueError("tier must be %s (not %r)" % (" or ".join(TIERS), tier))\n'),
    ("S33-15 selecting an unregistered version is allowed", "subproto/systemone/versions.py",
     '    if label not in known:\n',
     '    if False:\n'),
    ("S33-16 a per-slot stamp follows the wish, not the answer", "subproto/engine.py",
     '        for d in decisions:\n            d["model_version"] = self.version_for(d.get("backend"))\n',
     '        for d in decisions:\n            d["model_version"] = self.model_version\n'),
    ("S33-17 the compiled arm stops stamping versions", "subproto/engine.py",
     '        for d in decisions:\n            d["model_version"] = self.version_for(d.get("backend"))\n',
     '        for d in decisions:\n            if d.get("compiled"):\n                continue\n'
     '            d["model_version"] = self.version_for(d.get("backend"))\n'),
    ("S33-18 the Router cannot see registered versions", "subproto/systemone/router.py",
     '            adapters = versions.configured_adapters(config, self.manifest)\n',
     '            adapters = registry.configured_adapters(config)\n'),
    ("S33-19 pre-stamp decisions vanish from the version table", "subproto/report.py",
     '            version = d.get("model_version") or "unrecorded"\n',
     '            version = d.get("model_version")\n            if not version:\n                continue\n'),
    ("S33-20 the export drops the version it was given", "subproto/dataset.py",
     '                "backend": d.get("backend"),\n                "model_version": d.get("model_version"),\n',
     '                "backend": d.get("backend"),\n'),
    ("S33-21 an empty active silently reinstates the spare version", "subproto/systemone/versions.py",
     '    if not manifest.get("active"):\n',
     '    if False:\n'),
    ("S31-12 `.json` is truncated to `.js` again, so a data read cannot couple to its node",
     "subproto/protocol.py",
     '|csv|tsv|ya?ml|toml|md|html|css|sh|vue|svelte)(?![A-Za-z0-9_])")',
     '|csv|tsv|ya?ml|toml|md|html|css|sh|vue|svelte)")'),
   # ------------------------------------------- retrain.py / cli.py: the cadence (S34)
    ("S34-1 a refusal or a dry run starts the cooldown clock", "subproto/retrain.py",
     '            if r.get("status") == "ran" and r.get("exit") == 0]\n',
     '            if r.get("status") in ("ran", "refused", "planned")\n'),
    ("S34-2 an unreadable history reads as an empty one, so the cadence invents a clean slate",
     "subproto/retrain.py",
     '                        "is recorded" % (os.path.basename(target), str(exc)[:90]))\n        return None\n',
     '                        "is recorded" % (os.path.basename(target), str(exc)[:90]))\n        return []\n'),
    ("S34-3 an unreadable history is overwritten with the one new row", "subproto/retrain.py",
     '    if rows is None:\n        raise ValueError(warn[0] if warn else "the history file is unreadable")\n',
     '    if rows is None:\n        rows = []\n'),
    ("S34-4 the fingerprint cannot tell train=A val=B from train=B val=A", "subproto/retrain.py",
     '    for marker, rows in ((b"#train\\n", train), (b"#val\\n", val)):\n',
     '    for marker, rows in ((b"", train), (b"", val)):\n'),
    ("S34-5 a slot that answers one way only is still counted as examples", "subproto/retrain.py",
     '    if unbalanced:\n        reasons.append("a slot with only one answer teaches the answer, not the "\n',
     '    if False:\n        reasons.append("a slot with only one answer teaches the answer, not the "\n'),
    ("S34-6 the per-answer floor prints but does not gate", "subproto/retrain.py",
     '    if short_answers:\n        reasons.append("under %d in both answers: %s" % (\n',
     '    if False:\n        reasons.append("under %d in both answers: %s" % (\n'),
    ("S34-7 the wait is advice, not a rule", "subproto/retrain.py",
     '        elif days < min_days:\n',
     '        elif False:\n'),
    ("S34-8 an unchanged split is retrained anyway", "subproto/retrain.py",
     '        if prev.get("split_sha") == sha:\n',
     '        if False:\n'),
    ("S34-9 the printed command breaks on this project's own path", "subproto/retrain.py",
     '    return " ".join(shlex.quote(a) for a in argv)\n',
     '    return " ".join(str(a) for a in argv)\n'),
    ("S34-10 a version label is reused for a different split", "subproto/retrain.py",
     '    return "lora-v%d" % (max(used) + 1 if used else 1)\n',
     '    return "lora-v%d" % 1\n'),
    ("S34-11 the record forgets why it refused", "subproto/retrain.py",
     '        "reasons": list(state["reasons"]),\n        "notes": list(state["notes"]),\n',
     '        "reasons": [],\n        "notes": [],\n'),
    ("S34-12 the toolchain says weights and MLX are there when they are not", "subproto/retrain.py",
     '        out["mlx"] = bool(trainer.mlx_available())\n',
     '        out["mlx"] = True\n'),
    ("S34-13 a trainer that never started is reported as exit 0", "subproto/retrain.py",
     '        return subprocess.call(shlex.split(command_line),\n'
     '                               stdout=sys.stderr if chatter_to_stderr else None)\n',
     '        subprocess.call(shlex.split(command_line))\n        return 0\n'),
    ("S34-19 --json glues the trainer's progress line onto the front of the document",
     "subproto/retrain.py",
     '                               stdout=sys.stderr if chatter_to_stderr else None)\n',
     '                               stdout=None)\n'),
    ("S34-14 --run ignores the cadence it just printed", "subproto/cli.py",
     '    if args.run:\n        if state["ready"]:\n',
     '    if args.run:\n        if True:\n'),
    ("S34-15 --run names a split it never wrote", "subproto/cli.py",
     '            wrote = retrain.write_split(config, split_kw)\n',
     '            wrote = None\n'),
    ("S34-16 every status check ages the cadence log by a phantom plan", "subproto/cli.py",
     '    elif args.record:\n        status = "planned"\n',
     '    if True:\n        status = "planned"\n'),
    ("S34-17 a run that did not finish becomes a selectable version", "subproto/cli.py",
     '        if (label and label != retrain.PLANNED and row.get("status") == "ran"\n                and row.get("exit") == 0):\n',
     '        if label and label != retrain.PLANNED:\n'),
    ("S34-18 a personal fine-tune is registered as a foundation model", "subproto/cli.py",
     '                    manifest, label, url=args.url, tier=args.tier or "personal",\n',
     '                    manifest, label, url=args.url, tier=args.tier or "foundation",\n'),
    # --------------------------------------------------- cli.py: `show` (T38)
    # A pointer is an address in one particular list. Resolving it in the other one
    # prints a neighbouring turn as the item that was cut, which is the exact
    # overclaim the retention proof exists to avoid.
    ("T38-1 a drop's message index resolves against the raw body, not the flattened list",
     "subproto/cli.py",
     '    msgs = protocol.normalize_messages(body)\n',
     '    msgs = [(m.get("role"), m.get("content"), "unknown")\n'
     '            for m in (body.get("messages") or [])]\n'),
    ("T38-2 a pointer whose kind disagrees with what it indexes still prints a quote",
     "subproto/cli.py",
     '    if str(pointer).rsplit(":", 1)[-1].split("#", 1)[0] != mkind:\n',
     '    if False:\n'),
    ("T38-3 a decision reports one count where the rows below it show two",
     "subproto/cli.py",
     '        figure = "" if head is None else "%d kept  %d cut  " % head\n',
     '        figure = "" if head is None else "%d kept  " % (head[0],)\n'),
    ("T38-4 a request that is not there exits 0", "subproto/cli.py",
     '        say(style.reason("no request %d in %s — subproto report lists the ids"\n'
     '                         % (args.request_id, config.db_path),\n'
     '                         indent=style.INDENT, color=color))\n'
     '        return 1\n',
     '        say(style.reason("no request %d in %s — subproto report lists the ids"\n'
     '                         % (args.request_id, config.db_path),\n'
     '                         indent=style.INDENT, color=color))\n'
     '        return 0\n'),
    ("T38-5 a page with no stored body quotes nothing and says nothing about it",
     "subproto/cli.py",
     '    if body is None and row.get("body_sha"):\n'
     '        say("")\n'
     '        say(style.prose("the pointers above are real and the counts are measured, but "\n'
     '                        "this request\'s body was never stored, so the page cannot show "\n'
     '                        "the text they name — that is what --store-bodies is for.",\n'
     '                        indent=style.INDENT, color=color))\n',
     ''),
    # -------------------------------------- S39: the checkpoint's question shape
    # `laya_server` translates between two vocabularies — the slot's option names and
    # Laya's typed criteria — and every one of those translations can lose an option
    # or invent a device that aborts the process. The stub runtime makes them gateable
    # without 842 MB of weights.
    ("S39-1 an option's underscores reach the encoder as part of the word",
     "subproto/laya_server.py",
     '    return " ".join(str(name).replace("_", " ").split())',
     '    return str(name)'),
    ("S39-2 two options with one name are asked about as one option",
     "subproto/laya_server.py",
     '            key = "%s#%d" % (label, i) if counts[label] > 1 else label',
     '            key = label'),
    ("S39-3 an empty option name is asked about as nothing",
     "subproto/laya_server.py",
     '        labels = [n if n.strip() else "option %d" % i for i, n in enumerate(names)]',
     '        labels = list(names)'),
    ("S39-4 the answer comes back keyed by the question, not the option",
     "subproto/laya_server.py",
     '        return dict((name, float(probs.get(key, 0.0))) for key, name in pairs)',
     '        return dict((key, float(probs.get(key, 0.0))) for key, name in pairs)'),
    ("S39-5 the per-option shape leaks its repaired key into the reply",
     "subproto/laya_server.py",
     '            probs[name] = float((ans.get("probabilities") or {}).get("keep", 0.0))',
     '            probs[key] = float((ans.get("probabilities") or {}).get("keep", 0.0))'),
    ("S39-6 the port opens before the weights are paid for",
     "subproto/laya_server.py",
     '        self.warm()\n', '        pass\n'),
    ("S39-7 the default device is the one that aborts the process",
     "subproto/laya_server.py",
     '        self.device = device or os.environ.get("LAYA_DEVICE") or "cpu"',
     '        self.device = device or os.environ.get("LAYA_DEVICE")'),
    ("S39-8 mlx is answered with a stand-in instead of a refusal",
     "subproto/laya_server.py",
     '    if requested == "mlx":\n        raise RuntimeError(',
     '    if requested == "mlx":\n        return "mlx"\n    if False:\n'
     '        raise RuntimeError('),
    # --------------------------------- S40: what the benches measured and printed
    # The Router spends the median because the budget is a per-decision timeout, and
    # both benches print verdicts computed from their own rows — so a row has to be
    # able to contradict the sentence, and an unanswered backend has to be able to
    # print nothing rather than a flattering nothing.
    ("S40-1 the latency budget spends the tail instead of the median",
     "subproto/systemone/router.py",
     '        value = value.get("p50")', '        value = value.get("max")'),
    ("S40-2 a latency curve is not something the Router can compare",
     "subproto/systemone/router.py",
     '    if isinstance(value, dict):\n        value = value.get("p50")',
     '    if False:\n        value = value.get("p50")'),
    ("S40-3 a backend that never replied is scored as keeping nothing",
     "bench/ablation.py",
     '            if not probs and getattr(adapter, "last_error", None):',
     '            if False:'),
    ("S40-4 an unanswered case is counted as an answered one", "bench/ablation.py",
     '                no_answer[label] += 1\n', ''),
    ("S40-5 an unmeasured metric prints as a measured one", "bench/ablation.py",
     '    return "%.3f" % v if v is not None else "—"', '    return "%.3f" % v'),
    ("S40-6 agreement is divided by a zero that was never checked",
     "bench/ablation.py",
     '                             "agreement": round(agree[label] / float(scored[label]), 4)\n'
     '                                          if scored[label] else None,',
     '                             "agreement": round(agree[label] / float(scored[label] or 1), 4),'),
    ("S40-7 the corpus stops reporting its own ceiling", "bench/ablation.py",
     '        if gold == hk:\n            identical += 1',
     '        if gold != hk:\n            identical += 1'),
    ("S40-8 the ceiling counts cases instead of decisions", "bench/ablation.py",
     '        decided += len(case["tools"])', '        decided += 1'),
    ("S40-9 the fits column reads a median the budget was not measured against",
     "bench/laya_latency.py",
     '                        "yes" if lt["p95"] <= budget else "no",',
     '                        "yes" if lt["p50"] <= budget else "no",'),
    ("S40-10 a shape that was never shipped is labelled as if it was",
     "bench/laya_latency.py",
     '                     % (row["shape"], "" if row["shipped"] else " (not shipped)",',
     '                     % (row["shape"], " (not shipped)" if row["shipped"] else "",'),
    ("S40-11 the cost ratio compares one shape's tail to the other's median",
     "bench/laya_latency.py",
     '                    round(m["shapes"][1]["ms"]["p50"] / choice["ms"]["p50"])',
     '                    round(m["shapes"][1]["ms"]["p95"] / choice["ms"]["p50"])'),

    # ---------------------------------------------------------------- S41: doctor
    # A diagnostic earns its place by being able to say *no*. Every row it prints is
    # a measurement (a bind, a write, a probe), so each of those is a rule here: a
    # check that cannot fail is the same defect as a count that cannot be wrong.
    ("T41-1 a red check still exits 0", "subproto/doctor.py",
     '    return 1 if any(r["state"] in RED for r in rows) else 0\n',
     '    return 0\n'),
    ("T41-2 the data dir is judged by whether it exists, not by writing it",
     "subproto/doctor.py",
     '        with open(probe, "w") as handle:\n'
     '            handle.write("witness")\n'
     '        os.remove(probe)\n',
     ''),
    ("T41-3 a configured backend that will not answer reads as healthy",
     "subproto/doctor.py",
     '        rows.append(_row("model backends", names, style.DOWN, url,\n',
     '        rows.append(_row("model backends", names, None, url,\n'),
    ("T41-4 the key's material reaches the page a bug report pastes",
     "subproto/doctor.py",
     '                    ", ".join(present),\n',
     '                    ", ".join("%s=%s" % (n, os.environ[n]) for n in present),\n'),
    ("T41-5 asking for any free port falls through to the default",
     "subproto/doctor.py",
     '    rows = check_all(config, port=want if want is not None else config.port)',
     '    rows = check_all(config, port=want or config.port)'),
    ("T41-6 the json document asserts its own ok", "subproto/doctor.py",
     '    return {"ok": not any(r["state"] in RED for r in rows),',
     '    return {"ok": True,'),
    ("T41-7 --json prints the page instead of the document", "subproto/doctor.py",
     '    if getattr(ns, "json", False):\n', '    if False:\n'),
    ("T41-8 a bind that failed is reported as a free port", "subproto/doctor.py",
     '    except OSError:\n        return False, port\n',
     '    except OSError:\n        return True, port\n'),
    ("T41-9 --version prints a number and not the machine it ran on",
     "subproto/cli.py",
     '        print("subproto %s (python %s, %s %s)" % (\n'
     '            __version__, ".".join(str(n) for n in sys.version_info[:3]),\n'
     '            platform.system().lower(), platform.machine().lower()))\n',
     '        print("subproto %s" % __version__)\n'),
    ("T41-10 the documented command is not the one the parser answers",
     "subproto/cli.py",
     '          "doctor": cmd_doctor}[args.cmd]\n', '          }[args.cmd]\n'),
    # S42: the demo is the first command a stranger runs, on a machine that already
    # has services on it. Its two rules are that it does not collide (a port, a
    # SQLite file) and that it says where it wrote.
    #
    # One more mutant was probed here and dropped: leaving the probe socket unclosed.
    # Nothing catches it, because CPython's refcounting closes it when the helper
    # returns — the defect describes itself away, so a test for it would only be a
    # test of the implementation.
    ("S42-1 a port someone else holds is the demo's problem to fail",
     "subproto/cli.py",
     '    except OSError:\n        sock.bind(("127.0.0.1", 0))\n'
     '        return sock.getsockname()[1]\n',
     '    except OSError:\n        return hint\n'),
    ("S42-2 two demos share one fixed SQLite file", "subproto/cli.py",
     '        home = tempfile.mkdtemp(prefix="subproto-demo-")\n',
     '        home = "/tmp/subproto-demo"\n'),
    ("S42-3 the demo hides the directory it wrote, so the run cannot be re-read",
     "subproto/cli.py",
     '    if sandbox:\n        print("")\n',
     '    if False:\n        print("")\n'),
    ("S42-4 the mock upstream takes port+1 without asking whether it is free",
     "subproto/cli.py",
     "    n = demo.replay(config, mock_port=_port_or_next(config.port + 1))\n",
     "    n = demo.replay(config)\n"),
    ("S42-5 the page dies on a console whose encoding is not this machine's",
     "subproto/cli.py",
     "def main(argv=None):\n    _utf8_streams()\n",
     "def main(argv=None):\n"),
    ("S42-6 an extension written in another case is no extension at all",
     "subproto/graph.py",
     "    return os.path.splitext(name)[1].lower()\n",
     "    return os.path.splitext(name)[1]\n"),
    ("S42-7 the demo reports its traffic before every row has landed",
     "subproto/demo.py",
     "        if seen >= n:\n            return seen\n",
     "        if True:\n            return seen\n"),
    ("S42-8 a host with no load average is printed as a load average of None",
     "bench/laya_latency.py",
     '    if c["load_avg_start"] and c["load_avg_end"]:\n',
     "    if True:\n"),
    ("S42-9 the curve stops reading its host and still claims one",
     "bench/laya_latency.py",
     "        return [round(x, 2) for x in os.getloadavg()]\n",
     "        return None\n"),
    ("S42-10 a host with no getloadavg at all crashes the bench",
     "bench/laya_latency.py",
     "    except (AttributeError, OSError):\n",
     "    except OSError:\n"),
    # S43: two pages that describe the machine rather than the traffic. `doctor` reads
    # its Python floor out of a file that an installed wheel does not have, and
    # `retrain` explains an absence by naming where it looked. Both claims are only
    # worth printing if the page says where the fact came from.
    ("S43-1 a missing trainer does not say where it was looked for",
     "subproto/retrain.py",
     '    if not tc["trainer"]:\n',
     "    if False:\n"),
    ("S43-2 the trainer note loses the path and keeps the sentence",
     "subproto/retrain.py",
     '        lines.append(st.note(tc["trainer_path"],\n',
     '        lines.append(st.note("",\n'),
    ("S43-3 the floor row claims it read a file it could not open",
     "subproto/doctor.py",
     '    except OSError:\n        return FLOOR, "the shipped default"\n',
     '    except OSError:\n        return FLOOR, os.path.basename(PYPROJECT)\n'),
    ("S43-4 the floor is quoted from the constant, not parsed from the file",
     "subproto/doctor.py",
     "    return (int(match.group(1)), int(match.group(2))), os.path.basename(PYPROJECT)\n",
     "    return FLOOR, os.path.basename(PYPROJECT)\n"),
    # S46: `doctor` explains an absent checkpoint, and the explanation has to be the one
    # that is true of the interpreter running it. Naming "Python >= 3.10" to a reader
    # already on 3.11 sends them to fix something that is not broken.
    ("S46-B7 the laya row blames the interpreter version on a version that is new enough",
     "subproto/doctor.py",
     "    if py < (3, 10):\n",
     "    if False:\n"),
]

TESTS = ["subproto/tests/test_implicit.py", "subproto/tests/test_learn.py",
         "subproto/tests/test_dataset_split.py", "subproto/tests/test_report_sections.py",
         "subproto/tests/test_graph.py", "subproto/tests/test_compiler.py",
         "subproto/tests/test_versions.py", "subproto/tests/test_router.py",
         "subproto/tests/test_systemone.py", "subproto/tests/test_retrain.py",
         # The grid is a rule now: a state word printed without its meaning, or a page
         # that only renders for a tty, is the same class of defect as a wrong count.
         "subproto/tests/test_style.py",
         # `show` is the surface that turns a retention proof back into bytes, so its
         # pointer resolution is a rule too (T38).
         "subproto/tests/test_show.py",
         # S39/S40: the checkpoint's question shape, the answer's keys, and the two
         # benches whose prose is computed from their own rows.
         "subproto/tests/test_laya.py", "subproto/tests/test_laya_real.py",
         "subproto/tests/test_ablation.py", "subproto/tests/test_laya_latency.py",
         # T41: the diagnostic. Its rows are claims about a machine, and each one is
         # measured by a socket, a write or a probe that this file can break.
         "subproto/tests/test_doctor.py",
         # S42: the demo, which is the only rule file's subject that needs a live
         # proxy. It costs a few seconds per mutant — worth it, because a stranger's
         # first command failing on a busy port is exactly the defect this suite
         # exists to keep out of a release.
         "subproto/tests/test_cli_demo_home.py"]


def _read(path):
    # `newline=""` and an explicit encoding: this file's central claim is that the
    # sources come back byte-identical, and Python's default text mode would rewrite
    # line endings (and fail outright on a cp1252 locale) before a mutant ran.
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _run(label):
    # The child is told to speak UTF-8 and the parent reads it as UTF-8: the sources
    # under test carry em dashes and set words, and a cp1252 console would break on
    # them before the mutant's own result was ever printed.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest"] + TESTS + ["-q", "--no-header"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def _last(line_stdout):
    rows = [l for l in line_stdout.strip().splitlines() if l.strip()]
    return rows[-1] if rows else "(no output)"


def main():
    originals = {}
    # A duplicated entry kills nothing twice: it inflates the count and hides the rule
    # that has no mutant at all. Check the list against itself before trusting it.
    ids, pairs = set(), set()
    dupes = []
    for _id, path, old, new in MUTANTS:
        if _id in ids or (path, old, new) in pairs:
            dupes.append(_id)
        ids.add(_id)
        pairs.add((path, old, new))
    if dupes:
        print("REFUSING TO RUN: duplicate mutant entries: %s" % ", ".join(dupes))
        return 2

    for _id, path, old, new in MUTANTS:
        if path not in originals:
            originals[path] = _read(path)

    base = _run("baseline")
    print("baseline over %d mutants: %s" % (len(MUTANTS), _last(base.stdout)))
    if base.returncode != 0:
        print("\nREFUSING TO RUN: the baseline is not green, so every 'kill' below "
              "would be vacuous.")
        return 2

    failures = []
    for mid, path, old, new in MUTANTS:
        src = originals[path]
        n = src.count(old)
        if n != 1:
            print("%-12s %s  (anchor found %d times, need exactly 1)" % ("PATCH-MISS", mid, n))
            failures.append("anchor not unique: " + mid)
            continue
        _write(path, src.replace(old, new, 1))
        r = _run(mid)
        killed = r.returncode != 0
        print("%-12s %s" % ("KILLED" if killed else "SURVIVED", mid))
        if not killed:
            failures.append("survived: " + mid)

    for path, src in originals.items():
        _write(path, src)
    dirty = [p for p, src in originals.items()
             if hashlib.sha256(open(p, "rb").read()).hexdigest()
             != hashlib.sha256(src.encode()).hexdigest()]
    print("\nsources restored byte-identical: %s" % ("yes" if not dirty else "NO -> %s" % dirty))
    if dirty:
        failures.append("sources not restored")

    after = _run("after")
    print("baseline again after restore: %s" % _last(after.stdout))
    if after.returncode != 0:
        failures.append("suite broken after restore")

    print("MUTATION GATE: %s" % ("OK - all %d mutants killed" % len(MUTANTS)
                                 if not failures else "PROBLEM"))
    if not failures:
        print("MUTATION GATE: OK")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
