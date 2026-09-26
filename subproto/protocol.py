import base64
import gzip
import json
import re
import zlib


def maybe_decompress(raw, header_value):
    h = (header_value or "").lower()
    if not raw:
        return raw
    if "gzip" in h or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except (OSError, zlib.error):
            return raw
    return raw


def load_body(raw, content_encoding):
    raw = maybe_decompress(raw, content_encoding)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None


def dump_body(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def body_b64(raw):
    return base64.b64encode(raw or b"").decode("ascii")


def sha256_12(raw):
    import hashlib

    return hashlib.sha256(raw or b"").hexdigest()[:12]


def approx_tokens(text):
    if not text:
        return 0
    return max(1, int(len(text) / 3.6 + 0.5))


def block_text(block):
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return block.get("text") or ""
    if kind == "thinking":
        return block.get("thinking") or ""
    if kind == "tool_use":
        return json.dumps(block.get("input"), separators=(",", ":"))
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            return "\n".join(block_text(b) for b in content)
        return str(content or "")
    if kind == "image":
        return "[image]"
    return ""


def content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block_text(b) for b in content)
    if content is None:
        return ""
    return str(content)


def _gemini_parts_text(parts):
    chunks = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if "text" in p:
            chunks.append(p["text"] or "")
        elif "functionCall" in p:
            chunks.append(json.dumps((p["functionCall"] or {}).get("args"), separators=(",", ":")))
        elif "functionResponse" in p:
            chunks.append(json.dumps((p["functionResponse"] or {}).get("response"),
                                     separators=(",", ":")))
        elif "inlineData" in p or "fileData" in p:
            chunks.append("[media]")
    return "\n".join(chunks)


def _gemini_kind(parts):
    for p in parts or []:
        if isinstance(p, dict):
            if "functionCall" in p:
                return "assistant_tool_call"
            if "functionResponse" in p:
                return "tool_result"
    return None


def _normalize_gemini(body):
    out = []
    si = body.get("systemInstruction") or body.get("system_instruction")
    if isinstance(si, dict):
        txt = _gemini_parts_text(si.get("parts"))
        if txt:
            out.append(("system", txt, "system"))
    elif isinstance(si, str) and si:
        out.append(("system", si, "system"))
    for c in body.get("contents") or []:
        if not isinstance(c, dict):
            continue
        role = c.get("role") or "user"
        if role == "model":
            role = "assistant"
        parts = c.get("parts")
        if isinstance(parts, str):
            parts = [{"text": parts}]
        kind = _gemini_kind(parts) or ("assistant" if role == "assistant" else "user")
        out.append((role, _gemini_parts_text(parts), kind))
    return out


def flatten_tools(tools):
    """Normalize OpenAI / Anthropic / Gemini tool specs into [{name, description, _spec_chars}].

    Gemini packs many tools under one `functionDeclarations` list, so the per-spec
    character cost is measured on the individual declaration, not the wrapper —
    otherwise tool_gate would report a misleading savings figure.
    """
    flat = []
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("functionDeclarations"), list):
            for d in t["functionDeclarations"]:
                if isinstance(d, dict):
                    item = dict(d)
                    item["_spec_chars"] = len(json.dumps(d, separators=(",", ":")))
                    flat.append(item)
            continue
        if not isinstance(t, dict):
            continue
        item = dict(t)
        item["_spec_chars"] = len(json.dumps(t, separators=(",", ":")))
        flat.append(item)
    return flat


def tool_name(t):
    if not isinstance(t, dict):
        return "?"
    return (t.get("name") or (t.get("function") or {}).get("name") or "?")


def filter_tools(tools, keep):
    """Rebuild a request's tools list keeping only names in `keep` (lowercased).

    Preserves each dialect's shape: OpenAI/Anthropic tools are flat entries, while
    Gemini packs declarations under one wrapper — so the inner list is filtered and
    the wrapper is dropped entirely once it empties.
    """
    out = []
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("functionDeclarations"), list):
            kept = [
                d for d in t["functionDeclarations"]
                if tool_name(d).lower() in keep
            ]
            if kept:
                out.append(dict(t, functionDeclarations=kept))
            continue
        if tool_name(t).lower() in keep:
            out.append(t)
    return out


