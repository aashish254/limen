#!/usr/bin/env python3.11
"""Mutation gate for the label flywheel: edit the rule, the suite must fail.

`subproto/implicit.py` decides, from recorded traffic alone, that a drop was wrong;
`subproto/dataset.py` decides whether that verdict reaches the training set;
`subproto/report.py` and `subproto/cli.py` decide what the human is shown. Each is a
file of *rules*, and a test suite over rules can pass while the rule is inverted —
so each rule is broken here, one at a time, and must be caught.

Three properties this script enforces on itself:

1. The baseline must be green before any mutant runs. A mutant tested against a
   already-failing suite "kills" vacuously; that invalidates every result below it.
2. Each anchor must appear **exactly once** in its file. A second occurrence would
   leave the mutated line untouched and report a false kill.
3. The source is restored byte-for-byte, and verified, whether or not the run passed.

Usage: `python3.11 bench/mutation_gate.py` (exit 1 if any mutant survives or patches).
"""

import hashlib
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
     '        if cov.get("disagreements"):',
     '        if False:'),

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
    ("S31-12 `.json` is truncated to `.js` again, so a data read cannot couple to its node",
     "subproto/protocol.py",
     '|csv|tsv|ya?ml|toml|md|html|css|sh|vue|svelte)(?![A-Za-z0-9_])")',
     '|csv|tsv|ya?ml|toml|md|html|css|sh|vue|svelte)")'),
]

TESTS = ["subproto/tests/test_implicit.py", "subproto/tests/test_learn.py",
         "subproto/tests/test_dataset_split.py", "subproto/tests/test_report_sections.py",
         "subproto/tests/test_graph.py", "subproto/tests/test_compiler.py"]


def _run(label):
    return subprocess.run([sys.executable, "-m", "pytest"] + TESTS + ["-q", "--no-header"],
                          capture_output=True, text=True)


def _last(line_stdout):
    rows = [l for l in line_stdout.strip().splitlines() if l.strip()]
    return rows[-1] if rows else "(no output)"


def main():
    originals = {}
    for _id, path, old, new in MUTANTS:
        if path not in originals:
            originals[path] = open(path).read()

    base = _run("baseline")
    print("baseline (%d tests): %s" % (len(MUTANTS), _last(base.stdout)))
    if base.returncode != 0:
        print("\nREFUSING TO RUN: the baseline is not green, so every 'kill' below "
              "would be vacuous.")
        return 2

    failures, files = [], {}
    for mid, path, old, new in MUTANTS:
        src = originals[path]
        n = src.count(old)
        if n != 1:
            print("%-12s %s  (anchor found %d times, need exactly 1)" % ("PATCH-MISS", mid, n))
            failures.append("anchor not unique: " + mid)
            continue
        files[path] = files.get(path, src) or src
        open(path, "w").write(src.replace(old, new, 1))
        r = _run(mid)
        killed = r.returncode != 0
        print("%-12s %s" % ("KILLED" if killed else "SURVIVED", mid))
        if not killed:
            failures.append("survived: " + mid)

    for path, src in originals.items():
        open(path, "w").write(src)
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
