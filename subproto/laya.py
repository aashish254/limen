"""Laya backend adapter.

Laya answers narrow questions (which option / keep-or-drop / how scoreable)
rather than generating prose, so each slot asks it a schema-shaped question and
gets a probability vector back. When no laya server is reachable the engine falls
back to the heuristics and records that it did — the dataset then becomes the
supervision signal for the fine-tune that closes the gap.
"""

import json
import urllib.error
import urllib.request


class LayaClient:
    def __init__(self, base_url, timeout=0.35):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.last_error = None
        self.last_latency_ms = None
        self._cache = {}

    @property
    def available(self):
        return bool(self.base_url)

    def health(self):
        if not self.base_url:
            return {"configured": False}
        try:
            req = urllib.request.Request(self.base_url + "/health", method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return {"configured": True, "ok": r.status == 200,
                        "body": r.read(256).decode("utf-8", "replace")}
        except Exception as exc:
            self.last_error = "%s" % exc
            return {"configured": True, "ok": False, "error": self.last_error}

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
        started = __import__("time").time()
        try:
            req = urllib.request.Request(
                self.base_url + "/score", data=payload,
                headers={"content-type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.last_error = "%s" % exc
            self.last_latency_ms = int((__import__("time").time() - started) * 1000)
            return None
        self.last_latency_ms = int((__import__("time").time() - started) * 1000)
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
