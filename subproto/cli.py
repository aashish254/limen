import argparse
import json
import os
import sys
import threading
import time

from .config import Config
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


def _open_telemetry(config):
    config.ensure_dirs()
    return Telemetry(config.db_path)


def _fmt(n):
    return "{:,}".format(int(n))


def cmd_up(args):
    from .proxy import serve
    from .engine import Engine

    config = Config.load(args.config, port=args.port, data_dir=args.home,
                         store_bodies=True if args.store_bodies else None,
                         laya_url=args.laya_url, model=args.model,
                         router=True if args.router else None)
    config.ensure_dirs()
    telemetry = _open_telemetry(config)
    engine = None
    if args.slots:
        engine = Engine(config, graph_path=args.graph)
    srv = serve(config, telemetry, engine)
    applied = os.environ.get("SUBPROTO_APPLY")
    print("subproto listening on http://127.0.0.1:%d" % config.port)
    print("  data dir     %s" % config.data_dir)
    print("  bodies       %s" % ("recording (gzip)" if config.store_bodies else "metadata only"))
    print("  slots        %s" % (args.slots or "off (pure passthrough)"))
    print("  enforcing    %s" % (applied or "nothing — observation mode"))
    if applied:
        from .engine import ADVISORY_SLOTS
        advisory = [s.strip() for s in applied.split(",") if s.strip() in ADVISORY_SLOTS]
        if advisory:
            print("               %s is advisory-only: it records a tier, it never "
                  "rewrites the request" % ",".join(advisory))
    print("  graph        %s" % (engine.graph_path if engine else "n/a"))
    if engine and engine.backend:
        print("  model        %s" % json.dumps(engine.model_status()))
    print("")
    for tool in (args.for_ or ["claude", "codex"]):
        for line in [l.format(port=config.port) for l in INJECT.get(tool, INJECT["generic"])]:
            print("  % 12s %s" % (tool + ":", line))
    print("")
    print("Ctrl-C to stop. Then run: subproto report")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        telemetry.close()


def cmd_models(args):
    """List every System One backend the registry can bind, and which is active."""
    from . import systemone

    config = Config.load(args.config, data_dir=args.home, model=args.model,
                         router=True if args.router else None)
    st = systemone.status(config)
    router = systemone.Router(config)
    plan = router.plan() if router.available else None
    if args.json:
        st["router"] = {"enabled": router.available, "budget_ms": router.budget_ms,
                        "evidence": sorted(router.evidence), "plan": router.plan()}
        print(json.dumps(st, indent=1))
        return 0
    print("active System One backend: %s" % st["active"])
    print("")
    for row in st["adapters"]:
        mark = "*" if row["active"] else " "
        extra = ("  -> %s" % ",".join(row["slots"])) if row.get("slots") else ""
        print("  %s %-10s %-58s %s%s" % (mark, row["name"], row["about"],
                                         row["where"], extra))
    print("")
    print("  per slot:  %s" % "  ".join("%s=%s" % kv for kv in sorted(st["slots"].items())))
    print("  context/effort are answered by the code graph and request shape, "
          "not by a model")
    print("")
    if plan:
        print("  router: ON (budget %s) · ranks configured backends by measured "
              "slot precision" % (("%gms" % router.budget_ms)
                                  if router.budget_ms else "none"))
        for slot in sorted(plan):
            info = plan[slot]
            print("    %-10s %-7s %s" % (slot, info["mode"], info["reason"]))
        print("")
    else:
        print("  router:  off — slots use the named model above (SUBPROTO_MODEL[_<slot>]).")
        print("           turn on with SUBPROTO_ROUTER=on (or --router) to pick the best")
        print("           measured backend per slot; ties and no-evidence stay on heuristics.")
        print("")
    print("select one: subproto up --model laya")
    print('           (SUBPROTO_MODEL=http://host:port works for any /health + /score server)')
    print(' per slot:  SUBPROTO_MODEL_COMPACT=djev subproto up --slots')
    return 0


def cmd_report(args):
    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    from . import dataset, implicit, report
    labels = dataset.load_labels(config)
    # the regret section re-derives the harvest from local bodies rather than reading
    # the last stored one, so what prints here is what the traffic says *now*
    harvest = None if args.no_learn else implicit.harvest(config, telemetry)
    summary = report.summarize(telemetry, since=args.since, until=args.until,
                               labels=labels, implicit=harvest)
    if args.json:
        print(json.dumps(report.render_json(summary), indent=1, default=str))
    else:
        print(report.render_text(summary, since=args.since, until=args.until))
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
        print("no such directory: %s" % root)
        return 1
    started = time.time()
    g = graph_mod.build(root)
    target = args.out or os.path.join(root, ".subproto-graph.json")
    graph_mod.save(g, target)
    print("%d files, %d import edges, %.1fs -> %s" % (
        g["file_count"], g["edge_count"], time.time() - started, target))
    if args.install:
        config = Config.load(args.config, data_dir=args.home)
        config.ensure_dirs()
        key = os.path.basename(root)
        link = os.path.join(config.graph_dir, key + ".json")
        with open(target) as f:
            data = f.read()
        with open(link, "w") as f:
            f.write(data)
        print("installed as %s (use --graph %s)" % (link, link))


