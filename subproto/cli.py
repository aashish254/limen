import argparse
import json
import os
import platform
import socket
import sys
import tempfile
import threading
import time

from . import __version__, style
from .config import Config, HomeError
from .engine import ALL_SLOTS
from .telemetry import Telemetry


INJECT = {
    "claude": [
        "export ANTHROPIC_BASE_URL=http://127.0.0.1:{port}",
        "export CLAUDE_CODE_ENABLE_PROXY=1  # not required; base URL is enough",
    ],
    "codex": [
        'export OPENAI_BASE_URL=http://127.0.0.1:{port}/v1',
    ],
    "gemini": [
        'export GEMINI_BASE_URL=http://127.0.0.1:{port}/v1',
        'export GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:{port}/v1',
    ],
    "openai": ['export OPENAI_BASE_URL=http://127.0.0.1:{port}/v1'],
    "aider": [
        'export OPENAI_API_BASE=http://127.0.0.1:{port}/v1',
        'export ANTHROPIC_API_BASE=http://127.0.0.1:{port}',
    ],
    "generic": [
        "base_url = http://127.0.0.1:{port}/v1   # OpenAI-compatible setting",
        "base_url = http://127.0.0.1:{port}      # Anthropic-compatible setting",
    ],
}


def _is_date(value):
    import datetime
    try:
        datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return False
    return True


def _count_requests(db_path):
    """How many rows a command is about to overwrite. 0 when there is nothing to read."""
    if not os.path.exists(db_path):
        return 0
    import sqlite3
    try:
        tel = Telemetry(db_path)
        try:
            rows = tel.query("SELECT COUNT(*) AS n FROM requests")
            return int(rows[0]["n"]) if rows else 0
        finally:
            tel.close()
    except sqlite3.Error:
        # A home the user pointed at may hold a database this version cannot read. The
        # command still works — it replaces the file — so the count is cosmetic here.
        return 0


def _graph_arg(args):
    """Which index a command runs with: `--graph` wins, then this directory's own.

    The engine deliberately does not look at the working directory, so the one place
    that decides whether an installed index counts is here, where a reader can see it.
    """
    from . import graph as graph_mod
    return getattr(args, "graph", None) or graph_mod.ambient()


def _open_telemetry(config):
    config.ensure_dirs()
    return Telemetry(config.db_path)


def _fmt(n):
    return "{:,}".format(int(n))


def _shown_path(path, home=None):
    """A stored body's path, spelled the way a reader would type it.

    The page has a 100-column measure and this line is the one that can break it. A path
    under the home prints as `~/…`, which both fits and pastes into a shell as-is; `--json`
    keeps the absolute path, because a script has no home to expand (S46/B8).
    """
    if not path:
        return path
    root = home if home is not None else os.path.expanduser("~")
    if root and path.startswith(root + os.sep):
        return "~" + path[len(root):]
    return path


def cmd_up(args):
    from .proxy import serve
    from .engine import Engine

    color = style.enabled()
    config = Config.load(args.config, port=args.port, data_dir=args.home,
                         store_bodies=True if args.store_bodies else None,
                         laya_url=args.laya_url, model=args.model,
                         router=True if args.router else None)
    config.ensure_dirs()
    telemetry = _open_telemetry(config)
    engine = None
    if args.slots:
        try:
            engine = Engine(config, graph_path=_graph_arg(args))
        except (OSError, ValueError) as e:
            # A --graph value is user input, so a bad one is said as a state, not
            # as a traceback: the reader has to see which path they handed over.
            print(style.header("up", "the graph did not load", color=color))
            print(style.reason("%s: %s" % (args.graph, e),
                              indent=style.INDENT, color=color))
            return 1
    srv = serve(config, telemetry, engine)
    # The engine's own answer, not the environment's: `SUBPROTO_ENFORCE` turns the
    # default pair on without naming them, and a banner that read only SUBPROTO_APPLY
    # printed "observation mode" over a proxy that was rewriting the request.
    applied = ",".join(engine.enforcing) if engine is not None else None
    print("")
    print(style.header("up", "listening on http://127.0.0.1:%d" % config.port,
                       color=color))
    print("")
    print(_cfg_row("data dir", config.data_dir, color))
    print(_cfg_row("bodies", "recording (gzip)" if config.store_bodies
                                 else "metadata only", color))
    print(_cfg_row("slots", style.state(style.ON if args.slots else style.OFF,
                                        color=color)
                   + ("" if args.slots else "  pure passthrough"),
                   color, styled=True))
    print(_cfg_row("enforcing", applied or _absent(
        style.OFF, "observation mode — it records, it never rewrites",
        color), color, styled=True))
    if applied:
        from .engine import ADVISORY_SLOTS
        advisory = [s.strip() for s in applied.split(",") if s.strip() in ADVISORY_SLOTS]
        if advisory:
            print(style.prose("%s is advisory-only: it records a tier, it never "
                              "rewrites the request" % ",".join(advisory),
                              indent=_UP_CONTENT, color=color))
    print(_cfg_row("graph", _absent(style.PRESENT, engine.graph_path, color)
                   if engine and engine.graph_path else _absent(
                       style.OFF, "no index — subproto graph <repo> makes one",
                       color), color, styled=True))
    if engine and (engine.backend or engine.model_notes
                   or engine.manifest.get("active")):
        ms = engine.model_status()
        health = ("up" if ms.get("ok") else
                  ("not available" if ms.get("configured") else "in-process"))
        print(_cfg_row("model", "%s  %s" % (
            ms.get("label") or "heuristic",
            style.state(health, color=color)), color, styled=True))
        print(style.note("%s%s" % (
            "version %s — " % ms["version"] if ms.get("version") else "",
            "selected by %s" % ms.get("source")), indent=_UP_CONTENT, color=color))
        if ms.get("slots"):
            print(style.prose("slots  %s" % "  ".join(
                "%s=%s" % kv for kv in sorted(ms["slots"].items())),
                indent=_UP_CONTENT, color=color))
        # A version that could not be used must be said out loud at startup, not left
        # inside the JSON or implied by each decision quietly recording `heuristic`.
        for note in engine.model_notes:
            print(style.reason(note, indent=_UP_CONTENT, color=color))
    print("")
    print(style.section("point an agent at it", indent=style.INDENT, color=color))
    for tool in (args.for_ or ["claude", "codex"]):
        print(style.sub(tool, indent=_UP_CONTENT, color=color))
        for line in [l.format(port=config.port)
                     for l in INJECT.get(tool, INJECT["generic"])]:
            print(style.action(line, indent=_UP_CONTENT + 2, color=color))
    print("")
    print(style.prose("Ctrl-C to stop. Then run: subproto report", indent=0,
                      color=color))
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        telemetry.close()


# The startup banner's label column is as wide as its longest label (`enforcing`),
# so every note, command and reason on that page hangs at its content column — not at
# the generic one, which would put the exports three columns left of the values.
_UP_LABEL_TAKE = 8
_UP_CONTENT = style.INDENT + (style.METRIC_W - _UP_LABEL_TAKE) + 2


def _cfg_row(label, value, color, styled=False):
    return style.row(label, value, label_w=style.METRIC_W - _UP_LABEL_TAKE,
                     color=color, styled=styled)


def _absent(state, why, color):
    """A switched-off thing, named by its state and explained in dim beside it.

    The colour goes on the one word that is a state; the sentence that says what
    follows from it never takes colour, or a page has two reasons to be amber.
    """
    return style.state(state, color=color) + "  " + style.paint(why, style.DIM, color)


