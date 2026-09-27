"""A local scoring server that satisfies the System One contract.

It answers POST /score with a per-option keep-probability vector and GET /health
with an ok flag — the protocol `subproto.systemone.HTTPScoreAdapter` speaks, so
anything served here can be registered under any label (`--model laya`,
`SUBPROTO_MODEL=openjev`, ...). Two backends live here:

* `lexical` — a deterministic lexical scorer, the same signal the heuristics use,
  served over the wire so the engine records the selected label instead of
  "heuristic". It is a stand-in and says so.
* `laya` — the real thing: `pip install "laya[serve]"` in a Python >= 3.10 venv
  (the checkpoint requires it; subproto's own 3.9 leg cannot host it, which is
  what the HTTP boundary is for) and one forward pass of
  `convaiinnovations/laya` answers each question with a probability — one the
  library labels uncalibrated at load time, because the checkpoint ships invalid
  temperatures. `bench/laya_latency.py` records that warning above the curve it
  measured rather than smoothing it over.

**Why there is no MLX backend.** SPEC §11 locked "on-device = Apple MLX" before
the weights were read. They are a ModernBERT-large encoder with a 2-layer
classification head, `pipeline_tag: text-classification`, `output_tokens: 0` on
every answer — a non-autoregressive classifier that never generates text.
`mlx_lm` loads causal LMs, so it has no path to this architecture,
and the `laya` package needs torch. The latency bet behind that decision turns out not
to have been the obstacle either: one pass of the torch CPU `choice` shape answers near
SPEC's per-decision budget (`bench/laya_latency.py` publishes the curve, including
whether its tail held inside `SUBPROTO_MODEL_TIMEOUT_MS`), so MLX is not merely awkward
here, it is unnecessary and unavailable. `--backend mlx` therefore refuses with that
explanation rather than pretending.

MPS is *not* the default on purpose: `Router(device=None)` picks MPS and the
process dies with a MetalPerformanceShadersGraph assertion (SIGABRT, exit 134)
before the first answer. CPU is the measured-working device; `LAYA_DEVICE=mps`
is available for a host where it does work.

Run it, from the clone. The real backend needs its own Python >= 3.10 venv with
torch, so the commands below assume `.venv-laya` — that is
`python3.11 -m venv .venv-laya && .venv-laya/bin/pip install "laya[serve]"`:

    python3 -m subproto.laya_server --port 8890                     # lexical
    .venv-laya/bin/python -m subproto.laya_server --backend laya --port 8890
    subproto up --model laya --laya-url http://127.0.0.1:8890
"""

import argparse
import importlib
import importlib.util
import json
import math
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import heuristics, protocol

WORD_RE = re.compile(r"[A-Za-z]{3,}")

# What the encoder is asked, verbatim. Chosen so one pass answers the whole
# option set (see LayaScorer for why this is the affordable shape); a slot's
# keep threshold is not applied here.
_INSTRUCTIONS = "Which of these should stay in the agent's prompt for this request?"
_KEEP_DROP = "For this request, should the agent keep "
_LABELS = {"keep": "it is needed, leave it in the prompt",
           "drop": "it is irrelevant, cut it from the prompt"}


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


def laya_importable():
    """True only when the real `laya` runtime answers for something.

    A directory on sys.path is not a model: `find_spec` reports a namespace
    package as importable, so this imports the module and checks for the entry
    point we actually call.
    """
    try:
        module = importlib.import_module("laya")
    except Exception:
        return False
    return hasattr(module, "Router")


def select_backend(requested=None):
    """auto -> the real checkpoint when its runtime is present, else lexical."""
    requested = (requested or os.environ.get("LAYA_BACKEND") or "auto").lower()
    if requested == "mlx":
        raise RuntimeError(
            "there is no MLX path to Laya: it is a non-autoregressive encoder "
            "(ModernBERT-large, text-classification) and mlx_lm loads causal LMs. "
            'Use --backend laya (torch), which is what the checkpoint ships on.')
    if requested == "laya":
        if not laya_importable():
            raise RuntimeError(
                "the laya backend needs `pip install 'laya[serve]'` on Python "
                ">= 3.10 (this interpreter is %d.%d). `--backend lexical` runs "
                "anywhere." % sys.version_info[:2])
        return "laya"
    if requested == "auto":
        return "laya" if laya_importable() else "lexical"
    if requested == "lexical":
        return "lexical"
    raise RuntimeError("unknown backend %r" % requested)


