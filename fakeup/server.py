"""A mock upstream that speaks just enough of both dialects to test the proxy:
streamed SSE with realistic usage events, non-streamed JSON, and errors.

It used to answer every request with the same usage block. That made the whole billing
side of the tool unfalsifiable: `subproto report` printed the identical `billed input`
and `spend` whether the slots had cut 40 % of the request or nothing at all, while
`subproto live` on the same traffic said the tokens were delivered — two pages of one
product disagreeing about whether anything happened (S46/B4). The mock now prices the
body it was actually sent, and it models a prompt cache the way a provider does: the
part of this request that is the same string as the last one is a cache read, so a
proxy that rewrites the cached prefix is billed for it (I2 stops being a claim and
becomes something the meter can see).

Output is still a tariff constant: the mock answers "done" and does not measure what it
wrote, so no page should read an output figure as a result.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLEEP = float(os.environ.get("MOCKUP_SLEEP", "0") or 0)

# The same characters-per-token the proxy's own estimator uses, so the two agree on what
# a request weighs rather than differing by an unexplained factor.
TOK_CHARS = 3.6

# Per dialect: what the mock charges for an answer, and the shape it answers in.
TARIFFS = {
    "anthropic": {"output": 320, "reasoning": 0},
    "openai": {"output": 320, "reasoning": 0},
    "gemini": {"output": 410, "reasoning": 90},
    "responses": {"output": 210, "reasoning": 64},
}


def _tok(text):
    return int(len(text or "") / TOK_CHARS + 0.5)


def prompt_text(body):
    """The request as one string, in the order a provider reads it."""
    parts = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    if isinstance(body.get("instructions"), str):
        parts.append(body["instructions"])
    for tool in body.get("tools") or []:
        parts.append(json.dumps(tool, sort_keys=True))
    for key in ("messages", "input"):
        for item in body.get(key) or []:
            parts.append(item if isinstance(item, str) else json.dumps(item, sort_keys=True))
    if isinstance(body.get("prompt"), str):
        parts.append(body["prompt"])
    if isinstance(body.get("contents"), (list, str)):
        parts.append(json.dumps(body["contents"], sort_keys=True))
    return "\n".join(parts)


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def price(body, tariff, previous=""):
    """Usage for this body, given the text the same session sent last time."""
    text = prompt_text(body)
    total = _tok(text)
    cached = _tok(text[:_common_prefix(text, previous)])
    out = dict(TARIFFS[tariff])
    out.update({"model": body.get("model") or "mock-model",
                "input_uncached": max(0, total - cached),
                "cache_write": 0, "cache_read": cached})
    return out


def anthropic_stream(usage):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "mock", "type": "message", "role": "assistant",
            "model": usage["model"], "content": [], "usage": {
                "input_tokens": usage["input_uncached"],
                "cache_creation_input_tokens": usage["cache_write"],
                "cache_read_input_tokens": usage["cache_read"],
                "output_tokens": 4}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "done"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": usage["output"]}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    for name, payload in events:
        yield "event: %s\ndata: %s\n\n" % (name, json.dumps(payload))
        if SLEEP:
            time.sleep(SLEEP)


def openai_stream(usage):
    for chunk in ({"id": "mock", "object": "chat.completion.chunk", "model": usage["model"],
                   "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": None}]},
                  {"id": "mock", "object": "chat.completion.chunk", "model": usage["model"],
                   "choices": [], "usage": {
                       "prompt_tokens": usage["input_uncached"] + usage["cache_read"]
                       + usage["cache_write"],
                       "completion_tokens": usage["output"],
                       "prompt_tokens_details": {"cached_tokens": usage["cache_read"]},
                       "completion_tokens_details": {"reasoning_tokens": usage["reasoning"]}}}):
        yield "data: %s\n\n" % json.dumps(chunk)
        if SLEEP:
            time.sleep(SLEEP)
    yield "data: [DONE]\n\n"


def _responses_obj(usage):
    return {"id": "resp_mock", "object": "response", "model": usage["model"],
            "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}]}],
            "usage": {
                "input_tokens": usage["input_uncached"] + usage["cache_read"],
                "output_tokens": usage["output"],
                "input_tokens_details": {"cached_tokens": usage["cache_read"]},
                "output_tokens_details": {"reasoning_tokens": usage["reasoning"]}}}


def responses_stream(usage):
    # OpenAI /v1/responses streams lifecycle events; usage rides on the final
    # `response.completed` event, so the parser must pick it up from there.
    for name, payload in (
        ("response.created", {"type": "response.created",
                              "response": {"id": "resp_mock", "model": usage["model"],
                                           "status": "in_progress"}}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "done"}),
        ("response.completed", {"type": "response.completed", "response": _responses_obj(usage)}),
    ):
        yield "event: %s\ndata: %s\n\n" % (name, json.dumps(payload))
        if SLEEP:
            time.sleep(SLEEP)


def _anthropic_obj(usage, echo=None):
    obj = {"id": "mock", "type": "message", "role": "assistant",
           "model": usage["model"], "content": [{"type": "text", "text": "done"}],
           "usage": {"input_tokens": usage["input_uncached"],
                     "cache_creation_input_tokens": usage["cache_write"],
                     "cache_read_input_tokens": usage["cache_read"],
                     "output_tokens": usage["output"]}}
    if echo is not None:
        obj["_mock_echo"] = echo
    return obj


def _gemini_obj(usage):
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"text": "done"}]},
                            "finishReason": "STOP"}],
            "modelVersion": usage["model"],
            "usageMetadata": {
                "promptTokenCount": usage["input_uncached"] + usage["cache_read"],
                "candidatesTokenCount": usage["output"],
                "thoughtsTokenCount": usage["reasoning"],
                "cachedContentTokenCount": usage["cache_read"]}}


def gemini_stream(usage):
    # Gemini streams `:streamGenerateContent` as SSE `data:` frames; each carries
    # cumulative usageMetadata, so the last frame is authoritative.
    yield "data: %s\n\n" % json.dumps(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "do"}]}}]})
    yield "data: %s\n\n" % json.dumps(_gemini_obj(usage))
    if SLEEP:
        time.sleep(SLEEP)


def _sse(self):
    self.send_response(200)
    self.send_header("content-type", "text/event-stream")
    self.send_header("connection", "close")
    self.close_connection = True
    self.end_headers()
    return self.wfile


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw.decode())
        except ValueError:
            return self._json({"error": {"message": "mock: bad json"}}, 400)
        if (self.headers.get("x-force-error") or "") == "1":
            return self._json({"type": "error", "error": {
                "type": "overloaded_error", "message": "mock overloaded"}}, 529)
        model = body.get("model") or "mock-model"
        stream = bool(body.get("stream"))
        is_gemini = "generateContent" in self.path or "streamGenerateContent" in self.path
        is_responses = "/responses" in self.path
        is_anthropic = "/messages" in self.path
        tariff = ("gemini" if is_gemini else "responses" if is_responses
                  else "anthropic" if is_anthropic else "openai")
        if is_gemini:
            stream = stream or "streamGenerateContent" in self.path
        text = prompt_text(body)
        key = (tariff, model)
        seen = getattr(self.server, "seen", None)
        lock = getattr(self.server, "seen_lock", None)
        if seen is None:                      # a bare handler, not behind MockUpstream
            seen, lock = {}, threading.Lock()
            self.server.seen, self.server.seen_lock = seen, lock
        with lock:
            previous = seen.get(key, "")
            usage = price(body, tariff, previous)
            seen[key] = text
        if is_gemini:
            if stream:
                wfile = _sse(self)
                for line in gemini_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json(_gemini_obj(usage))
        if is_responses:
            if stream:
                wfile = _sse(self)
                for line in responses_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json(_responses_obj(usage))
        if is_anthropic:
            if stream:
                wfile = _sse(self)
                for line in anthropic_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json(_anthropic_obj(usage, body if body.get("_mock_echo") else None))
        if stream:
            wfile = _sse(self)
            for line in openai_stream(usage):
                wfile.write(line.encode())
                wfile.flush()
            return
        return self._json({"id": "mock", "object": "chat.completion", "model": model,
                           "choices": [{"index": 0, "message": {"role": "assistant",
                                        "content": "done"}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": usage["input_uncached"] + usage["cache_read"],
                                     "completion_tokens": usage["output"],
                                     "prompt_tokens_details": {"cached_tokens": usage["cache_read"]}}})


class MockUpstream(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=8799):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), Handler)
        self.port = port
        # What each (dialect, model) session last sent, so the next request can be
        # priced against it. This is the mock's whole memory.
        self.seen = {}
        self.seen_lock = threading.Lock()

    def start(self):
        import threading
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