def _history_versions(config):
    """{version: record} for runs that *finished* — the labels `--use` may select.

    A trained checkpoint has no endpoint until something serves it, so the
    manifest cannot know it exists. This is the one place the two stores meet:
    the cadence log says a version was produced, the manifest says whether it is
    answering. A `refused` or `planned` row is not a version and never becomes one.
    """
    from . import retrain

    out = {}
    for row in retrain.load_history(config) or []:
        label = row.get("version")
        if (label and label != retrain.PLANNED and row.get("status") == "ran"
                and row.get("exit") == 0):
            out[label] = row
    return out


def _models_edit(args, config, manifest, target):
    """Apply `--add` / `--use` / `--rollback` to the manifest and persist it.

    Returns (manifest, lines). Every failure raises before the file is written, so a
    rejected `--use` cannot leave the manifest pointing at something half-registered.
    """
    from . import systemone
    from .systemone import registry

    lines = []
    if args.add:
        if not (args.url or "").strip():
            # Documented as required, and a version with no url can never answer: the
            # page would list it and every decision would fall back with a reason.
            raise ValueError("--add needs --url: "
                             "subproto models --add %s --url http://127.0.0.1:8000 "
                             "--tier personal" % args.add)
        manifest, entry = systemone.add(manifest, args.add, url=args.url, tier=args.tier,
                                        note=args.note)
        lines.append("registered %s (%s tier, added %s)"
                     % (entry["label"], entry["tier"], entry["added"]))
    if args.use:
        label = (args.use or "").strip()
        known = {v.get("label") for v in manifest.get("versions") or []}
        if label not in known and label != registry.HEURISTIC:
            row = _history_versions(config).get(label)
            if row:
                manifest, _entry = systemone.add(
                    manifest, label, url=args.url, tier=args.tier or "personal",
                    version=label,
                    note="from training_history: split %s, %d train / %d val, git %s"
                         % (str(row.get("split_sha") or "")[:8], row.get("n_train") or 0,
                            row.get("n_val") or 0, row.get("git_sha") or "no git tree"))
                lines.append("registered %s from training_history (split %s…, "
                             "%d train / %d val, git %s)"
                             % (label, str(row.get("split_sha") or "")[:8],
                                row.get("n_train") or 0, row.get("n_val") or 0,
                                row.get("git_sha") or "no git tree"))
                if not (args.url or registry.configured_url(label, config)):
                    lines.append("note: %s has no endpoint to serve it — until one "
                                 "answers /health, every decision says heuristic" % label)
        manifest, message = systemone.use(manifest, args.use)
        lines.append(message)
    if args.rollback:
        manifest, message = systemone.rollback(manifest)
        lines.append(message)
    if lines:
        systemone.save(target, manifest)
        lines.append("wrote %s" % target)
        pin = os.environ.get("SUBPROTO_MODEL") or os.environ.get("LAYA_URL")
        if pin:
            lines.append("note: SUBPROTO_MODEL/LAYA_URL is set, so it still outranks the "
                         "manifest — unset it for this to take effect on the next `up`")
    return manifest, lines


def cmd_models(args):
    """List every System One backend, and the version manifest that picks between them."""
    from . import systemone

    color = style.enabled()

    config = Config.load(args.config, data_dir=args.home, model=args.model,
                         router=True if args.router else None)
    target = systemone.path(config)
    manifest = systemone.load(target)
    try:
        manifest, changes = _models_edit(args, config, manifest, target)
    except ValueError as exc:
        sys.stderr.write("models: %s\n" % exc)
        return 1
    st = systemone.status(config)
    desc = dict(systemone.describe(manifest, config), path=target)
    choice = systemone.select(config, manifest=manifest)
    router = systemone.Router(config, manifest=manifest)
    plan = router.plan() if router.available else None
    for line in changes:
        print(line)
    if changes:
        print("")
    if args.json:
        st["router"] = {"enabled": router.available, "budget_ms": router.budget_ms,
                        "evidence": sorted(router.evidence), "plan": router.plan()}
        st["manifest"] = desc
        st["selection"] = {"label": choice["label"], "version": choice["version"],
                           "source": choice["source"], "notes": choice["notes"]}
        print(json.dumps(st, indent=1))
        return 0
    versions = len(desc["versions"])
    print(style.header("models", "%d backends, %d version%s" % (
        len(st["adapters"]), versions, "s" if versions != 1 else ""), color=color))
    print("")
    # The page's one answer, on the grid rather than as a sentence with a colon in it.
    print(style.row("backend", style.state(st["active"], color=color), styled=True,
                    color=color))
    print("")
    print(style.section("backends", indent=style.INDENT, color=color))
    for row in st["adapters"]:
        # `*` is the same mark the version list uses: which one is answering now.
        mark = "*" if row["active"] else " "
        print("  %s %s" % (mark, style.table(
            row["name"], (row["where"], "", 0), name_w=14, indent=0, color=color)))
        print(style.note("%s%s" % (row["about"], ("  ->  " + ",".join(row["slots"]))
                                   if row.get("slots") else ""),
                         indent=style.SUB_INDENT + 2, color=color))
    print("")
    print(style.row("per slot", "  ".join("%s=%s" % kv
                                          for kv in sorted(st["slots"].items())),
                    color=color))
    print(style.prose("context/effort are answered by the code graph and request "
                      "shape, not by a model", color=color))
    print("")
    print(style.section("versions", "(%s)" % desc["path"], indent=style.INDENT,
                        color=color))
    if not desc["versions"]:
        print(style.prose("none registered — every decision is stamped with whatever "
                        "the selection above says", indent=style.SUB_INDENT,
                        color=color))
    for row in desc["versions"]:
        mark = "*" if row["active"] else ("=" if row["previous"] else " ")
        print("  %s %s" % (mark,
                           style.table(row["label"],
                                       (row["tier"], "", -10),
                                       (row["version"], "", -11),
                                       (row["endpoint"] or "no url", "", -34),
                                       (row["health"], "", 0),
                                       name_w=14, indent=0, color=color)))
    for warning in desc["warnings"]:
        print(style.reason(warning, color=color))
    if desc["versions"]:
        print(style.row("active", desc["active"] or "heuristic", color=color))
        print(style.row("previous", desc["previous"] or "-", color=color))
        if choice["source"] == "pin":
            print(style.reason("SUBPROTO_MODEL/config `model` is set: it outranks the "
                               "manifest, so %s is not answering"
                               % (desc["active"] or "the manifest"), color=color))
    for note in choice["notes"]:
        print(style.reason(note, color=color))
    print("")
    if plan:
        print(style.section("router", "ON (budget %s) — ranks configured backends by "
                            "measured slot precision" % (
                                ("%gms" % router.budget_ms)
                                if router.budget_ms else "none"),
                            indent=style.INDENT, color=color))
        for slot in sorted(plan):
            info = plan[slot]
            print(style.table(slot, (info["mode"], "", 7), (info["version"], "", 12),
                              (info["reason"], "", 0), name_w=14, color=color))
        print("")
    else:
        print(style.row("router", style.state(style.OFF, color=color)
                        + "  slots use the named model above "
                          "(SUBPROTO_MODEL[_<slot>])", color=color, styled=True))
        print(style.prose("turn on with SUBPROTO_ROUTER=on (or --router) to pick the "
                        "best measured backend per slot; ties and no-evidence stay "
                        "on heuristics", color=color))
        print("")
    print(style.section("select one", indent=style.INDENT, color=color))
    print(style.row("model", "subproto up --model laya", color=color))
    print(style.note("SUBPROTO_MODEL=http://host:port works for any "
                     "/health + /score server", color=color))
    print(style.row("per slot", "SUBPROTO_MODEL_COMPACT=djev subproto up --slots",
                    color=color))
    print(style.row("versions", "subproto models --add mlx-lora-v2 "
                                "--url http://host:port --tier personal",
                    color=color))
    print(style.row("switch", "subproto models --use mlx-lora-v2", color=color))
    print(style.row("undo", "subproto models --rollback", color=color))
    return 0


