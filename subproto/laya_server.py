"""A local scoring server that satisfies the contract `laya.py` speaks.

It answers POST /score with a per-option keep-probability vector and GET /health
with an ok flag. The default backend is a *deterministic lexical scorer* — the
same signal the heuristics use, served over the wire so the engine records
`backend == "laya"`. This is deliberately not a real Laya model: the fine-tuned
MLX checkpoint is external (TODO T16 / SPEC §11), and until then an honest
lexical stand-in behind the real interface beats a fake one.

If Apple MLX is installed *and* a checkpoint is configured, the `mlx` backend is
selected instead; otherwise we stay on lexical. That guard is what makes T16 pure
wiring rather than a redesign.

Run it:
    python -m subproto.laya_server --port 8890
    LAYA_URL=http://127.0.0.1:8890 subproto up
"""

import argparse
import importlib.util
import json
import math
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import heuristics, protocol

WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _tokens(text):
    return set(w.lower() for w in WORD_RE.findall(text or ""))


def lexical_probabilities(state, question, options):
    """Relevance of each option to `state`, in [0,1].

    `state` is the latest user turn; `question` is kept for the interface
    (Laya answers narrow questions) though scoring is question-agnostic here.
    """
    q = _tokens(state)
    core = heuristics.CORE_TOOLS
    out = {}
    for opt in options:
        name = (opt or "?").lower()
        terms = _tokens(name.replace("_", " ").replace("/", " ")) | set(
            t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 3)
        if not terms:
            out[opt] = 0.5
            continue
        overlap = len(q & terms)
        raw = overlap / float(max(1, min(len(terms), 12)))
        p = 0.05 + 0.9 * math.tanh(raw * 1.8)
        if name in core or any(c in name for c in ("read", "edit", "bash", "grep")):
            p = max(p, 0.92)
        out[opt] = round(max(0.0, min(1.0, p)), 4)
    return out


def mlx_available():
    return importlib.util.find_spec("mlx") is not None


def select_backend(requested=None):
    """auto -> mlx if runtime + checkpoint present, else lexical."""
    requested = (requested or os.environ.get("LAYA_BACKEND") or "auto").lower()
    if requested == "mlx" or (requested == "auto" and mlx_available()
                              and os.environ.get("LAYA_MLX_MODEL")):
        if not mlx_available():
            raise RuntimeError("mlx backend requested but Apple MLX is not installed")
        return "mlx"
    return "lexical"


class Scorer:
    def __init__(self, backend="lexical"):
        self.backend = backend
        self._model = None
        if backend == "mlx":
            # Guarded: no quantized checkpoint ships in-repo (T16 is external).
            from mlx import nn  # noqa: F401  (import only when actually selected)
            raise NotImplementedError(
                "MLX scoring needs the quantized Laya checkpoint (T16); "
                "run without LAYA_BACKEND=mlx to use the lexical scorer")

    def score(self, state, question, options):
        return lexical_probabilities(state, question, options)


class Handler(BaseHTTPRequestHandler):
    scorer = None  # set by the server factory

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            return self._json({"ok": True, "backend": self.scorer.backend})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/score":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(n).decode())
            options = body.get("options") or []
            probs = self.scorer.score(body.get("state") or "",
                                      body.get("question") or "keep", options)
        except (ValueError, KeyError) as exc:
            return self._json({"error": "bad request: %s" % exc}, 400)
        return self._json({"probabilities": probs, "backend": self.scorer.backend})


class LayaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=8890, host="127.0.0.1", backend="auto"):
        handler = type("Bound", (Handler,), {"scorer": Scorer(select_backend(backend))})
        ThreadingHTTPServer.__init__(self, (host, port), handler)
        self.port = port
        self.backend = handler.scorer.backend

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=int(os.environ.get("LAYA_PORT", 8890)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--backend", default="auto", choices=["auto", "lexical", "mlx"])
    args = ap.parse_args(argv)
    srv = LayaServer(args.port, args.host, args.backend)
    print("laya_server on http://%s:%d (backend=%s)" % (args.host, args.port, srv.backend))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