def append_after_prefix(body, note):
    """Put *note* at the very end of the conversation — never inside the cached prefix.

    I2: writing into the top-level `system` string rewrites the prefix itself, which
    busts the provider cache and bills a fresh read on every turn. So the note is
    attached after the last turn, and that turn is cloned rather than mutated: the
    bytes ahead of it — system, tools, all earlier turns — stay identical.
    Returns (body_or_clone, injected).
    """
    out = dict(body)
    msgs = out.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[-1], dict):
        last = msgs[-1]
        if last.get("role") == "assistant":
            # ...assistant, user is the alternation both dialects expect.
            tail = {"role": "user",
                    "content": [{"type": "text", "text": note}]
                    if isinstance(last.get("content"), list) else note}
            out["messages"] = list(msgs) + [tail]
            return out, True
        clone = dict(last)
        content = last.get("content")
        if isinstance(content, list):
            clone["content"] = list(content) + [{"type": "text", "text": note}]
        elif isinstance(content, str):
            clone["content"] = content + "\n\n" + note
        else:
            return body, False
        out["messages"] = list(msgs[:-1]) + [clone]
        return out, True
    contents = out.get("contents")  # Gemini shape
    if isinstance(contents, list) and contents and isinstance(contents[-1], dict):
        last = contents[-1]
        if isinstance(last.get("parts"), list):
            out["contents"] = list(contents[:-1]) + [
                dict(last, parts=list(last["parts"]) + [{"text": note}])]
            return out, True
    return body, False


def normalize_messages(body):
    """Return [(role, text, kind)] for either dialect, in request order."""
    out = []
    if not isinstance(body, dict):
        return out
    if "contents" in body and "messages" not in body:
        return _normalize_gemini(body)
    sys_ = body.get("system")
    if sys_:
        out.append(("system", content_to_text(sys_), "system"))
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        content = m.get("content")
        if role == "tool" or (
            isinstance(content, list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        ):
            kind = "tool_result"
        elif isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content
        ):
            kind = "assistant_tool_call"
        else:
            kind = role
        out.append((role, content_to_text(content), kind))
    return out


IDENT_RE = re.compile(r"[A-Za-z0-9_.$/-]{3,}")
# The extension must be the *whole* extension: an unordered alternation matched `js`
# inside ".json" (and `ts` inside ".tsx"), silently truncating the path — which is
# how a data-file read stayed uncoupleable for the whole of v5.
PATH_RE = re.compile(r"[\w./-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|rb|php|c|h|cpp|cs|swift|kt|sql|json|jsonl|csv|tsv|ya?ml|toml|md|html|css|sh|vue|svelte)(?![A-Za-z0-9_])")
TOKEN_STOP = {
    "the", "and", "for", "with", "you", "are", "was", "that", "this", "from",
    "have", "has", "not", "but", "can", "will", "should", "would", "into",
    "please", "just", "like", "about", "there", "their", "them", "they",
    "code", "file", "files", "make", "make", "sure", "using", "use",
}


def lexical_tokens(text, limit=200):
    toks = []
    seen = set()
    for m in IDENT_RE.finditer(text or ""):
        raw = m.group(0)
        for part in re.split(r"[._/$-]+|(?<=[a-z0-9])(?=[A-Z])", raw):
            part = part.lower().strip(".,;:()[]{}\"'`")
            if len(part) < 3 or part in TOKEN_STOP or part in seen:
                continue
            seen.add(part)
            toks.append(part)
            if len(toks) >= limit:
                return toks
    return toks


def extract_paths(text):
    return [p.lower() for p in PATH_RE.findall(text or "")]


PATH_RUN_RE = re.compile(r"[\w./-]+")
PATH_PUNCT = "._-/"


def paths_named(text, needles):
    """The path tokens in `text` that name one of `needles` — without regexing all of `text`.

    S32: the compiler couples a message to a file only through a path the message
    actually spells out, and a message spells out a handful of the index's files at
    most. `extract_paths` over a 10 KB tool result costs ~80 µs and a decision has a
    dozen of them — that scan was most of the latency gap over the per-slot arm. So each
    needle is located by a C-level `find`, and the regex only ever sees the path-shaped
    run around the hit: `PATH_RE` can never match across a character outside its class,
    so confining it to the run loses no path that the full scan would have found.

    The result is a superset in one direction — a hit inside `xetry.py` returns
    `xetry.py`, which is not the needle — and the caller's own comparisons are what
    reject it. A needle with no extension (`Makefile`) can never arrive here as a path,
    because `PATH_RE` only ever extracts a dotted name.
    """
    if not text or not needles:
        return []
    found, seen, done = [], set(), set()
    for nd in needles:
        if not nd:
            continue
        at = text.find(nd)
        while at >= 0:
            start = at
            while start > 0:
                ch = text[start - 1]
                if not (ch.isalnum() or ch in PATH_PUNCT):
                    break
                start -= 1
            if start not in done:
                done.add(start)
                run = PATH_RUN_RE.match(text, start)
                if run:
                    for p in PATH_RE.findall(run.group(0)):
                        p = p.lower()
                        if p not in seen:
                            seen.add(p)
                            found.append(p)
            at = text.find(nd, at + len(nd))
    return found


def same_path(a, b):
    """Path equality under either direction of truncation: `retry.py` vs `app/x/retry.py`.

    Recorded traffic names the same file three ways — a call argument (`app/x/retry.py`),
    a result header (`/Users/me/repo/app/x/retry.py`) and a graph key (`app/x/retry.py`) —
    so any comparison across those sources has to tolerate a shared suffix.
    """
    a, b = (a or "").lower(), (b or "").lower()
    return bool(a) and bool(b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))
