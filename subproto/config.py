import json
import os

DEFAULT_DATA_DIR = os.path.expanduser("~/.subproto")

OPENAI_UPSTREAM = os.environ.get("SUBPROTO_OPENAI_UPSTREAM", "https://api.openai.com")
ANTHROPIC_UPSTREAM = os.environ.get("SUBPROTO_ANTHROPIC_UPSTREAM", "https://api.anthropic.com")
GEMINI_UPSTREAM = os.environ.get("SUBPROTO_GEMINI_UPSTREAM",
                                 "https://generativelanguage.googleapis.com")

TRUTHY = ("1", "true", "yes", "on")


def _env_flag(name, default=None):
    if name not in os.environ:
        return default
    return os.environ[name].strip().lower() in TRUTHY


class Config:
    def __init__(self, source=None, **overrides):
        self.source = source or {}
        self.overrides = overrides

    def _get(self, key, env=None, default=None):
        if self.overrides.get(key) is not None:
            return self.overrides[key]
        if env is not None and _env_flag(env) is not None:
            return _env_flag(env)
        if key in self.source:
            return self.source[key]
        return default

    @property
    def data_dir(self):
        return os.path.abspath(os.path.expanduser(
            self._get("data_dir") or os.environ.get("SUBPROTO_HOME") or DEFAULT_DATA_DIR
        ))

    @property
    def port(self):
        return int(self._get("port") or os.environ.get("SUBPROTO_PORT") or 8787)

    @property
    def store_bodies(self):
        return bool(self._get("store_bodies", "SUBPROTO_STORE_BODIES", False))

    @property
    def record_features(self):
        return bool(self._get("record_features", "SUBPROTO_RECORD_FEATURES", True))

    @property
    def laya_url(self):
        return self._get("laya_url") or os.environ.get("LAYA_URL")

    @property
    def model(self):
        """Selected System One backend: a registry name or an http(s) URL."""
        return self._get("model") or os.environ.get("SUBPROTO_MODEL")

    @property
    def model_url(self):
        """Where a named backend is served (SUBPROTO_MODEL=openjev + this)."""
        return self._get("model_url") or os.environ.get("SUBPROTO_MODEL_URL")

    @property
    def model_by_slot(self):
        """Per-slot overrides from the config file, e.g. {"compact": "djev"}.

        SUBPROTO_MODEL_<SLOT> env vars are read by the registry and win over this;
        `systemone.MODEL_SLOTS` is the list of slots a model can answer.
        """
        return self._get("model_by_slot") or {}

    @property
    def router(self):
        """Route un-pinned slots by measured evidence instead of a named model.

        Off by default (observation-first): manual selection stays in force until
        SUBPROTO_ROUTER=on (or config "router": true) opts in.
        """
        return bool(self._get("router", "SUBPROTO_ROUTER", False))

    @property
    def router_budget_ms(self):
        """Latency ceiling (p50 decision ms) an adapter must fit to be routed to.

        Unset -> no budget: rank purely on measured precision.
        """
        raw = (self._get("router_budget_ms")
               or os.environ.get("SUBPROTO_ROUTER_BUDGET_MS"))
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @property
    def router_evidence(self):
        """Path to an ablation results file the Router ranks against.

        Unset -> the committed `bench/ablation.json`. Point it at a fresh
        `bench/ablation.py --write` output (or the labelled M4 corpus) to re-route
        without a code change.
        """
        return self._get("router_evidence") or os.environ.get("SUBPROTO_ROUTER_EVIDENCE")

    @property
    def openai_upstream(self):
        return (self._get("openai_upstream") or OPENAI_UPSTREAM).rstrip("/")

    @property
    def anthropic_upstream(self):
        return (self._get("anthropic_upstream") or ANTHROPIC_UPSTREAM).rstrip("/")

    @property
    def gemini_upstream(self):
        return (self._get("gemini_upstream") or GEMINI_UPSTREAM).rstrip("/")

    @property
    def db_path(self):
        return os.path.join(self.data_dir, "telemetry.db")

    @property
    def bodies_dir(self):
        return os.path.join(self.data_dir, "bodies")

    @property
    def graph_dir(self):
        return os.path.join(self.data_dir, "graphs")

    @classmethod
    def load(cls, path=None, **overrides):
        src = {}
        candidates = [path] if path else [
            os.environ.get("SUBPROTO_CONFIG"),
            "./.subproto.json",
            os.path.join(DEFAULT_DATA_DIR, "config.json"),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                try:
                    with open(candidate) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        src = data
                except (ValueError, OSError):
                    src = {}
                break
        return cls(source=src, **overrides)

    def ensure_dirs(self):
        for d in (self.data_dir, self.graph_dir):
            os.makedirs(d, exist_ok=True)
        if self.store_bodies:
            os.makedirs(self.bodies_dir, exist_ok=True)

    def to_dict(self):
        return {
            "data_dir": self.data_dir,
            "port": self.port,
            "store_bodies": self.store_bodies,
            "record_features": self.record_features,
            "laya_url": self.laya_url,
            "model": self.model,
            "model_url": self.model_url,
            "model_by_slot": self.model_by_slot,
            "router": self.router,
            "router_budget_ms": self.router_budget_ms,
            "openai_upstream": self.openai_upstream,
            "anthropic_upstream": self.anthropic_upstream,
            "gemini_upstream": self.gemini_upstream,
        }