def cmd_report(args):
    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    from . import dataset, implicit, report
    labels = dataset.load_labels(config)
    # the regret section re-derives the harvest from local bodies rather than reading
    # the last stored one, so what prints here is what the traffic says *now*
    harvest = None if args.no_learn else implicit.harvest(config, telemetry)
    for name, value in (("--since", args.since), ("--until", args.until)):
        if value and not _is_date(value):
            # A window that cannot match anything is reported as "0 requests", which is
            # the same dead end a wrong --home gives you. Say which bound is wrong.
            print(style.header("report", "that is not a date", color=style.enabled()))
            print(style.reason('%s %s — the form is --since 2026-09-25' % (name, value),
                               indent=0, color=style.enabled()))
            telemetry.close()
            return 1
    summary = report.summarize(telemetry, since=args.since, until=args.until,
                               labels=labels, implicit=harvest)
    if args.json:
        print(json.dumps(report.render_json(summary), indent=1, default=str))
    else:
        color = style.enabled()
        print(report.render_text(summary, since=args.since, until=args.until,
                                color=color))
    telemetry.close()


def cmd_live(args):
    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    from . import live
    try:
        live.run(telemetry, interval=args.interval, once=args.once)
    finally:
        telemetry.close()
    return 0


def cmd_graph(args):
    from . import graph as graph_mod

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(style.prose("no such directory: %s" % root, indent=0,
                        color=style.enabled()))
        return 1
    started = time.time()
    g = graph_mod.build(root)
    target = args.out or os.path.join(root, ".subproto-graph.json")
    graph_mod.save(g, target)
    color = style.enabled()
    print(style.header("graph", os.path.basename(root), color=color))
    print("")
    print(style.row("files", g["file_count"], label_w=12, color=color))
    print(style.row("import edges", g["edge_count"], label_w=12, color=color))
    print(style.row("indexed in", "%.1fs" % (time.time() - started), label_w=12,
                    color=color))
    print(style.row("written", target, label_w=12, color=color))
    if args.install:
        config = Config.load(args.config, data_dir=args.home)
        config.ensure_dirs()
        key = os.path.basename(root)
        link = os.path.join(config.graph_dir, key + ".json")
        with open(target, "rb") as f:
            data = f.read()
        with open(link, "wb") as f:
            f.write(data)
        print(style.row("installed", link, label_w=12, color=color))
        print(style.note("point any command at it with --graph %s" % key,
                         indent=style.LABEL_W + 4, color=color))


def cmd_where(args):
    from . import graph as graph_mod

    root = None
    if args.graph:
        path = os.path.abspath(args.graph)
        if os.path.isdir(path):
            # Everyone reads `--graph` as "the repo", and `subproto compile` takes a
            # directory, so `where` must not die with IsADirectoryError on one.
            root = path
            path = graph_mod.index_path(root)
    else:
        # `--path` names the repo, so a missing index under it is the same case as
        # `--graph <repo>`: index it here and say so, rather than failing at a file
        # path the reader never typed.
        root = os.path.abspath(args.path or ".")
        path = graph_mod.index_path(root)
    if os.path.exists(path):
        g = graph_mod.load(path)
    elif root is None:
        # Only the reader knows which repo this file should have come from.
        print(style.prose("no graph at %s — run: subproto graph <repo>" % path,
                          indent=0), file=sys.stderr if args.json else sys.stdout)
        return 1
    else:
        # Not wrapped: both of these paths are things the reader copies out.
        print(style.note("no index at %s — built one in memory (cache it: "
                         "subproto graph %s)" % (path, root),
                         indent=0, color=style.enabled()), file=sys.stderr)
        g = graph_mod.build(root)
    query = " ".join(args.query) if isinstance(args.query, list) else (args.query or "")
    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            query = f.read() + "\n" + query
    elif not query and not sys.stdin.isatty():
        query = sys.stdin.read()
    hits = graph_mod.search(g, query or "", top_k=args.top)
    if args.json:
        print(json.dumps([{"file": r, "score": s, "why": w} for r, s, w in hits], indent=1))
    else:
        color = style.enabled()
        # The query belongs on the page: two of these side by side are otherwise
        # indistinguishable, and the scores only mean something next to their ask.
        print(style.header("where", style.clip(query, 52), color=color))
        print("")
        if not hits:
            print(style.prose("no file in the index matched that.",
                              indent=style.INDENT, color=color))
        for rel, score, why in hits:
            node = g["nodes"].get(rel, {})
            print(style.table("%.1f" % score, (rel, "", -52),
                              (",".join(why)[:44], "", 0), name_w=6,
                              indent=style.INDENT, color=color))
            doc = (node.get("doc") or "").strip()[:70]
            # Hang the qualifier under the path column (2 of indent + the 6-wide
            # score + the cell's leading space), not under some other block's margin.
            print(style.note("%d lines%s" % (
                node.get("lines") or 0, "  %s" % doc if doc else ""),
                indent=style.INDENT + 7, color=color))
    return 0


