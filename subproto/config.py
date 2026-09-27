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


class HomeError(OSError):
    """The data directory a command was pointed at cannot be used.

    `--home` and `SUBPROTO_HOME` are user input, and a stranger's typo in one of them
    used to end in a `FileExistsError` traceback with no line they could act on
    (S46/M3). One named error, raised at the boundary, printed by the dispatcher.
    """

    def __init__(self, path, cause):
        detail = getattr(cause, "strerror", None) or cause
        OSError.__init__(self, "%s: %s" % (path, detail))
        self.path = path
        self.cause = cause


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
    def model_timeout_ms(self):
        """How long one System One answer may take before the slot falls back.

        350 ms was set when the only endpoint in the repo answered in under a
        millisecond. The real checkpoint costs 123 ms p50 and 133 ms p95 for a
        9-tool decision on torch CPU (`bench/laya_latency.md`), so the shipped
        default is a working budget for one decision and a tight one for a
        12-option set — raise it to trade latency for the model.

        Read as a number, not through `_get`'s env argument: that path calls
        `_env_flag`, which answers "is this set to a truthy word?" and would turn
        `1500` into `False` and the budget into zero.
        """
        raw = (self._get("model_timeout_ms")
               or os.environ.get("SUBPROTO_MODEL_TIMEOUT_MS"))
        try:
            return max(1, int(str(raw).strip())) if raw is not None else 350
        except ValueError:
            return 350

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

        Unset -> the committed `subproto/systemone/evidence/ablation.json`. Point it at a fresh
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
                    with open(candidate, encoding="utf-8", errors="replace") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        src = data
                except (ValueError, OSError):
                    src = {}
                break
        return cls(source=src, **overrides)

    def ensure_dirs(self):
        for d in (self.data_dir, self.graph_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as exc:
                raise HomeError(d, exc)
        if self.store_bodies:
            try:
                os.makedirs(self.bodies_dir, exist_ok=True)
            except OSError as exc:
                raise HomeError(self.bodies_dir, exc)

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
