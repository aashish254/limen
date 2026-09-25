"""A mock upstream that speaks just enough of both dialects to test the proxy:
streamed SSE with realistic usage events, non-streamed JSON, and errors.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLEEP = float(os.environ.get("MOCKUP_SLEEP", "0") or 0)


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
        usage = {"model": model, "input_uncached": 120, "cache_write": 0,
                 "cache_read": 48000, "output": 320, "reasoning": 0}
        is_gemini = "generateContent" in self.path or "streamGenerateContent" in self.path
        is_responses = "/responses" in self.path
        is_anthropic = "/messages" in self.path
        if is_gemini:
            stream = stream or "streamGenerateContent" in self.path
            usage.update({"input_uncached": 220, "cache_read": 5100,
                          "output": 410, "reasoning": 90})
            if stream:
                wfile = _sse(self)
                for line in gemini_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json(_gemini_obj(usage))
        if is_responses:
            usage.update({"input_uncached": 340, "cache_read": 52000,
                          "output": 210, "reasoning": 64})
            if stream:
                wfile = _sse(self)
                for line in responses_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json(_responses_obj(usage))
        if is_anthropic:
            usage.update({"cache_read": 48000, "cache_write": 900})
            if stream:
                wfile = _sse(self)
                for line in anthropic_stream(usage):
                    wfile.write(line.encode())
                    wfile.flush()
                return
            return self._json({"id": "mock", "type": "message", "role": "assistant",
                               "model": model, "content": [{"type": "text", "text": "done"}],
                               "usage": {"input_tokens": usage["input_uncached"],
                                         "cache_creation_input_tokens": usage["cache_write"],
                                         "cache_read_input_tokens": usage["cache_read"],
                                         "output_tokens": usage["output"]}})
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

    def start(self):
        import threading
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
