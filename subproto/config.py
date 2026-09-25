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
            "openai_upstream": self.openai_upstream,
            "anthropic_upstream": self.anthropic_upstream,
            "gemini_upstream": self.gemini_upstream,
        }
