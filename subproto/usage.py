"""Token usage extraction for the OpenAI and Anthropic dialects, both streamed
and non-streamed. Streaming is the common case for coding agents, so usage has
to be reconstructed from SSE events instead of a single JSON body.
"""

import json


def empty():
    return {
        "model": None,
        "input_uncached": 0,
        "cache_write": 0,
        "cache_read": 0,
        "output": 0,
        "reasoning": 0,
        "source": "none",
    }


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def from_openai(obj):
    u = obj.get("usage") or {}
    if not u:
        return None
    details = u.get("prompt_tokens_details") or {}
    out_details = u.get("completion_tokens_details") or {}
    inp = _int(u.get("prompt_tokens"))
    cached = _int(details.get("cached_tokens"))
    return {
        "model": obj.get("model"),
        "input_uncached": max(0, inp - cached),
        "cache_write": 0,
        "cache_read": cached,
        "output": _int(u.get("completion_tokens")),
        "reasoning": _int(out_details.get("reasoning_tokens")),
        "source": "openai",
    }


def from_anthropic(obj):
    u = obj.get("usage")
    if not u:
        return None
    inp = _int(u.get("input_tokens"))
    cw = _int(u.get("cache_creation_input_tokens"))
    cr = _int(u.get("cache_read_input_tokens"))
    return {
        "model": obj.get("model"),
        "input_uncached": inp,
        "cache_write": cw,
        "cache_read": cr,
        "output": _int(u.get("output_tokens")),
        "reasoning": 0,
        "source": "anthropic",
    }


def from_gemini(obj):
    um = obj.get("usageMetadata")
    if not um:
        return None
    inp = _int(um.get("promptTokenCount"))
    cached = _int(um.get("cachedContentTokenCount"))
    return {
        "model": obj.get("modelVersion") or obj.get("model"),
        "input_uncached": max(0, inp - cached),
        "cache_write": 0,
        "cache_read": cached,
        "output": _int(um.get("candidatesTokenCount")),
        "reasoning": _int(um.get("thoughtsTokenCount")),
        "source": "gemini",
    }


def from_responses(obj):
    """OpenAI /v1/responses: usage lives on the (completed) response object."""
    resp = obj.get("response") if (obj.get("type") or "").startswith("response.") else obj
    if not isinstance(resp, dict):
        return None
    u = resp.get("usage")
    if not u:
        return None
    inp = _int(u.get("input_tokens"))
    cached = _int((u.get("input_tokens_details") or {}).get("cached_tokens"))
    reasoning = _int((u.get("output_tokens_details") or {}).get("reasoning_tokens"))
    return {
        "model": resp.get("model") or obj.get("model"),
        "input_uncached": max(0, inp - cached),
        "cache_write": 0,
        "cache_read": cached,
        "output": _int(u.get("output_tokens")),
        "reasoning": reasoning,
        "source": "responses",
    }


# Every non-anthropic dialect reads a single usage object per response (or the
# final cumulative one for streams), so a plain overwrite is correct.
_DIALECT_READERS = {
    "openai": from_openai,
    "gemini": from_gemini,
    "responses": from_responses,
}



def merge(streamed, final):
    if not streamed:
        return final
    if not final:
        return streamed
    out = dict(final)
    for k in ("input_uncached", "cache_write", "cache_read", "output", "reasoning"):
        if not out.get(k):
            out[k] = streamed.get(k, 0)
    out["model"] = out.get("model") or streamed.get("model")
    return out


def total_in(u):
    return u["input_uncached"] + u["cache_write"] + u["cache_read"]


def total_tok(u):
    return total_in(u) + u["output"] + u["reasoning"]


class UsageAccumulator:
    """Feed SSE payloads (or a whole JSON body); yields provider usage deltas.

    Anthropic streams usage across two event families (message_start carries
    input tokens, each message_delta carries an output increment), so output
    must be summed per delta and inputs taken from the first sighting.
    """

    def __init__(self, dialect):
        self.dialect = dialect
        self.model = None
        self.input_uncached = 0
        self.cache_write = 0
        self.cache_read = 0
        self.output = 0
        self.reasoning = 0
        self.source = "none"
        self._anthropic_inputs_seen = False

    def _set_inputs(self, u):
        if self._anthropic_inputs_seen:
            return
        self._anthropic_inputs_seen = True
        self.input_uncached = u["input_uncached"]
        self.cache_write = u["cache_write"]
        self.cache_read = u["cache_read"]

    def usage(self):
        return {
            "model": self.model,
            "input_uncached": self.input_uncached,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
            "output": self.output,
            "reasoning": self.reasoning,
            "source": self.source,
        }

    def feed_obj(self, obj):
        if not isinstance(obj, dict):
            return
        if obj.get("model") and not self.model:
            self.model = obj["model"]
        reader = _DIALECT_READERS.get(self.dialect)
        if reader is not None:
            u = reader(obj)
            if u:
                self.source = u["source"]
                self.input_uncached = u["input_uncached"]
                self.cache_write = u["cache_write"]
                self.cache_read = u["cache_read"]
                self.output = u["output"]
                self.reasoning = u["reasoning"]
                self.model = self.model or u["model"]
            return
        t = obj.get("type")
        if t == "message_start":
            u = from_anthropic(obj.get("message") or {})
            if u:
                self.source = "anthropic"
                self._set_inputs(u)
                self.model = self.model or u["model"]
        elif t == "message_delta":
            u = obj.get("usage")
            if isinstance(u, dict):
                self.source = "anthropic"
                delta = _int(u.get("output_tokens"))
                if delta:
                    self.output = delta
        elif obj.get("usage") and t in (None, "message"):
            u = from_anthropic(obj)
            if u:
                self.source = "anthropic"
                self._set_inputs(u)
                self.output = max(self.output, u["output"])

    def feed_sse(self, text):
        for line in text.split("\n"):
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                import json

                self.feed_obj(json.loads(payload))
            except ValueError:
                continue

    def feed_body(self, raw, content_type=""):
        if "event-stream" in (content_type or "") or raw[:5] == b"data:" or raw[:1] == b"\n":
            self.feed_sse(raw.decode("utf-8", "replace"))
            return
        try:
            self.feed_obj(json.loads(raw.decode("utf-8", "replace")))
        except ValueError:
            pass


class ByteFeeder:
    """Buffer network reads so SSE events are only parsed once they are complete.

    Providers flush each SSE event with a blank line, so events are split on
    \\n\\n; anything still in the buffer may be a partial frame and is left alone.
    """

    def __init__(self, accumulator, content_type=""):
        self.acc = accumulator
        self.content_type = content_type or ""
        self.raw = bytearray()

    def feed(self, chunk):
        if "event-stream" not in self.content_type:
            self.raw.extend(chunk)
            return
        self.acc_pending = getattr(self, "acc_pending", b"") + chunk
        while b"\n\n" in self.acc_pending:
            event, self.acc_pending = self.acc_pending.split(b"\n\n", 1)
            self.acc.feed_sse(event.decode("utf-8", "replace"))

    def finish(self, content_encoding=""):
        from . import protocol

        if "event-stream" not in self.content_type:
            self.raw = bytearray(protocol.maybe_decompress(bytes(self.raw), content_encoding))
            try:
                self.acc.feed_obj(json.loads(bytes(self.raw).decode("utf-8", "replace")))
            except ValueError:
                pass
            return
        if getattr(self, "acc_pending", b""):
            self.acc.feed_sse(self.acc_pending.decode("utf-8", "replace"))

