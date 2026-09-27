import http.client
import json
import os
import ssl
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import protocol
from . import usage as usage_mod
from .telemetry import CATEGORIES

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
    "host",
}
# `content-encoding` is deliberately NOT here: it is an end-to-end header, so the
# label must travel with the bytes in both directions or the far side decodes
# garbage. The one exception is the engine path, which re-serialises the body as
# plain JSON and so has to drop the claim itself (see `_passthrough`).
RESPONSE_STRIP = HOP_BY_HOP - {"content-length"}
# We relay response bytes untouched, so the upstream's own length still describes
# them; dropping it would leave an HTTP/1.1 body with no framing but `connection:
# close`, which the SDK reads as a broken stream.

CA_CANDIDATES = (
    os.environ.get("SSL_CERT_FILE"),
    "/etc/ssl/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
)
for _c in CA_CANDIDATES:
    if _c and os.path.exists(_c):
        SSL_CONTEXT = ssl.create_default_context(cafile=_c)
        break
else:
    SSL_CONTEXT = ssl.create_default_context()


def dialect_for(path):
    if "/messages" in path:
        return "anthropic"
    if "generateContent" in path or "streamGenerateContent" in path:
        return "gemini"
    if "/responses" in path:
        return "responses"
    if "/chat/completions" in path or "/completions" in path:
        return "openai"
    return None


def _upstream_for(dialect, config):
    return {
        "anthropic": config.anthropic_upstream,
        "gemini": config.gemini_upstream,
    }.get(dialect, config.openai_upstream)


def client_name(headers):
    ua = (headers.get("user-agent") or "").lower()
    for needle, label in (("claude", "claude-code"), ("codex", "codex"),
                          ("gemini", "gemini-cli"), ("cline", "cline"),
                          ("opencode", "opencode"), ("cursor", "cursor"),
                          ("aider", "aider"), ("antigravity", "antigravity"),
                          ("openai", "openai-sdk")):
        if needle in ua:
            return label
    return ua.split("/")[0][:32] or "unknown"


def analyze_request(body):
    """Prompt-shape features, used both for telemetry and by the decision slots."""
    msgs = protocol.normalize_messages(body)
    cat_chars = dict((c, 0) for c in CATEGORIES)
    hist = dict((c, []) for c in CATEGORIES)
    total = len(msgs) or 1
    for idx, (role, text, kind) in enumerate(msgs):
        cat_chars[kind] = cat_chars.get(kind, 0) + len(text)
        hist.setdefault(kind, []).append((idx + 1) / total)
    tools = protocol.flatten_tools((body or {}).get("tools"))
    tool_names = []
    tool_spec_chars = 0
    for t in tools:
        name = protocol.tool_name(t)
        if name != "?":
            tool_names.append(name)
        tool_spec_chars += t.get("_spec_chars", 0)
    all_text = "\n".join(t for _, t, _ in msgs)
    est = sum(protocol.approx_tokens("x" * n) for n in cat_chars.values() if n)
    est += protocol.approx_tokens("x" * tool_spec_chars) if tool_spec_chars else 0
    user_turns = sum(1 for _, _, k in msgs if k == "user")
    return {
        "msg_count": len(msgs),
        "sys_chars": cat_chars.get("system", 0),
        "tool_spec_chars": tool_spec_chars,
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "cat_chars": cat_chars,
        "hist_age_rank": dict((k, [round(v, 3) for v in hist.get(k, [])]) for k in CATEGORIES),
        "est_in_tok": est,
        "user_turns": user_turns,
        "paths": protocol.extract_paths(all_text),
        "query": all_text[-4000:],
        "features": {
            "msg_count": len(msgs),
            "user_turns": user_turns,
            "est_in_tok": est,
            "tool_count": len(tool_names),
            "cat_chars": cat_chars,
        },
    }


class UpstreamError(Exception):
    def __init__(self, status, body, message=""):
        Exception.__init__(self, message or "upstream %s" % status)
        self.status = status
        self.body = body