def cmd_compile(args):
    """v5 witness: compile one turn under a single budget, and show the proof."""
    from . import compiler, demo, graph as graph_mod, protocol
    from .proxy import analyze_request

    query = " ".join(args.task) if isinstance(args.task, list) else (args.task or "")
    if not query.strip():
        print(style.prose('nothing to compile — try: subproto compile "fix retry.py" '
                          '--graph <repo>', indent=0))
        return 1
    color = style.enabled()

    def say(line, err=False):
        # stdout is reserved for the witness: with --json it must stay parseable,
        # so every human-facing line before the document goes to stderr.
        print(line, file=sys.stderr if (args.json or err) else sys.stdout)

    say(style.header("compile", "compiled turn, one budget over messages, tools "
                                "and files", color=color))
    say("")

    root = os.path.abspath(args.path or ".")
    path = args.graph or graph_mod.index_path(root)
    if os.path.isdir(path):
        say(style.note("indexing %s …" % path, indent=0, color=color))
        started = time.time()
        g = graph_mod.build(path)
        say(style.note("%d files, %d import edges, %.1fs" % (
            g["file_count"], g["edge_count"], time.time() - started),
            indent=style.INDENT, color=color))
    elif os.path.exists(path):
        g = graph_mod.load(path)
    elif args.graph:
        # A line that carries a path is not wrapped: the reader has to copy it.
        say(style.note("no graph at %s — run: subproto graph <repo>" % path,
                       indent=0, color=color))
        return 1
    else:
        say(style.note("no index at %s — building one from %s (or pass --graph)"
                       % (path, root), indent=0, color=color))
        g = graph_mod.build(root)

    body = demo.synthetic_request(args.seed, jitter=False, turns=args.turns,
                                  shuffle=False)
    # The task becomes the live turn: what the compiler reads is what you typed.
    body["messages"] = list(body["messages"]) + [{"role": "user", "content": query}]
    analysis = analyze_request(body)
    new_body, decisions, proof = compiler.compile_turn(
        "anthropic", body, analysis, g, query, budget=args.budget,
        body_sha=protocol.sha256_12(protocol.dump_body(body)),
        enforce=("tool_gate", "compact", "context"))
    if proof is None:
        print(style.reason("nothing to compile: the request has no droppable "
                         "candidates", indent=0, color=color))
        return 1
    if proof["over_budget"]:
        print(style.header("compile", "over budget: nothing was cut", color=color))
        print("")
        print(style.reason("%s — the tail is sacred (I3)" % proof["reason"],
                           indent=style.INDENT, color=color))
        return 1

    by_kind = {}
    for row in proof["kept"]:
        by_kind.setdefault(row["kind"], [0, 0])[0] += 1
    for row in proof["dropped"]:
        by_kind.setdefault(row["kind"], [0, 0])[1] += 1
    if args.json:
        print(json.dumps({"proof": proof, "decisions": decisions,
                          "compiled_body": new_body}, indent=1))
        return 0
    saved = proof["tokens_before"] - proof["tokens_after"]
    pct = 100.0 * saved / max(1, proof["tokens_before"])
    print(style.row("task", query[:68] + ("…" if len(query) > 68 else ""),
                    label_w=style.METRIC_W, color=color, styled=True))
    print(style.row("budget", style.field(proof["budget"], width=9, color=color)
                    + style.unit("tok", color=color) + "   "
                    + style.note("pool was %s tok" % _fmt(proof["tokens_before"]),
                                 indent=0, color=color),
                    label_w=style.METRIC_W, color=color, styled=True))
    print(style.row("spent", style.field(proof["tokens_after"], width=9, color=color)
                    + style.unit("tok", color=color) + "   "
                    + "−%.1f%%" % pct,
                    label_w=style.METRIC_W, color=color, styled=True))
    print(style.row("protected",
                    style.field(proof["protected_tokens"], width=9, color=color)
                    + style.unit("tok", color=color),
                    label_w=style.METRIC_W, color=color, styled=True))
    print(style.note("booked before the optimiser ran",
                     indent=style.METRIC_CONTENT, color=color))
    print("")
    for kind in compiler.KINDS:
        if kind in by_kind:
            print(style.table(kind, (by_kind[kind][0], "kept", 3),
                              (by_kind[kind][1], "dropped", 7),
                              name_w=style.METRIC_W + 1, indent=style.INDENT,
                              color=color))
    def set_row(label, items):
        """A kept set: its count in the number column, its members wrapped beneath.

        The members are what a reader checks the tail against, and a joined list of
        twelve of them ran off the page — so the count carries the scan and the
        list carries the detail.
        """
        print(style.row(label, len(items) if items else "none",
                        label_w=style.METRIC_W, color=color))
        if items:
            print(style.prose(", ".join(str(i) for i in items),
                              indent=style.METRIC_CONTENT, width=86, color=color))

    nd = proof["never_dropped"]
    print("")
    set_row("tail kept", nd["tail_indices"])
    set_row("core tools", nd["core_tools"])
    set_row("files added", proof["files_surfaced"] or [])
    if proof["dropped"]:
        print("")
        print(style.section("what was cut, and what beat it", indent=style.INDENT,
                            color=color))
        for d in proof["dropped"][:8]:
            print(style.table(d["kind"], (style.clip(d["id"], 26), "", -26),
                              (d["tokens"], "tok", 5),
                              (style.clip(d["reason"], 46), "", 0),
                              name_w=9, color=color))
        if len(proof["dropped"]) > 8:
            print(style.note("%d more in --json" % (len(proof["dropped"]) - 8),
                             indent=style.SUB_INDENT + 2, color=color))
        print("")
        floor = proof.get("floor_value_per_tok")
        print(style.prose("every row lost to the same bar: the lowest value/token ratio "
                          "the turn kept (%s). Which item set it is in --json."
                          % ("%.6f" % floor if floor else "n/a"),
                          indent=0, color=color))
        print("")
        print(style.prose("every drop carries a reversible pointer (body_sha + index); "
                          "through the proxy they resolve against the telemetry row: "
                          "subproto show <request_id>", indent=0, color=color))
    return 0


def _first_text(obj, depth=0):
    """The first readable text inside a recorded message of either dialect, on one line.

    Short by design: the page shows *which* item a pointer names, and re-printing the
    prompt would bury the decision the reader came to read.
    """
    if depth > 4:
        return ""
    if isinstance(obj, str):
        return " ".join(obj.split())
    if isinstance(obj, dict):
        for key in ("text", "content", "description", "arguments", "input"):
            if key in obj:
                found = _first_text(obj[key], depth + 1)
                if found:
                    return found
        return ""
    if isinstance(obj, list):
        for item in obj:
            found = _first_text(item, depth + 1)
            if found:
                return found
    return ""


def _drop_key(kind, pointer):
    """One identity for one cut, whoever recorded it.

    The compiled proof writes `tool:exitplanmode` and the slot's own candidate list
    writes `KillShell`: a prefix and a case difference is how one item reached two rows
    and how a row a reader wanted to match up could not be matched (S46/B2, B6).
    """
    return (kind, str(pointer).rsplit(":", 1)[-1].lower())


def _owned_cuts(decision):
    """The items this decision's own record scored as cuts, by identity.

    Under the compiler each participating slot's record carries the *joint* proof, so
    every slot lists every cut the one knapsack made — including the ones billed to
    another slot. `tool_gate` printed 15 rows summing to 5,345 tok beside its own
    `4,145 tok` label while `compact` printed 3 of those same messages, so a reader who
    added the block got a different number than the block claims and the request looked
    like it lost 18 items when it lost 15 (S46/B6).
    """
    names = {str(n).rsplit(":", 1)[-1].lower() for n in decision.get("dropped") or []}
    for c in decision.get("candidates") or []:
        if c.get("target") and not c.get("keep"):
            names.add(str(c["target"]).rsplit(":", 1)[-1].lower())
    return names


def _decision_drops(decision):
    """[(kind, pointer, index, tokens, why)] for the cuts one decision actually made.

    The compiled arm carries `reversible` (body_sha + index + pointer) on every drop;
    the per-slot arm only names its candidates. Both are read here so the page says
    the same thing about a cut whichever mechanism made it.
    """
    out = []
    seen = {}

    def add(kind, pointer, index, tokens, why):
        """One row per item, not one per arm that happened to name it.

        The per-slot record lists its cuts twice — once as `dropped` names and again as
        the non-kept `candidates` — and printing both made a 24-tool request read as
        `12 kept 24 cut`, with 24 rows for 12 tools (S46/B2).
        """
        key = _drop_key(kind, pointer)
        if key in seen:
            row = seen[key]
            if row[2] is None and index is not None:
                row[2] = index
            if not row[3] and tokens:
                row[3] = tokens
            if not row[4] and why:
                row[4] = why
            return
        row = [kind, pointer, index, tokens, why]
        seen[key] = row
        out.append(row)

    for d in (decision.get("proof") or {}).get("dropped") or []:
        rev = d.get("reversible") or {}
        add(d.get("kind") or "message",
            rev.get("pointer") or d.get("id") or "",
            rev.get("index"), d.get("tokens", 0), d.get("reason") or "")
    if out:
        # Keep the rows this slot paid for. The nameless record — a proof with no
        # candidates beside it — keeps the whole list, because then the proof *is* the
        # slot's own answer.
        owned = _owned_cuts(decision)
        if owned:
            mine = [r for r in out if _drop_key(r[0], r[1])[1] in owned]
            if mine:
                return [tuple(r) for r in mine]
        return [tuple(r) for r in out]
    for name in decision.get("dropped") or []:
        add("tool", "tool:%s" % name, None, 0, "")
    for c in decision.get("candidates") or []:
        target = c.get("target")
        if c.get("keep") or not target:
            continue
        index = c.get("index")
        tail = str(target).rsplit("#", 1)
        if index is None and len(tail) > 1 and tail[1].isdigit():
            index = int(tail[1])
        add("message" if "#" in str(target) else "tool",
            str(target), index, c.get("tokens", 0),
            ", ".join(str(w) for w in c.get("why") or []))
    return [tuple(r) for r in out]