def cmd_where(args):
    from . import graph as graph_mod

    path = args.graph or os.path.join(os.path.abspath(args.path or "."), ".subproto-graph.json")
    if os.path.isdir(path):
        # Everyone reads `--graph` as "the repo", and `subproto compile` takes a
        # directory, so `where` must not die with IsADirectoryError on one.
        root = os.path.abspath(path)
        cached = graph_mod.index_path(root)
        if os.path.exists(cached):
            g = graph_mod.load(cached)
        else:
            print("no index at %s — built one in memory (cache it: subproto graph %s)"
                  % (cached, root), file=sys.stderr)
            g = graph_mod.build(root)
    else:
        if not os.path.exists(path):
            print("no graph at %s — run: subproto graph <repo>" % path)
            return 1
        g = graph_mod.load(path)
    query = " ".join(args.query) if isinstance(args.query, list) else (args.query or "")
    if args.file:
        with open(args.file) as f:
            query = f.read() + "\n" + query
    elif not query and not sys.stdin.isatty():
        query = sys.stdin.read()
    hits = graph_mod.search(g, query or "", top_k=args.top)
    if args.json:
        print(json.dumps([{"file": r, "score": s, "why": w} for r, s, w in hits], indent=1))
    else:
        for rel, score, why in hits:
            node = g["nodes"].get(rel, {})
            print("%7.1f  %-52s %s" % (score, rel, ",".join(why)[:44]))
            print("         %d lines · %s" % (node.get("lines") or 0,
                                              (node.get("doc") or "")[:70]))
    return 0


def cmd_compile(args):
    """v5 witness: compile one turn under a single budget, and show the proof."""
    from . import compiler, demo, graph as graph_mod, protocol
    from .proxy import analyze_request

    query = " ".join(args.task) if isinstance(args.task, list) else (args.task or "")
    if not query.strip():
        print('nothing to compile — try: subproto compile "fix retry.py" --graph <repo>')
        return 1
    def say(line):
        # stdout is reserved for the witness: with --json it must stay parseable.
        print(line, file=sys.stderr if args.json else sys.stdout)

    root = os.path.abspath(args.path or ".")
    path = args.graph or graph_mod.index_path(root)
    if os.path.isdir(path):
        say("indexing %s ..." % path)
        started = time.time()
        g = graph_mod.build(path)
        say("  %d files, %d import edges, %.1fs" % (
            g["file_count"], g["edge_count"], time.time() - started))
    elif os.path.exists(path):
        g = graph_mod.load(path)
    elif args.graph:
        print("no graph at %s — run: subproto graph <repo>" % path)
        return 1
    else:
        say("no index at %s — building one from %s (or pass --graph)" % (path, root))
        g = graph_mod.build(root)

    body = demo.synthetic_request(args.seed, jitter=False, turns=args.turns)
    # The task becomes the live turn: what the compiler reads is what you typed.
    body["messages"] = list(body["messages"]) + [{"role": "user", "content": query}]
    analysis = analyze_request(body)
    new_body, decisions, proof = compiler.compile_turn(
        "anthropic", body, analysis, g, query, budget=args.budget,
        body_sha=protocol.sha256_12(protocol.dump_body(body)),
        enforce=("tool_gate", "compact", "context"))
    if proof is None:
        print("nothing to compile: the request has no droppable candidates")
        return 1
    if proof["over_budget"]:
        print("over budget: %s — the tail is sacred (I3), so nothing was cut"
              % proof["reason"])
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
    print("compiled turn — one budget over messages, tools and files")
    print("  task        %s" % (query[:68] + ("..." if len(query) > 68 else "")))
    print("  budget      %s tok  (pool was %s tok)" % (
        _fmt(proof["budget"]), _fmt(proof["tokens_before"])))
    print("  spent       %s tok  (-%.1f%%)" % (_fmt(proof["tokens_after"]), pct))
    print("  protected   %s tok booked before the optimiser ran"
          % _fmt(proof["protected_tokens"]))
    for kind in compiler.KINDS:
        if kind in by_kind:
            print("  %-11s %d kept / %d dropped" % (kind, by_kind[kind][0], by_kind[kind][1]))
    nd = proof["never_dropped"]
    print("  tail kept   %s" % (", ".join(nd["tail_indices"]) or "-"))
    print("  core tools  %s" % (", ".join(nd["core_tools"]) or "-"))
    print("  files added %s" % (", ".join(proof["files_surfaced"]) or "none"))
    if proof["dropped"]:
        print("\n  what was cut, and what beat it:")
        for d in proof["dropped"][:8]:
            print("    %-8s %-26s %5d tok  %s" % (
                d["kind"], d["id"][:26], d["tokens"], d["reason"][:60]))
        if len(proof["dropped"]) > 8:
            print("    ... %d more in --json" % (len(proof["dropped"]) - 8))
        print("\n  every drop carries a reversible pointer (body_sha + index); through the")
        print("  proxy they resolve against the telemetry row: subproto show <request_id>")
    return 0