def option_text(name):
    """How an option is described to the encoder: an underscore is a word break,
    and the criterion must be non-empty for the question to be well-formed."""
    return " ".join(str(name).replace("_", " ").split())


class LayaScorer:
    """Real Laya: one forward pass per question, probabilities as given.

    The checkpoint answers *typed questions*. `choice` over the option set is a
    distribution (it sums to 1), and it is the only shape that can fit SPEC's
    per-decision budget: one pass costs 123 ms p50 and 139 ms worst observed on
    torch CPU, against 800 ms p50 for the per-option `keep_drop` shape.
    `bench/laya_latency.py` publishes the curve with its conditions, including
    whether the tail held inside `SUBPROTO_MODEL_TIMEOUT_MS` on that host that
    day. A distribution is not a
    keep/drop probability, so nothing here thresholds it — the caller's 0.5 rule is a
    contract mismatch this file reports rather than hides (SPEC FR-4, open decision).
    """

    backend = "laya"

    def __init__(self, device=None, shape="choice", threads=None):
        if not laya_importable():
            raise RuntimeError("laya runtime not importable")
        import laya

        if threads:
            import torch
            torch.set_num_threads(int(threads))
        self.shape = shape
        self.device = device or os.environ.get("LAYA_DEVICE") or "cpu"
        self.router = laya.Router(device=self.device)
        self.load_ms = None
        self.calls = 0
        self.last_ms = None
        self.last_answer = {}
        self.warm()

    def warm(self):
        """Pay the checkpoint load before the port opens, not on request #1.

        The first predict costs ~7 s of weight loading; a server that took the
        port first would answer its first decision 45x slower than the curve
        reports, and a health probe would race that load.
        """
        started = time.time()
        self.router.predict("warming", {"warm": {
            "type": "noul", "instructions": "Is this server awake?"}})
        self.load_ms = int((time.time() - started) * 1000)
        return self

    @staticmethod
    def _criteria(options):
        """({criterion key: text}, [(key, option as the caller sent it)]).

        Two separate jobs, and conflating them is how an option goes missing:
        the criterion the encoder reads must be non-empty and unique, while the
        answer must come back keyed by the exact string the caller asked about.
        So an empty or duplicate name is repaired *in the question* (`option 1`,
        `Read#0`) and left alone in the reply. Laya keys its answer by the
        criterion, so without the suffix two identical names would collapse into
        one probability and the last option would be scored as the second.
        """
        names = [str(o) for o in options]
        labels = [n if n.strip() else "option %d" % i for i, n in enumerate(names)]
        counts = {}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        pairs, criteria = [], {}
        for i, label in enumerate(labels):
            key = "%s#%d" % (label, i) if counts[label] > 1 else label
            pairs.append((key, names[i]))
            criteria[key] = option_text(label)
        return criteria, pairs

    def score(self, state, question, options):
        options = list(options or [])
        if not options:
            return {}
        criteria, pairs = self._criteria(options)
        started = time.time()
        if self.shape == "keep_drop":
            probs = self._score_keep_drop(state, criteria, pairs)
        else:
            probs = self._score_choice(state, criteria, pairs)
        self.calls += 1
        self.last_ms = int((time.time() - started) * 1000)
        return probs

    def _score_choice(self, state, criteria, pairs):
        out = self.router.predict(state or " ", {"keep": {
            "type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}})
        ans = ((out.get("answers") or {}).get("keep") or {})
        probs = ans.get("probabilities") or {}
        self.last_answer = {"confidence": ans.get("confidence"),
                            "choice": ans.get("choice"),
                            "input_tokens": (out.get("usage") or {}).get("input_tokens"),
                            "calibration": "choice-distribution"}
        return dict((name, float(probs.get(key, 0.0))) for key, name in pairs)

    def _score_keep_drop(self, state, criteria, pairs):
        """Per-option binary question: an independent probability, N passes."""
        probs, top = {}, None
        for key, name in pairs:
            out = self.router.predict(state or " ", {"keep": {
                "type": "choice",
                "instructions": _KEEP_DROP + criteria[key] + " available?",
                "criteria": dict(_LABELS)}})
            ans = ((out.get("answers") or {}).get("keep") or {})
            probs[name] = float((ans.get("probabilities") or {}).get("keep", 0.0))
            top = ans.get("confidence") if top is None else top
        self.last_answer = {"confidence": top, "input_tokens": None,
                            "calibration": "per-option"}
        return probs


class Scorer:
    """The backend behind /score, plus what to say about it in /health."""

    def __init__(self, backend="lexical", **kw):
        self.backend = backend
        if backend == "laya":
            self._scorer = LayaScorer(**kw)
        elif backend == "lexical":
            self._scorer = None
        else:  # pragma: no cover - select_backend guards the strings
            raise RuntimeError("unknown backend %r" % backend)

    @property
    def info(self):
        if self._scorer is None:
            return {"backend": "lexical", "model": None, "device": None}
        return {"backend": "laya", "model": "convaiinnovations/laya",
                "device": self._scorer.device, "shape": self._scorer.shape,
                "load_ms": self._scorer.load_ms, "calls": self._scorer.calls}

    def score(self, state, question, options):
        if self._scorer is not None:
            return self._scorer.score(state, question, options)
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
            body = {"ok": True, "backend": self.scorer.backend}
            body.update(self.scorer.info)
            return self._json(body)
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
        except RuntimeError as exc:
            # The adapter treats a non-response as "backend unavailable" and the
            # slot falls back to the heuristic; a socket death does the same but
            # leaves nothing to read in the log.
            return self._json({"error": "%s" % exc}, 500)
        reply = {"probabilities": probs, "backend": self.scorer.backend}
        answer = getattr(self.scorer._scorer, "last_answer", None) or {}
        last_ms = getattr(self.scorer._scorer, "last_ms", None)
        if last_ms is not None:
            reply["ms"] = last_ms
        reply.update(dict(("answer_" + k, v) for k, v in answer.items()))
        return self._json(reply)


class LayaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=8890, host="127.0.0.1", backend="auto", **scorer_kw):
        scorer = Scorer(select_backend(backend), **scorer_kw)
        handler = type("Bound", (Handler,), {"scorer": scorer})
        ThreadingHTTPServer.__init__(self, (host, port), handler)
        self.port = port
        self.backend = scorer.backend
        self.scorer = scorer

    def start(self):
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("LAYA_PORT", 8890)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "lexical", "laya", "mlx"],
                    help="laya is the real checkpoint (needs Python >= 3.10); "
                         "auto picks it when importable, else lexical")
    ap.add_argument("--shape", default=os.environ.get("LAYA_SHAPE", "choice"),
                    choices=["choice", "keep_drop"],
                    help="how the question is asked of the encoder: one "
                         "distribution over the options (affordable) or one "
                         "independent probability per option (N passes)")
    ap.add_argument("--device", default=None,
                    help="torch device for the checkpoint (default: cpu; mps "
                         "aborts on this host — see the module docstring)")
    ap.add_argument("--threads", type=int, default=None,
                    help="torch CPU threads (the scoring server's only knob)")
    args = ap.parse_args(argv)
    try:
        srv = LayaServer(args.port, args.host, args.backend, shape=args.shape,
                         device=args.device, threads=args.threads)
    except RuntimeError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    print("laya_server on http://%s:%d (%s)" % (
        args.host, args.port, " ".join("%s=%s" % kv for kv in sorted(srv.scorer.info.items())
                                       if kv[1] is not None)))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