def _head_counts(decision, drops):
    """(kept, cut) for one decision, or None when it edited no pool at all.

    The three arms record their pool differently: a compiled decision carries the
    proof's kept/dropped lists, `effort` carries neither because it changes the route
    rather than the prompt, and the per-slot arm carries only `dropped_count` plus the
    scored candidates. Reading one field and calling it the count is how `12 kept`
    (tool specs) ended up beside 15 cuts (specs, messages, files).

    `drops` is the rows the page is about to print, so a joint proof that covers another
    slot's pool is counted down to this one before its numbers are printed beside it.
    """
    proof = decision.get("proof") or {}
    if isinstance(proof.get("kept"), list):
        kept, dropped = proof["kept"], proof.get("dropped") or []
        kinds = {d[0] for d in drops}
        if kinds and {d.get("kind") for d in dropped} - kinds:
            kept = [k for k in kept if k.get("kind") in kinds]
            return (len(kept), len(drops))
        return (len(kept), len(dropped) or len(drops))
    cands = decision.get("candidates") or []
    if decision.get("kept") is not None or cands:
        kept = decision.get("kept")
        if kept is None:
            kept = sum(1 for c in cands if c.get("keep"))
        cut = decision.get("dropped_count")
        return (kept, len(drops) if cut is None else cut)
    return None


def _excerpt(kind, pointer, index, body):
    """Where a drop's pointer leads in the body the request really sent.

    A message pointer is an index into `normalize_messages` — the same flattened list
    the compiler scored, where one Anthropic message of three content blocks is three
    entries. Resolving it against `body["messages"]` would point at a neighbouring turn
    and print it as the one that was cut, so the page reads the same list and refuses
    the text unless its kind agrees with the kind the pointer names.
    """
    from . import protocol

    if not isinstance(body, dict):
        return ""
    if kind == "tool":
        name = str(pointer).rsplit(":", 1)[-1].lower()
        for spec in body.get("tools") or []:
            fn = spec.get("function") or spec
            if str(fn.get("name") or "").lower() == name:
                return _first_text(fn.get("description") or "")
        return ""
    msgs = protocol.normalize_messages(body)
    if index is None:
        return ""
    index = int(index)
    if not 0 <= index < len(msgs):
        return ""
    _role, text, mkind = msgs[index]
    if str(pointer).rsplit(":", 1)[-1].split("#", 1)[0] != mkind:
        return ""
    return " ".join(str(text).split())


def cmd_show(args):
    from . import dataset, protocol

    color = style.enabled()
    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    rows = telemetry.query("SELECT * FROM requests WHERE id = ?", (args.request_id,))
    telemetry.close()
    row = rows[0] if rows else None
    decisions = json.loads(row["decisions"] or "[]") if row else []
    body_path = body = None
    if row and row.get("body_sha"):
        suffix = "_%s.json.gz" % row["body_sha"]
        for path in dataset.record_bodies(config):
            if path.endswith(suffix):
                body_path = path
                loaded = protocol.load_body(dataset.read_body(path), "gzip")
                body = loaded if isinstance(loaded, dict) else None
                break

    if args.json:
        payload = dict(row or {"id": args.request_id})
        if row is None:
            # A script must not read "no such request" as "a request that made no
            # decisions" — the empty lists below are otherwise identical either way.
            payload["error"] = "no request %d in %s" % (args.request_id, config.db_path)
        payload["decisions"] = decisions
        payload["body_path"] = body_path
        payload["drops"] = [{"slot": d["slot"], "kind": k, "pointer": p,
                             "tokens": t, "why": w,
                             "excerpt": _excerpt(k, p, i, body)}
                            for d in decisions
                            for (k, p, i, t, w) in _decision_drops(d)]
        print(json.dumps(payload, indent=1, default=str))
        return 0 if row else 1

    say = lambda *a, **k: print(*a, **k)
    say(style.header("show", "request %d" % args.request_id,
                     "%s %s" % (row["api"], row["path"]) if row else "",
                     row["model"] or "" if row else "", color=color))
    say("")
    if row is None:
        say(style.reason("no request %d in %s — subproto report lists the ids"
                         % (args.request_id, config.db_path),
                         indent=style.INDENT, color=color))
        return 1
    say(style.detail("client", row["client"] or "unknown"))
    say(style.detail("recorded", time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(row["ts"]))))
    say(style.row("stream", style.state(style.YES if row["stream"] else style.OFF,
                                        color=color),
                  label_w=style.METRIC_W, color=color, styled=True))
    say(style.detail("status", row["status"] if row["status"] is not None else "n/a",
                     row["err"] if row["err"] else None))
    say(style.detail("ttfb", "%s ms" % row["ttfb_ms"]))
    say(style.detail("latency", "%s ms" % row["latency_ms"]))
    # `billed input` follows `report`'s convention: the uncached part plus the cache
    # write, with the read named beside it. Reading the write column for the
    # parenthetical said `0 cached` over a request that read 2,464 tokens from the
    # provider's cache — only Anthropic ever writes a cache, and the mock does not
    # (S46/B5).
    say(style.detail("billed input", row["in_tok"] + (row["cw_tok"] or 0),
                     "%s cached" % _fmt(row["cr_tok"] or 0)))
    say(style.detail("output", row["out_tok"]))
    say(style.detail("spend", "$%.4f" % (row["cost_usd"] or 0.0)))
    say(style.row("body", (style.state(style.PRESENT, color=color) + "  "
                           + style.paint(_shown_path(body_path), style.DIM, color))
                  if body_path
                  else style.state(style.MISSING, color=color) + "  " + style.paint(
                      "never stored — subproto up --store-bodies records them",
                      style.DIM, color),
                  label_w=style.METRIC_W, color=color, styled=True))

    saved = sum(d.get("savings_est_tok") or 0 for d in decisions)
    say("")
    # "2, 0 tok" read as one number with a thousands separator. The count and the
    # estimate are two facts, so they are joined with words, and this is a
    # potential — the `delivered`/`potential` vocabulary below says which is which.
    say(style.section("decisions",
                      "%d, and it could save %s tok" % (len(decisions), _fmt(saved))
                      if decisions else "none recorded for this request",
                      indent=style.INDENT, color=color))
    for d in decisions:
        applied = bool(d.get("applied"))
        drops = _decision_drops(d)
        # Both counts, never one: a bare `12 kept` over a list of 15 cuts reads as
        # arithmetic that does not close, and neither number alone says what the rows
        # below are evidence for.
        head = _head_counts(d, drops)
        figure = "" if head is None else "%d kept  %d cut  " % head
        value = (style.state(style.DELIVERED if applied else style.POTENTIAL,
                             color=color)
                 + "  " + style.paint(
                     "%s%s tok  %s ms  %s" % (
                         figure,
                         _fmt(d.get("savings_est_tok") or 0),
                         "%.1f" % (d.get("decision_ms") or 0.0),
                         d.get("model_version") or d.get("backend") or "heuristic"),
                     None, color))
        say(style.row(d["slot"], value, label_w=style.METRIC_W, color=color,
                      styled=True))
        # A unit with no number in front of it reads as a missing value, so the column
        # is only drawn when the arm measured one: the compiled path prices each cut,
        # the per-slot path prices the decision as a whole and says so once.
        priced = any(t for (_k, _p, _i, t, _w) in drops)
        if drops and not priced:
            say(style.note("priced as one decision, not per cut: the %s tok above is "
                           "the sum" % _fmt(d.get("savings_est_tok") or 0),
                           indent=style.SUB_INDENT + 2, color=color))
        for (kind, pointer, index, tokens, why) in drops[:8]:
            say(style.table(kind, (style.clip(pointer.rsplit(":", 1)[-1], 26), "", -26),
                            ((tokens, "tok", 5) if priced else ("", "", 5)),
                            (style.clip(why, 46), "", 0),
                            name_w=style.METRIC_W - 9, indent=style.SUB_INDENT,
                            color=color))
            excerpt = _excerpt(kind, pointer, index, body)
            if excerpt:
                # The grid's content column, and a quote that ends inside it: a pasted
                # page must not wrap, and this is an identifier of the item, not prose
                # for reading. --json carries the text whole.
                say(style.note(style.clip(excerpt, 66), indent=style.METRIC_CONTENT,
                               color=color))
        if len(drops) > 8:
            say(style.note("%d more in --json" % (len(drops) - 8),
                           indent=style.SUB_INDENT + 2, color=color))
        if d["slot"] == "effort":
            say(style.note("tier %s, advisory-only: %s" % (
                d.get("tier") or "n/a",
                ", ".join(d.get("reasons") or []) or "no reason given"),
                indent=style.METRIC_CONTENT, color=color))
    if body is None and row.get("body_sha"):
        say("")
        say(style.prose("the pointers above are real and the counts are measured, but "
                        "this request's body was never stored, so the page cannot show "
                        "the text they name — that is what --store-bodies is for.",
                        indent=style.INDENT, color=color))
    return 0


