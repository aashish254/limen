"""graphify-lite: an offline import/symbol graph of the working repo.

The point of the graph is that a decision can be made about *which files a task
touches* without asking a frontier model to read through the repo. Indexing is
paid once at `subproto graph`; every later lookup is a sub-millisecond scan of
the cached index.
"""

import ast
import io
import json
import os
import re
import time

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript",
    ".tsx": "typescript",
    # S27: the files an agent reads that have no parser. A schema note and a design doc
    # are as load-bearing as a module for a "why is this migration failing" turn, and an
    # index that skips them leaves the compiler with no evidence about them at all.
    ".md": "markdown", ".markdown": "markdown", ".rst": "text", ".txt": "text",
    ".sql": "sql",
    # S31 — FR-10's documented hole, closed. A task names a *fixture* ("the refund
    # fixture", `amount_cents`) as often as it names a module, and a data file's keys
    # are exactly its symbols: the shape of what is in there, without reading it.
    ".json": "json", ".csv": "csv", ".tsv": "tsv", ".yaml": "yaml", ".yml": "yaml",
}

DATA_LANGS = ("json", "yaml", "csv", "tsv")

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".next", ".pytest_cache", "target", "vendor", ".mypy_cache", ".ruff_cache",
             ".subproto", "site-packages"}

FILE_CAP = 4 * 1024 * 1024