def _connect(target, timeout):
    u = urllib.parse.urlparse(target)
    if u.scheme == "http":
        return http.client.HTTPConnection(u.netloc, timeout=timeout)
    return http.client.HTTPSConnection(u.netloc, timeout=timeout, context=SSL_CONTEXT)


def relay(dialect, path, req_headers, raw_body, config, timeout=600.0, tries=2,
          reencoded_body=False):
    """Forward the request untouched, streaming the response as it arrives.

    Yields ("head", status, headers), then ("ttfb", ms) on the first body read,
    then ("chunk", bytes) per network read. `reencoded_body` says these bytes came
    back out of `dump_body`, i.e. they are no longer what the agent sent.
    """
    base = _upstream_for(dialect, config)
    target = base + path
    parts = urllib.parse.urlparse(target)
    strip = HOP_BY_HOP | {"content-encoding"} if reencoded_body else HOP_BY_HOP
    hdrs = dict((k, v) for k, v in req_headers.items() if k.lower() not in strip)
    if not any(k.lower() in ("authorization", "x-api-key", "api-key", "x-goog-api-key")
               for k in hdrs):
        env_key = {"openai": "OPENAI_API_KEY", "responses": "OPENAI_API_KEY",
                   "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[dialect]
        key = os.environ.get(env_key, "")
        if key:
            if dialect == "anthropic":
                hdrs["x-api-key"] = key
            elif dialect == "gemini":
                hdrs["x-goog-api-key"] = key
            else:
                hdrs["Authorization"] = "Bearer " + key
    for attempt in range(tries):
        conn = _connect(target, timeout)
        try:
            conn.request("POST", parts.path + (parts.query and "?" + parts.query),
                         body=raw_body or None, headers=hdrs)
            resp = conn.getresponse()
            if resp.status in (502, 503, 504) and attempt + 1 < tries:
                resp.read()
                conn.close()
                time.sleep(0.4 * (attempt + 1))
                continue
            started = time.time()
            first = True
            try:
                yield ("head", resp.status, dict(resp.getheaders()))
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    if first:
                        yield ("ttfb", int((time.time() - started) * 1000))
                        first = False
                    yield ("chunk", chunk)
            finally:
                conn.close()
            return
        except (http.client.HTTPException, OSError, ssl.SSLError) as exc:
            try:
                conn.close()
            except Exception:
                pass
            if attempt + 1 >= tries:
                body = protocol.dump_body({"error": {
                    "type": "subproto_upstream_error",
                    "message": "%s: %s" % (type(exc).__name__, exc),
                    "target": target,
                }})
                raise UpstreamError(502, body, "%s" % (exc,))
            time.sleep(0.3 * (attempt + 1))


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, config, telemetry, engine=None):
        self.proto_config = config
        self.telemetry = telemetry
        self.engine = engine
        ThreadingHTTPServer.__init__(
            self, addr, make_handler(config, telemetry, engine))