def cmd_audit(args):
    from . import dataset

    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    from .engine import Engine
    engine = Engine(config, graph_path=_graph_arg(args))
    res = dataset.audit(config, telemetry, engine=engine, limit=args.limit,
                        verbose=args.verbose)
    print(json.dumps(res, indent=1, default=str))
    telemetry.close()


def cmd_export(args):
    from . import dataset

    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    res = dataset.export(config, telemetry, path=args.out,
                         slots=tuple(args.slots.split(",")) if args.slots else None,
                         include_candidates=not args.slim)
    print(json.dumps(res, indent=1))
    telemetry.close()


def cmd_split(args):
    from . import dataset

    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    res = dataset.build_training_split(config, telemetry, val_frac=args.val_frac,
                                       seed=args.seed)
    print(json.dumps(res, indent=1))
    telemetry.close()
    return 0


def cmd_retrain(args):
    """Decide whether a fine-tune is worth starting, and record the decision.

    Dry-run is the default in the only direction that matters: the printed page is
    the product, and the trainer starts only if `--run` says so *and* the cadence
    cleared. A refusal is still a record, because "we did not train, and here is
    why" is the thing a later comparison needs.
    """
    from . import retrain

    config = Config.load(args.config, data_dir=args.home)
    split_kw = {}
    if args.val_frac is not None:
        split_kw["val_frac"] = args.val_frac
    if args.seed is not None:
        split_kw["seed"] = args.seed
    state = retrain.readiness(config, min_per_slot=args.min_per_slot,
                              min_answers=args.min_answers, min_days=args.min_days,
                              split_kw=split_kw)
    line = retrain.command(config, epochs=args.epochs, rank=args.rank)
    ran, status, wrote = None, None, None
    if args.run:
        if state["ready"]:
            # write what the page counted *before* naming it in a command, so the
            # run trains on the split whose sha is about to go into the record
            wrote = retrain.write_split(config, split_kw)
            line = retrain.command(config, train_path=wrote.get("train_path"),
                                   val_path=wrote.get("val_path"),
                                   epochs=args.epochs, rank=args.rank)
            version = retrain.version_for(retrain.load_history(config, []) or [])
            ran = retrain.run_command(line, chatter_to_stderr=args.json)
            status = "ran"
            retrain.append_history(config, retrain.record(
                state, status, command_line=line, version=version, exit_code=ran))
        else:
            status = "refused"
            retrain.append_history(config, retrain.record(state, status,
                                                          command_line=line))
    elif args.record:
        status = "planned"
        retrain.append_history(config, retrain.record(state, status, command_line=line))
    if args.json:
        payload = dict(state)
        payload["command"] = line
        payload["recorded"] = status
        payload["split_written"] = None if wrote is None else {
            "n_train": wrote.get("n_train"), "n_val": wrote.get("n_val"),
            "split_sha": retrain.fingerprint_rows(
                [wrote.get("train_path"), wrote.get("val_path")])}
        payload["exit"] = ran
        print(json.dumps(payload, indent=1, default=str))
    else:
        color = style.enabled()
        print(retrain.render_text(state, line, ran=ran, color=color))
        if wrote is not None:
            on_disk = retrain.fingerprint_rows([wrote.get("train_path"),
                                                wrote.get("val_path")])
            same = on_disk == state["split_sha"]
            print(style.row("wrote", "%s train / %s val on disk" % (
                wrote.get("n_train"), wrote.get("n_val")), color=color))
            # The fingerprint of what is actually on disk, printed beside the page's
            # own — so "it trained on what it measured" is checkable, not asserted.
            print(style.note("%s  (%s…)" % (
                "same rows as the page" if same else
                "DIFFERENT rows than the page counted", on_disk[:12]), color=color))
        if status:
            # `planned`/`ran`/`refused` name the row that went into the log, so they
            # ride with the path they were written to — and take no hue: the page's
            # verdict colour already lives in `decision`, and two would disagree.
            print(style.row("record", "%s  %s" % (status, retrain.history_path(config)),
                            color=color, styled=True))
    if args.run and not state["ready"]:
        return 1
    return 0 if ran in (None, 0) else 1


def cmd_label(args):
    from . import dataset
    from .engine import ALL_SLOTS

    config = Config.load(args.config, data_dir=args.home)
    color = style.enabled()
    telemetry = _open_telemetry(config)
    try:
        known = telemetry.query("SELECT 1 FROM requests WHERE id = ?",
                                (args.request_id,))
    finally:
        telemetry.close()
    if not known:
        # `show` refuses the same id; a label that attaches to nothing would sit in the
        # file forever and never reach a split.
        print(style.header("label", "no such request", color=color))
        print(style.reason("request %s is not in %s" % (args.request_id, config.db_path),
                           indent=0, color=color))
        return 1
    labels = dataset.load_labels(config)
    key = str(args.request_id)
    entry = labels.get(key, {})
    if args.slot == "all":
        for s in ALL_SLOTS:
            entry[s] = args.value
    else:
        entry[args.slot] = args.value
    if args.reason:
        entry["reason"] = args.reason
    labels[key] = entry
    path = dataset.save_labels(config, labels)
    print(style.row("labelled", "%s → %s (%s)" % (key, args.slot, args.value),
                    label_w=9, color=color, styled=True))
    print(style.note(path, color=color))