def cmd_audit(args):
    from . import dataset

    config = Config.load(args.config, data_dir=args.home)
    telemetry = _open_telemetry(config)
    from .engine import Engine
    engine = Engine(config, graph_path=args.graph)
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


def cmd_label(args):
    from . import dataset
    from .engine import ALL_SLOTS

    config = Config.load(args.config, data_dir=args.home)
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
    print("labelled request %s -> %s (%s) in %s" % (key, args.slot, args.value, path))


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
    print("subproto learn  ·  %d requests with decisions, %d bodies readable"
          " (%d never stored)" % (s["requests_scanned"], s["bodies_available"],
                                  s["skipped_no_body"]))
    if not s["bodies_available"]:
        print("")
        print("Nothing to judge. A re-read is only visible in the recorded body, so")
        print("start the proxy with --store-bodies and re-run some agent traffic.")
        return 0
    from .report import eviction_regret, regret_section
    for line in regret_section(eviction_regret(out)):
        print(line)
    print("")
    if args.dry_run:
        print("  --dry-run: nothing written. %s already holds %d request%s." % (
            implicit.IMPLICIT_FILE, s.get("already_stored", 0),
            "" if s.get("already_stored", 0) == 1 else "s"))
    elif out.get("unchanged"):
        print("  nothing new to store; %s already holds %d request%s and was left as it is"
              % (implicit.IMPLICIT_FILE, s["stored"], "" if s["stored"] == 1 else "s"))
    elif out.get("path"):
        print("  wrote %d request%s to %s (human `subproto label` verdicts live in a"
              " separate store and were not touched)" % (
                  s["stored"], "" if s["stored"] == 1 else "s", out["path"]))
    else:
        print("  nothing new to store; %s is unchanged" % implicit.IMPLICIT_FILE)
    return 0


def cmd_inject(args):
    config = Config.load(args.config, data_dir=args.home)
    for line in INJECT.get(args.tool, INJECT["generic"]):
        print(line.format(port=config.port))


def cmd_demo(args):
    """End-to-end demo against a local mock upstream: no API key, no spend."""
    import glob

    from . import dataset, demo, report
    from .engine import Engine
    from .proxy import ProxyServer

    # Honour --home, then SUBPROTO_HOME (so `demo` then `report` share a dir, as every
    # other command does); otherwise isolate the demo in its own wiped sandbox.
    home = args.home or os.environ.get("SUBPROTO_HOME") or "/tmp/subproto-demo"
    config = Config.load(args.config, data_dir=home, port=args.port, store_bodies=True,
                         model=args.model)
    config.ensure_dirs()
    for p in [config.db_path] + glob.glob(os.path.join(config.bodies_dir, "*")):
        if os.path.exists(p):
            os.remove(p)
    telemetry = Telemetry(config.db_path)
    engine = Engine(config, graph_path=args.graph or None)
    srv = ProxyServer(("127.0.0.1", config.port), config, telemetry,
                      engine if args.slots else None)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    n = demo.replay(config, mock_port=args.port + 1)
    time.sleep(0.2)
    print(report.render_text(report.summarize(telemetry)))
    print("")
    print("replayed %d synthetic agent requests through the proxy" % n)
    print("recorded bodies: %d" % len(dataset.record_bodies(config)))
    if args.audit:
        print(json.dumps(dataset.audit(config, telemetry, engine=engine, limit=50), indent=1))
    srv.shutdown()
    telemetry.close()


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="state dir (default ~/.subproto)")
    common.add_argument("--config", help="path to config json")

    p = argparse.ArgumentParser(
        prog="subproto",
        description="A System One layer for coding agents: decide before you pay.")
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
    md.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "cmd", None):
        build_parser().print_help()
        return 0
    fn = {"up": cmd_up, "report": cmd_report, "live": cmd_live, "graph": cmd_graph,
          "where": cmd_where, "compile": cmd_compile,
          "audit": cmd_audit, "export": cmd_export, "split": cmd_split, "label": cmd_label,
          "learn": cmd_learn,
          "inject": cmd_inject, "demo": cmd_demo, "models": cmd_models}[args.cmd]
    return fn(args) or 0