def make_handler(config, telemetry, engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "subproto/0.1"
        # Deliberately no `timeout` here. A relayed stream can pause before its first
        # token for longer than any bound this file could pick, and a socket timeout on
        # the write would cut that stream off mid-answer. A stalled client instead holds
        # one thread, and `daemon_threads` above means it cannot hold the process.

        def log_message(self, fmt, *args):
            if os.environ.get("SUBPROTO_VERBOSE"):
                BaseHTTPRequestHandler.log_message(self, fmt, *args)

        def _read_body(self):
            length = self.headers.get("content-length")
            if length is not None:
                return self.rfile.read(int(length))
            if (self.headers.get("transfer-encoding") or "").lower() != "chunked":
                return b""
            out = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0] or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                out.extend(self.rfile.read(size))
                self.rfile.readline()
            return bytes(out)

        def _send_json(self, status, obj):
            raw = protocol.dump_body(obj)
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path in ("/healthz", "/_subproto/health"):
                d = {"ok": True, "engine": bool(engine), "config": config.to_dict()}
                if engine is not None:
                    d["model"] = engine.model_status()
                return self._send_json(200, d)
            return self._send_json(404, {"error": {"message": "unknown path " + self.path}})

        def do_POST(self):
            dialect = dialect_for(self.path)
            if dialect is None:
                return self._send_json(404, {"error": {
                    "message": "subproto serves /v1/chat/completions, /v1/responses "
                               "and /v1/messages"}})
            self._passthrough(dialect)

        def _passthrough(self, dialect):
            t0 = time.time()
            raw = self._read_body()
            body = protocol.load_body(raw, self.headers.get("content-encoding"))
            analysis = analyze_request(body) if isinstance(body, dict) else {}
            rec = {
                "ts": t0,
                "api": dialect,
                "path": self.path,
                "client": client_name(self.headers),
                "stream": bool(isinstance(body, dict) and body.get("stream")),
                "model": body.get("model") if isinstance(body, dict) else None,
                "features": analysis,
                "body_sha": protocol.sha256_12(raw),
                "features_sha": protocol.sha256_12(protocol.dump_body(analysis)),
            }
            if config.store_bodies and raw:
                import gzip
                os.makedirs(config.bodies_dir, exist_ok=True)
                name = "%s_%s.json.gz" % (dialect, rec["body_sha"])
                with gzip.open(os.path.join(config.bodies_dir, name), "wb") as f:
                    f.write(raw)
            sent = raw
            reencoded = False
            if engine is not None and isinstance(body, dict):
                try:
                    new_body, decisions = engine.decide(dialect, body, analysis, config,
                                                        body_sha=rec["body_sha"])
                    if decisions:
                        rec["decisions"] = decisions
                        rec["engine_ms"] = round(
                            sum(d.get("decision_ms", 0) for d in decisions), 3)
                        if new_body is not None:
                            sent = protocol.dump_body(new_body)
                            reencoded = True
                            rec["interventions"] = [d["slot"] for d in decisions
                                                   if d.get("applied")]
                except Exception as exc:
                    rec["err"] = "engine:%s" % type(exc).__name__
            feeder = usage_mod.ByteFeeder(usage_mod.UsageAccumulator(dialect))
            status = None
            headers = {}
            body_tail = bytearray()
            try:
                for item in relay(dialect, self.path, self.headers, sent, config,
                                  reencoded_body=reencoded):
                    if item[0] == "head":
                        status, headers = item[1], item[2]
                        feeder.content_type = headers.get("content-type", "")
                        self.send_response(status)
                        for k, v in headers.items():
                            if k.lower() not in RESPONSE_STRIP:
                                self.send_header(k, v)
                        self.send_header("x-subproto", "0.1")
                        if "event-stream" in (headers.get("content-type") or ""):
                            self.send_header("connection", "close")
                            self.close_connection = True
                        self.end_headers()
                    elif item[0] == "ttfb":
                        rec["ttfb_ms"] = item[1]
                    else:
                        self.wfile.write(item[1])
                        feeder.feed(item[1])
            except UpstreamError as exc:
                if status is None:
                    self._send_json(exc.status, json.loads(exc.body.decode()))
                    status = exc.status
                rec["err"] = str(exc)[:400]
            except (BrokenPipeError, ConnectionResetError):
                rec["err"] = "client_disconnect"
            except OSError as exc:
                rec["err"] = "io:%s" % exc
            feeder.finish(headers.get("content-encoding", ""))
            rec["status"] = status
            rec["latency_ms"] = int((time.time() - t0) * 1000)
            u = feeder.acc.usage()
            if not u["model"]:
                u["model"] = rec["model"]
            rec["usage"] = u
            try:
                telemetry.record(rec)
            except Exception as exc:
                if os.environ.get("SUBPROTO_VERBOSE"):
                    print("telemetry record failed:", exc)
    return Handler


def serve(config, telemetry, engine=None, host="127.0.0.1"):
    srv = ProxyServer((host, config.port), config, telemetry, engine)
    return srv
