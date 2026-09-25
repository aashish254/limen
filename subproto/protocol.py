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
PATH_RE = re.compile(r"[\w./-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|rb|php|c|h|cpp|cs|swift|kt|sql|json|ya?ml|toml|md|html|css|sh|vue|svelte)")
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