def cmd_learn(args):
    """Label the drops that the recorded traffic itself contradicted."""
    from . import implicit

    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    out = implicit.run(config, telemetry, limit=args.limit, write=not args.dry_run)
    telemetry.close()
    if args.json:
        print(json.dumps({"summary": out["summary"], "labels": out["labels"]},
                         indent=1, default=str))
        return 0
    s = out["summary"]
    color = style.enabled()
    print(style.header("learn", "%d requests with decisions, %d bodies readable "
                       "(%d never stored)" % (s["requests_scanned"],
                                              s["bodies_available"],
                                              s["skipped_no_body"]), color=color))
    if not s["bodies_available"]:
        print("")
        print(style.prose("Nothing to judge — a re-read is only visible in the recorded "
                          "body, so start the proxy with --store-bodies and re-run some "
                          "agent traffic.", indent=style.INDENT, color=color))
        return 0
    from .report import eviction_regret, regret_section
    for line in regret_section(eviction_regret(out), color=color):
        print(line)
    print("")
    already = "%s already holds %d request%s" % (
        implicit.IMPLICIT_FILE, s.get("already_stored", 0),
        "" if s.get("already_stored", 0) == 1 else "s")
    # The block closes on the same grid the measurement above it was read on, so the
    # page has one number column rather than one per section.
    if args.dry_run:
        print(style.row("written", "nothing  (--dry-run)", label_w=style.METRIC_W,
                        color=color))
        print(style.note(already, indent=style.METRIC_CONTENT, color=color))
    elif out.get("unchanged"):
        print(style.row("written", "nothing new", label_w=style.METRIC_W,
                        color=color))
        print(style.note("%s and was left as it is" % already,
                         indent=style.METRIC_CONTENT, color=color))
    elif out.get("path"):
        print(style.row("written", "%d request%s" % (
            s["stored"], "" if s["stored"] == 1 else "s"),
            label_w=style.METRIC_W, color=color))
        print(style.action(out["path"], indent=style.METRIC_CONTENT, color=color))
        print(style.note("human `subproto label` verdicts live in a separate store and "
                         "were not touched", indent=style.METRIC_CONTENT, color=color))
    else:
        print(style.row("written", "nothing new", label_w=12, color=color))
        print(style.note("%s is unchanged" % implicit.IMPLICIT_FILE,
                         indent=style.SUB_INDENT, color=color))
    return 0


def cmd_inject(args):
    config = Config.load(args.config, data_dir=args.home)
    for line in INJECT.get(args.tool, INJECT["generic"]):
        print(line.format(port=config.port))


def _port_or_next(hint):
    """`hint` unless something already holds it — then whatever the kernel offers.

    The demo needs two ports: the proxy's, and the mock upstream's behind it. The
    proxy keeps the port the user can see (`--port`, and `subproto inject` prints
    it); the mock is internal, so a collision there is not worth failing the run
    for — a second service already holding `port + 1` is an ordinary machine, not
    a broken one.
    """
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", hint))
    except OSError:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()
    return hint


def cmd_demo(args):
    """End-to-end demo against a local mock upstream: no API key, no spend."""
    import glob

    from . import dataset, demo, report
    from .engine import Engine
    from .proxy import ProxyServer

    # Honour --home, then SUBPROTO_HOME, so `demo` then `report` share a dir as every
    # other command does. With neither, the demo gets a fresh temp dir per run: a
    # fixed sandbox made two runs share one SQLite file, and the second one reported
    # the first one's traffic as its own result.
    home = args.home or os.environ.get("SUBPROTO_HOME")
    sandbox = home is None
    if sandbox:
        home = tempfile.mkdtemp(prefix="subproto-demo-")
    config = Config.load(args.config, data_dir=home, port=args.port, store_bodies=True,
                         model=args.model)
    config.ensure_dirs()
    previous = _count_requests(config.db_path)
    for p in [config.db_path] + glob.glob(os.path.join(config.bodies_dir, "*")):
        if os.path.exists(p):
            os.remove(p)
    telemetry = Telemetry(config.db_path)
    engine = Engine(config, graph_path=_graph_arg(args))
    srv = ProxyServer(("127.0.0.1", config.port), config, telemetry,
                      engine if args.slots else None)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    n = demo.replay(config, mock_port=_port_or_next(config.port + 1))
    demo.settled(telemetry, n)
    print(report.render_text(report.summarize(telemetry), color=style.enabled()))
    print("")
    print("replayed %d synthetic agent requests through the proxy" % n)
    print("recorded bodies: %d" % len(dataset.record_bodies(config)))
    if previous:
        print(style.note("cleared %d earlier requests from this home — demo replays into "
                         "an empty one" % previous))
    if sandbox:
        print("")
        print(style.note("this run wrote to a fresh directory, so the next command has "
                         "to name it:"))
        print(style.action("subproto report --home %s" % home))
    if args.audit:
        print(json.dumps(dataset.audit(config, telemetry, engine=engine, limit=50), indent=1))
    srv.shutdown()
    telemetry.close()


def cmd_doctor(args):
    """Diagnose this machine: interpreter, data dir, port, keys, backends."""
    from . import doctor
    return doctor.run(args)