JS_IMPORT_RE = re.compile(
    r"""(?:^|[\s;}])import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|(?:^|[\s;}])export\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.M,
)
JS_EXPORT_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:default\s+)?"
    r"(?:function\s*\*?\s*([A-Za-z0-9_]+)|class\s+([A-Za-z0-9_]+)"
    r"|(?:const|let|var)\s+([A-Za-z0-9_]+))",
    re.M,
)
JS_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{2,}")
COMMENT_RE = re.compile(r"(?:^|\n)[ \t]*(?:#|//)[ \t]*(.{4,160})")

# S27 — a "symbol" for a file with no AST. In a heading or a DDL statement, the words
# *are* the API a task refers to ("the refunds migration"), so they get the same 3.0
# match `parse_python` symbols get; the prose body only ever reaches 0.6 via hints.
MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}[ \t]+(.{2,120})$", re.M)
RST_TITLE_RE = re.compile(r"^([A-Za-z0-9 ._:'-]{2,120})\n[=\-~^]{3,}[ \t]*$", re.M)
SQL_OBJECT_RE = re.compile(
    r"\b(?:CREATE|ALTER|DROP|FROM|JOIN|INTO|UPDATE)\s+"
    r"(?:TABLE\s+|VIEW\s+|INDEX\s+|MATERIALIZED\s+|EXISTS\s+|IF\s+NOT\s+)*"
    r"([A-Za-z_][\w.]*)", re.I)
SQL_COMMENT_RE = re.compile(r"--[ \t]*(.{4,160})")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def _title_words(text):
    """Tokens of a heading / DDL object name, split the way `lexical_tokens` splits a
    query — otherwise `refund_ledger` in the file can never match `refund` in the task."""
    out = []
    for raw in _WORD_RE.findall(text or ""):
        for part in re.split(r"[_$-]+", raw):
            part = part.lower()
            if len(part) >= 3 and part not in out:
                out.append(part)
    return out


def parse_doc(src, lang):
    """Return (symbols, doc) for markdown / rst-txt / sql. See S27 in TODO.md."""
    if lang == "markdown":
        heads = [m.group(1) for m in MD_HEADING_RE.finditer(src)]
    elif lang == "sql":
        heads = [m.group(1) for m in SQL_OBJECT_RE.finditer(src)]
    else:
        heads = [m.group(1) for m in RST_TITLE_RE.finditer(src)]
        if not heads:
            heads = [ln.strip() for ln in src.splitlines() if ln.strip()][:2]
    syms = []
    for h in heads:
        for w in _title_words(h):
            if w not in syms:
                syms.append(w)
    if lang == "sql":
        doc = " ".join(m.group(1) for m in list(SQL_COMMENT_RE.finditer(src))[:4])
    else:
        doc = " ".join(heads[:4]) or " ".join(src.split()[:40])
    return syms, doc


# S31 — a data file's API is its *shape*: nested keys for JSON/YAML, header columns
# for CSV/TSV. The values are deliberately not indexed — they are what the agent is
# reading the file to find, and putting them in the index would rank every fixture
# that happens to contain the word "error".
JSON_KEY_RE = re.compile(r'"([^"\n]{1,80})"\s*:')
YAML_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[^:#\-][^:]*?):(?P<rest>.*)$")
# A column name is a name, not a value: this rejects a binary or JSON blob whose
# first line csv.reader would otherwise hand back as a single "header" cell.
COLUMN_RE = re.compile(r"^[A-Za-z_][\w. ()-]{0,60}$")


def _json_key_paths(obj, prefix="", out=None, limit=400):
    """Nested key paths of a parsed JSON document: `refunds[].amount_cents`, `meta.version`.

    A list contributes one `[]`-marked path (its items share a schema) and is walked
    through its first dict item, which is where a fixture's column names live.
    """
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else str(k)
            out.append(path)
            _json_key_paths(v, path, out, limit)
    elif isinstance(obj, list):
        for item in obj[:1]:
            _json_key_paths(item, prefix + "[]" if prefix else "[]", out, limit)
    return out


def _yaml_key_paths(src, limit=400):
    """Nested key paths from indentation, with no YAML library.

    Only `key:` lines are read, so a value that happens to contain a colon cannot
    become a symbol; the indent stack is what makes `refund_ledger: -> amount_cents`
    a two-level path.
    """
    out = []
    stack = []
    for line in src.splitlines():
        raw = line.rstrip()
        if not raw or raw.lstrip().startswith("#"):
            continue
        m = YAML_KEY_RE.match(raw)
        if not m:
            continue
        indent = len(m.group("indent").expandtabs(2))
        key = m.group("key").strip().strip("'\"")
        if not key or key in ("true", "false", "null"):
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        paths = [k for _i, k in stack] + [key]
        out.append(".".join(paths))
        rest = (m.group("rest") or "").strip()
        if not rest:
            stack.append((indent, key))
        if len(out) >= limit:
            break
    return out


def _csv_columns(src, delim):
    """Header columns of a delimited file — the whole symbol surface a CSV has."""
    import csv as _csv

    try:
        rows = list(_csv.reader(io.StringIO(src), delimiter=delim))[:1]
    except Exception:
        return []
    return [c.strip() for c in (rows[0] if rows else [])
            if COLUMN_RE.match(c.strip())][:200]


def parse_data(src, lang, delim=None):
    """(symbols, doc, key_names) for a json / yaml / csv file. See S31 in TODO.md."""
    if lang == "json":
        try:
            paths = _json_key_paths(json.loads(src))
        except ValueError:
            paths = [m.group(1) for m in JSON_KEY_RE.finditer(src)]
    elif lang == "yaml":
        paths = _yaml_key_paths(src)
    else:
        cols = _csv_columns(src, delim or ("\t" if lang == "tsv" else ","))
        syms = []
        for name in cols:
            for w in _title_words(name):
                if w not in syms:
                    syms.append(w)
        doc = "columns: " + ", ".join(cols[:12])
        return syms, doc, [c.lower() for c in cols]
    names = list(dict.fromkeys(paths))
    syms = []
    for name in names:
        for w in _title_words(name):
            if w not in syms:
                syms.append(w)
    doc = "keys: " + ", ".join(names[:12])
    return syms, doc, names


def _ext(name):
    """A file's extension, in the only case the language table is keyed by.

    `SCHEMA.SQL` is as real a file as `schema.sql`, and on the case-insensitive
    filesystems most users run on it is the *same* file — an index that compared
    extensions byte for byte left every one of them out. `walk` and `build` have to
    agree here: a path one lets through and the other cannot name is a KeyError.
    """
    return os.path.splitext(name)[1].lower()


def walk(root, max_files=20000):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.endswith((".egg-info",)))
        for name in sorted(filenames):
            if _ext(name) not in LANG_BY_EXT:
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > FILE_CAP:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def parse_python(src):
    syms = []
    imports = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return syms, imports
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            syms.append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    syms.append(sub.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    syms.append(tgt.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append((a.name, 0))
        elif isinstance(node, ast.ImportFrom):
            if node.module or node.names:
                names = [a.name for a in node.names]
                imports.append(((node.module or "") + " " + " ".join(names), node.level or 0))
    doc = ""
    try:
        doc = ast.get_docstring(tree) or ""
    except Exception:
        doc = ""
    return syms, imports, doc


def parse_js(src):
    imports = []
    for m in JS_IMPORT_RE.finditer(src):
        spec = next((g for g in m.groups() if g), None)
        if spec:
            imports.append((spec, 0))
    syms = []
    for m in JS_EXPORT_RE.finditer(src):
        for g in m.groups():
            if g:
                syms.append(g)
    return syms, imports


def resolve_python(rel_path, module, level):
    if level:
        base = os.path.dirname(rel_path)
        for _ in range(level - 1):
            base = os.path.dirname(base)
        parts = module.split(".") if module else []
        cand = [os.path.normpath(os.path.join(base, *(parts + ["__init__.py"])))]
        cand.append(os.path.normpath(os.path.join(base, *(parts + [".py"]))))
        for i in range(len(parts)):
            cand.append(os.path.normpath(os.path.join(base, *(parts[:i] + [parts[i] + ".py"]))))
        return cand
    return ["site:" + module]


def resolve_js(rel_path, spec):
    if not spec.startswith("."):
        return ["pkg:" + spec.split("/")[0]]
    base = os.path.normpath(os.path.join(os.path.dirname(rel_path), spec))
    out = []
    for leaf in ("", ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
                 "/index.js", "/index.ts", "/index.tsx", "/__init__.py"):
        out.append(base + leaf)
    return out


def build(root):
    root = os.path.abspath(root)
    paths = walk(root)
    index = set(_rel(root, p) for p in paths)
    nodes = {}
    edges = []
    for p in paths:
        rel = _rel(root, p)
        lang = LANG_BY_EXT[_ext(p)]
        src = _read(p)
        lines = src.count("\n") + 1
        if lang == "python":
            parsed = parse_python(src)
            syms, imports = parsed[0], parsed[1]
            doc = parsed[2] if len(parsed) > 2 else ""
        elif lang in ("markdown", "text", "sql"):
            # No imports to follow: a doc's whole value is which words it is about.
            syms, doc = parse_doc(src, lang)
            imports = []
        elif lang in DATA_LANGS:
            # No imports either, and the *values* stay out of the hints: a fixture of
            # error strings would otherwise rank every unrelated "error" query.
            syms, doc, keys = parse_data(src, lang)
            imports = []
        else:
            syms, imports = parse_js(src)
            doc = " ".join(m.group(1) for m in list(COMMENT_RE.finditer(src))[:4])
        words = ([k.lower() for k in keys] if lang in DATA_LANGS else
                 [x.lower() for x in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", src)])
        hints = sorted(set(words + [os.path.basename(rel).lower()]
                           + [s.lower() for s in syms]))[:400]
        nodes[rel] = {
            "lang": lang,
            "lines": lines,
            "symbols": syms[:200],
            "doc": doc[:400],
            "hints": hints,
            "mtime": os.path.getmtime(p),
        }
        for spec, level in imports:
            for cand in (resolve_python(rel, spec.split(" ")[0], level) if lang == "python"
                         else resolve_js(rel, spec)):
                if cand in index:
                    edges.append((rel, cand))
                elif cand.startswith(("site:", "pkg:")):
                    edges.append((rel, cand))
    deps = {}
    dependents = {}
    for a, b in edges:
        deps.setdefault(a, []).append(b)
        dependents.setdefault(b, []).append(a)
    return {
        "root": root,
        "built_at": time.time(),
        "file_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "deps": deps,
        "dependents": dependents,
    }


def index_path(root):
    return os.path.join(os.path.abspath(root), ".subproto-graph.json")


def index_for(path):
    """The index file a `--graph` value points at. Every surface that takes the flag
    reads it as "the repo", so a directory means that repo's cached index."""
    if os.path.isdir(path):
        return index_path(path)
    return path


def ambient(root=None):
    """The index this directory left for itself, or None.

    Asked for by the command layer rather than sniffed by the engine: an engine that
    reads the working directory on its own answers differently depending on where it
    was called from, and that is ambient state a test room cannot see coming. `subproto
    graph <repo> --install` used to redden the suite for exactly that reason.
    """
    path = index_path(root or os.getcwd())
    return path if os.path.exists(path) else None


def save(graph, path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(graph, f, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def load(path):
    with open(index_for(path)) as f:
        return json.load(f)


# A one-entry cache, deliberately *outside* the graph payload: the view holds frozensets,
# and anything that json.dumps a decision or the index itself cannot carry them.
_VIEW = {"nodes": None, "built_at": None, "view": {}}


def _scoring_view(graph):
    """{rel: (lower_path, lower_symbol_set, hint_set)} for this index.

    S32: `score_files` used to rebuild two sets per node on *every* call, which for a
    20k-file index meant 40k allocations per decision — a cost only the compiled arm
    pays, because the per-slot path never consults the graph. The view is keyed to the
    node table it was derived from by identity plus build time, so a rebuilt or reloaded
    index (either of which constructs a fresh dict) cannot reuse a stale one; only
    mutating a live index in place can, and nothing does that.
    """
    nodes = graph.get("nodes") or {}
    if nodes is _VIEW["nodes"] and graph.get("built_at") == _VIEW["built_at"]:
        return _VIEW["view"]
    view = {}
    for rel, node in nodes.items():
        view[rel] = (rel.lower(),
                     frozenset(s.lower() for s in (node.get("symbols") or [])),
                     frozenset(node.get("hints") or []))
    _VIEW["nodes"] = nodes
    _VIEW["built_at"] = graph.get("built_at")
    _VIEW["view"] = view
    return view


def score_files(graph, query_terms, file_mentions, exclude=()):
    """Lexical relevance per file. Returns [(rel, score, why)]."""
    q = set(t.lower() for t in query_terms)
    mention_set = set(m.lower() for m in file_mentions)
    nodes = graph.get("nodes") or {}
    deps = graph.get("deps") or {}
    dependents = graph.get("dependents") or {}
    view = _scoring_view(graph)
    out = []
    for rel, node in nodes.items():
        if rel in exclude:
            continue
        low, syms, hints = view[rel]
        why = []
        score = 0.0
        # The same three tiers a term can earn — symbol beats path beats hint — taken as
        # set intersections instead of a per-term membership loop.
        sym_hits = q & syms
        score += 3.0 * len(sym_hits)
        why += ["sym:" + t for t in sym_hits]
        rest = q - sym_hits
        path_hits = set(t for t in rest if t in low)
        score += 2.0 * len(path_hits)
        why += ["path:" + t for t in path_hits]
        score += 0.6 * len((rest - path_hits) & hints)
        if not q and not mention_set:
            continue
        for m in mention_set:
            if m in low or low.endswith(m):
                score += 8.0
                why.append("mentioned")
        for dep in deps.get(rel, []):
            if dep in mention_set or any(dep.endswith(m) for m in mention_set):
                score += 2.0
                why.append("imports-mentioned")
        for back in dependents.get(rel, []):
            if back in mention_set or any(back.endswith(m) for m in mention_set):
                score += 1.2
                why.append("imported-by-mentioned")
        if score > 0:
            out.append((rel, round(score, 2), sorted(set(why))[:6]))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out


def search(graph, query, top_k=12):
    from . import protocol

    terms = protocol.lexical_tokens(query, limit=60)
    mentions = protocol.extract_paths(query)
    scored = score_files(graph, terms, mentions)
    return scored[:top_k]
