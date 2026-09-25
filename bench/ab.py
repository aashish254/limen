#!/usr/bin/env python3
"""Reproducible projection of per-slot token savings over a recorded corpus.

Run against synthetic traffic (`--mock`, no API key) or your own recorded
traffic (`--home ~/.subproto`). It never touches the wire — this is the honest,
offline side of the benchmark: given the exact prompts your agent sent, what each
slot would remove. The pass-rate half of the hero metric lives in the live
harness (roadmap); until that is stable we label everything here a projection.
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subproto import dataset, protocol
from subproto.config import Config
from subproto.engine import Engine
from subproto.proxy import analyze_request


def build_corpus(home, mock_n):
    """Return (config, requests_sent). Bodies land in home/bodies."""
    if mock_n:
        import socket
        import threading
        import time

        from subproto import demo
        from subproto.proxy import ProxyServer
        from subproto.telemetry import Telemetry

        def free_port():
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
            s.close()
            return p

        port = free_port()
        cfg = Config.load(data_dir=home, port=port, store_bodies=True)
        cfg.ensure_dirs()
        tel = Telemetry(cfg.db_path)
        srv = ProxyServer(("127.0.0.1", port), cfg, tel, Engine(cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        sent = demo.replay(cfg, mock_port=free_port(), n=mock_n)
        time.sleep(0.1)
        srv.shutdown()
        srv.server_close()
        tel.close()
        return cfg, sent
    cfg = Config.load(data_dir=home, store_bodies=True)
    return cfg, 0


def measure(cfg):
    eng = Engine(cfg)
    paths = dataset.record_bodies(cfg)
    totals = {"requests": 0, "est_in_tok": 0}
    slots = {}
    for path in paths:
        raw = dataset.read_body(path)
        body = protocol.load_body(raw, "gzip" if path.endswith(".gz") else "")
        if not isinstance(body, dict):
            continue
        a = analyze_request(body)
        totals["requests"] += 1
        totals["est_in_tok"] += a["est_in_tok"]
        dialect = "anthropic" if os.path.basename(path).startswith("anthropic") else "openai"
        _, decisions = eng.decide(dialect, body, a, cfg)
        for d in decisions:
            s = slots.setdefault(d["slot"], {"decisions": 0, "would_drop": 0,
                                             "savings_est_tok": 0})
            s["decisions"] += 1
            s["would_drop"] += len(d.get("dropped") or []) or d.get("dropped_count") or 0
            s["savings_est_tok"] += int(d.get("savings_est_tok") or 0)
    return totals, slots


def render(totals, slots, home):
    n = max(1, totals["requests"])
    est_in = max(1, totals["est_in_tok"])
    lines = []
    lines.append("# subproto benchmark (projection)\n")
    lines.append("Corpus: `%s` — %d recorded requests, %s est input tokens total.\n"
                 % (home, totals["requests"], f"{est_in:,}"))
    lines.append("| slot | decisions | dropped units | est tokens saved | % of input | tokens/req |")
    lines.append("|---|--:|--:|--:|--:|--:|")
    order = ["tool_gate", "compact", "context", "effort"]
    total_save = 0
    for slot in order:
        s = slots.get(slot, {"decisions": 0, "would_drop": 0, "savings_est_tok": 0})
        save = s["savings_est_tok"]
        total_save += save
        lines.append("| %s | %d | %d | %s | %.1f%% | %s |" % (
            slot, s["decisions"], s["would_drop"], f"{save:,}",
            100.0 * save / est_in, f"{save // n:,}"))
    lines.append("\n**Projected input-token reduction if all slots enforce: %.1f%%** "
                 "(heuristic stand-ins; the Laya fine-tune is expected to raise "
                 "precision, not change this ceiling.)" % (100.0 * total_save / est_in))
    lines.append("\n*These are projections from measured prompt shapes, not billed "
                 "savings. The live pass-rate–held harness is on the roadmap.*")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mock", type=int, const=24, nargs="?", default=0,
                    help="generate N synthetic agent requests first (no API key)")
    ap.add_argument("--home", default=os.path.expanduser("~/.subproto"),
                    help="state dir with recorded bodies (start proxy with --store-bodies)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "results.md"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    home = tempfile.mkdtemp(prefix="subproto-bench-") if args.mock else args.home
    cfg, _ = build_corpus(home, args.mock)
    if not dataset.record_bodies(cfg):
        print("No recorded bodies at %s/bodies.\n"
              "Run the proxy with --store-bodies and drive some agent traffic, "
              "or use --mock." % home)
        return 1
    totals, slots = measure(cfg)
    if args.json:
        print(json.dumps({"totals": totals, "slots": slots}, indent=1))
    else:
        text = render(totals, slots, home)
        print(text)
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print("\n-> wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
