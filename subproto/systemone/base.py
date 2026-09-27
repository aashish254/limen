"""The System One adapter contract.

A decision slot asks one question — "which of these options should survive?" —
and gets a probability per option back. Any model that can answer that question
in HTTP JSON can drive subproto: Laya, OpenJev, djev, SemIf, a quantized LoRA
behind a local server, or a vendor endpoint. Nothing downstream of this file may
assume which one is answering.
"""

import json
import time
import urllib.error
import urllib.request

# One answer's budget, in seconds. `Config.model_timeout_ms` overrides it; see
# that property for why the shipped number starves a real checkpoint.
DEFAULT_TIMEOUT_S = 0.35


def timeout_for(config, default=DEFAULT_TIMEOUT_S):
    """The adapter timeout a config asks for, in seconds.

    Every `HTTPScoreAdapter` is built from here so one setting governs the whole
    ladder — a versioned checkpoint, a registry name, and the bench's probe all
    get the same budget, and a slow model degrades identically in each.
    """
    ms = getattr(config, "model_timeout_ms", None) if config is not None else None
    try:
        return max(0.001, float(ms) / 1000.0) if ms else default
    except (TypeError, ValueError):
        return default


class ModelAdapter:
    """Base class: `score` returns {option: probability} or None when unavailable.

    Subclasses set a distinct `label`; that string is what the engine records per
    decision and what the dataset carries as the supervision provenance.
    """

    label = "model"

    def __init__(self, base_url=None, timeout=DEFAULT_TIMEOUT_S):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.last_error = None
        self.last_latency_ms = None

    @property
    def available(self):
        return bool(self.base_url)

    def health(self):
        if not self.base_url:
            return {"configured": False}
        return {"configured": True, "ok": True, "label": self.label}

    def score(self, state, question, options, cache_key=None):
        return None


class HTTPScoreAdapter(ModelAdapter):
    """Adapter for the `/health` + POST `/score` protocol subproto speaks.

    The wire shape is deliberately minimal and model-agnostic so that any local
    server exposing it — including `python -m subproto.laya_server` — can be
    registered under any label without touching this code.
    """

    def __init__(self, base_url, label="model", timeout=DEFAULT_TIMEOUT_S):
        ModelAdapter.__init__(self, base_url, timeout)
        if label:
            self.label = label
        self._cache = {}

    def health(self):
        if not self.base_url:
            return {"configured": False}
        try:
            req = urllib.request.Request(self.base_url + "/health", method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return {"configured": True, "ok": r.status == 200,
                        "label": self.label,
                        "body": r.read(256).decode("utf-8", "replace")}
        except Exception as exc:
            self.last_error = "%s" % exc
            return {"configured": True, "ok": False, "label": self.label,
                    "error": self.last_error}

    def score(self, state, question, options, cache_key=None):
        """Return {option: probability} or None when the backend is unavailable."""
        if not self.base_url:
            return None
        if cache_key is not None and cache_key in self._cache:
            return self._cache[cache_key]
        payload = json.dumps({
            "state": state,
            "question": question,
            "options": list(options),
            "output": "probability",
        }).encode()
        started = time.time()
        try:
            req = urllib.request.Request(
                self.base_url + "/score", data=payload,
                headers={"content-type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.last_error = "%s" % exc
            self.last_latency_ms = int((time.time() - started) * 1000)
            return None
        self.last_latency_ms = int((time.time() - started) * 1000)
        probs = (data or {}).get("probabilities") or (data or {}).get("scores")
        if not isinstance(probs, dict):
            self.last_error = "unexpected response: %s" % str(data)[:120]
            return None
        out = dict((k, float(v)) for k, v in probs.items())
        if cache_key is not None:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[cache_key] = out
        return out