def build_parser():
    from . import retrain as retrain_mod

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="state dir (default ~/.subproto)")
    common.add_argument("--config", help="path to config json")
    common.add_argument("--color", dest="color", action="store_true", default=None,
                        help="force escapes on, even in a pipe (for a capture)")
    common.add_argument("--no-color", dest="color", action="store_false",
                        help="force escapes off (for a paste)")

    p = argparse.ArgumentParser(
        prog="subproto",
        description="A System One layer for coding agents: decide before you pay.")
    p.add_argument("--version", action="store_true",
                   help="print the version, the interpreter and the platform, then exit")
    sub = p.add_subparsers(dest="cmd")

    up = sub.add_parser("up", parents=[common], help="run the local proxy")
    up.add_argument("--port", type=int)
    up.add_argument("--store-bodies", action="store_true",
                    help="also record request bodies so slots can be audited offline")
    up.add_argument("--slots", action="store_true", help="compute routing decisions per request")
    up.add_argument("--graph", help="path to a .subproto-graph.json for the context slot")
    up.add_argument("--laya-url", help="http://host:port of a laya scoring server")
    up.add_argument("--model", help="System One backend: laya, openjev, djev, semif, "
                                    "mlx_lora, heuristic, or an http://host:port URL")
    up.add_argument("--router", action="store_true",
                    help="pick the best measured backend per un-pinned slot "
                         "(SUBPROTO_ROUTER=on) instead of one named model")
    up.add_argument("--for", dest="for_", nargs="*", choices=sorted(INJECT),
                    help="print the env vars to point a tool at the proxy")

    rp = sub.add_parser("report", parents=[common], help="token, latency and waste report")
    rp.add_argument("--no-learn", action="store_true", dest="no_learn",
                    help="skip the eviction-regret harvest (no bodies read)")
    rp.add_argument("--since")
    rp.add_argument("--until")
    rp.add_argument("--json", action="store_true")

    lv = sub.add_parser("live", parents=[common], help="live terminal savings meter")
    lv.add_argument("--interval", type=float, default=1.0, help="refresh seconds")
    lv.add_argument("--once", action="store_true", help="print one snapshot and exit (CI/screenshots)")

    gr = sub.add_parser("graph", parents=[common], help="index a repo (imports + symbols)")
    gr.add_argument("path", nargs="?", default=".")
    gr.add_argument("--out")
    gr.add_argument("--install", action="store_true")

    wh = sub.add_parser("where", parents=[common], help="which files does a task touch?")
    wh.add_argument("query", nargs="*")
    wh.add_argument("--graph")
    wh.add_argument("--file")
    wh.add_argument("--path", default=".")
    wh.add_argument("--top", type=int, default=10)
    wh.add_argument("--json", action="store_true")

    cp = sub.add_parser("compile", parents=[common],
                        help="compile one turn: one budget over messages, tools and files")
    cp.add_argument("task", nargs="*")
    cp.add_argument("--graph", help="graph json or repo directory to index")
    cp.add_argument("--path", default=".", help="repo to index when --graph is a directory")
    cp.add_argument("--budget", type=int, help="token budget (default: 45%% of the estimate)")
    cp.add_argument("--turns", type=int, default=6, help="simulated prior turns (demo body)")
    cp.add_argument("--seed", type=int, default=3)
    cp.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", parents=[common],
                        help="one recorded request, and what each slot cut from it")
    sh.add_argument("request_id", type=int)
    sh.add_argument("--json", action="store_true")

    au = sub.add_parser("audit", parents=[common], help="re-run the slots over recorded traffic")
    au.add_argument("--limit", type=int, default=200)
    au.add_argument("--verbose", action="store_true")
    au.add_argument("--graph")

    ex = sub.add_parser("export", parents=[common], help="write the fine-tuning dataset as JSONL")
    ex.add_argument("--out")
    ex.add_argument("--slots")
    ex.add_argument("--slim", action="store_true", help="omit per-candidate scores")

    sp = sub.add_parser("split", parents=[common],
                        help="build a deterministic train/val Laya supervision split")
    sp.add_argument("--val-frac", type=float, default=0.2, dest="val_frac")
    sp.add_argument("--seed", type=int, default=1337)

    ln = sub.add_parser("learn", parents=[common],
                        help="label the drops that recorded traffic contradicted (no key, no upload)")
    ln.add_argument("--limit", type=int, default=200)
    ln.add_argument("--json", action="store_true")
    ln.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print the harvest without writing implicit_labels.json")

    lb = sub.add_parser("label", parents=[common], help="supervise a decision (good/bad/uncertain)")
    lb.add_argument("request_id", type=int)
    lb.add_argument("slot", choices=list(ALL_SLOTS) + ["all"])
    lb.add_argument("value", choices=["good", "bad", "uncertain"])
    lb.add_argument("--reason")

    ij = sub.add_parser("inject", parents=[common], help="print env vars for a given tool")
    ij.add_argument("tool", choices=sorted(INJECT))

    dm = sub.add_parser("demo", parents=[common], help="replay synthetic agent traffic against a mock upstream")
    dm.add_argument("--port", type=int, default=8799)
    dm.add_argument("--slots", action="store_true")
    dm.add_argument("--audit", action="store_true")
    dm.add_argument("--graph")
    dm.add_argument("--model", help="System One backend to run the slots with")

    md = sub.add_parser("models", parents=[common],
                        help="list System One backends and which one is active")
    md.add_argument("--model", help="show the selection as if this backend were chosen")
    md.add_argument("--router", action="store_true",
                    help="show the evidence-driven routing plan as if SUBPROTO_ROUTER=on")
    md.add_argument("--add", metavar="LABEL",
                    help="register a model version in models.json (needs --url)")
    md.add_argument("--url", help="endpoint for --add: any /health + /score server")
    md.add_argument("--tier",
                    help="foundation (a model you point at) or personal (one you trained)")
    md.add_argument("--note", help="free text for --add: which checkpoint, what changed")
    md.add_argument("--use", metavar="LABEL",
                    help="make a registered version active (the old one becomes previous)")
    md.add_argument("--rollback", action="store_true",
                    help="put the previous version back, or drop to the heuristics")
    md.add_argument("--json", action="store_true")

    rt = sub.add_parser("retrain", parents=[common],
                        help="decide whether a fine-tune is worth starting (prints, "
                             "and runs nothing, unless --run)")
    rt.add_argument("--run", action="store_true",
                    help="start the trainer, but only if the cadence cleared")
    rt.add_argument("--record", action="store_true",
                    help="append this decision to training_history.json without running")
    rt.add_argument("--min-per-slot", type=int, default=retrain_mod.MIN_PER_SLOT,
                    dest="min_per_slot", help="rows per slot before a run is worth it")
    rt.add_argument("--min-answers", type=int,
                    default=retrain_mod.MIN_ANSWERS_PER_SLOT, dest="min_answers",
                    help="rows per slot in *both* answers — the tighter floor")
    rt.add_argument("--min-days", type=int, default=retrain_mod.MIN_DAYS,
                    dest="min_days", help="days to wait after a completed run")
    rt.add_argument("--val-frac", type=float, default=None, dest="val_frac",
                    help="forwarded to the split builder (default: its own)")
    rt.add_argument("--seed", type=int, default=None)
    rt.add_argument("--epochs", type=int, default=3)
    rt.add_argument("--rank", type=int, default=8)
    rt.add_argument("--json", action="store_true")

    dc = sub.add_parser("doctor", parents=[common],
                        help="diagnose this machine: python, data dir, port, backends")
    dc.add_argument("--port", type=int,
                    help="the port to test (0 asks the kernel for any free one)")
    dc.add_argument("--json", action="store_true",
                    help="print the checks as one document, for a bug report")
    return p


def _utf8_streams():
    """Put the program's streams in UTF-8, whatever the machine's locale claims.

    A page here can echo text out of a repository or out of a manifest — a path, a
    version name, a note — and a console that declares cp1252 (Windows' default, or
    `LANG=C` in a container) raises UnicodeEncodeError on a character that is not
    its own. The command would die answering a question it was able to answer.
    `errors="replace"` is for the *input* streams: a byte that is not UTF-8 becomes
    one replacement character, not a traceback.
    """
    for name in ("stdout", "stderr", "stdin"):
        reconfigure = getattr(getattr(sys, name, None), "reconfigure", None)
        if reconfigure is not None:      # a capture object is not a text stream
            reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _utf8_streams()
    args = build_parser().parse_args(argv)
    if getattr(args, "version", False) and not getattr(args, "cmd", None):
        # One line, safe to paste into a bug report: which build, which interpreter,
        # which platform. `subproto doctor` is the page that diagnoses the machine.
        print("subproto %s (python %s, %s %s)" % (
            __version__, ".".join(str(n) for n in sys.version_info[:3]),
            platform.system().lower(), platform.machine().lower()))
        return 0
    if not getattr(args, "cmd", None):
        build_parser().print_help()
        return 0
    # One decision, made here, read by every surface: `--color` is how a page gets
    # screenshotted from a pipe, `--no-color` how it gets pasted out of a terminal.
    style.override(args.color)
    fn = {"up": cmd_up, "report": cmd_report, "live": cmd_live, "graph": cmd_graph,
          "where": cmd_where, "compile": cmd_compile, "show": cmd_show,
          "audit": cmd_audit, "export": cmd_export, "split": cmd_split, "label": cmd_label,
          "learn": cmd_learn, "retrain": cmd_retrain,
          "inject": cmd_inject, "demo": cmd_demo, "models": cmd_models,
          "doctor": cmd_doctor}[args.cmd]
    try:
        return fn(args) or 0
    except HomeError as exc:
        # The one place a bad `--home`/`SUBPROTO_HOME` is answered, so no command has to
        # remember to check and none of them ends in a traceback over a typo.
        print(style.reason(str(exc), indent=0, color=style.enabled()))
        return 1
