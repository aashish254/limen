"""``subproto doctor`` — why the thing is not working on *this* machine.

A stranger hits four walls and cannot tell which: a Python too old, a port
already taken, a data directory they cannot write, a backend configured but
dead. Each check measures the real thing (binds the socket, writes the file,
probes ``GET /health``) and prints one state word plus one remediation line.
A backend with no endpoint is "not configured", never broken.

Entry points — the module is callable and testable without the CLI:

* ``check_all(config, port=None)`` -> list of rows. A row is a dict of
  ``section, label, state, value, note, fix``; ``state`` is a word from
  style.py's vocabulary (None for a plain line), ``note`` and ``fix`` optional.
* ``render(rows, color=False)`` -> the page as one string.
* ``run(args=None)`` -> exit code, printing the page. ``args`` is None (parse
  ``sys.argv[1:]``), an argv list, or an argparse Namespace — the cli hook passes its
  parsed ``--port/--home/--config/--color`` straight through.
  Exits 1 only when a check is red (a Python too old, an unwritable dir, a taken port,
  a configured backend that will not answer). ``--json`` prints the same rows as
  ``{"ok": ..., "checks": [...]}`` for a bug report.
"""

import argparse
import json
import os
import re
import socket
import sys

from . import laya_server, style
from .config import Config
from .systemone import registry

FLOOR = (3, 9)
PROBE_NAME = ".subproto-doctor-witness"
KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")
ENV_VARS = ("LAYA_URL", "OPENJEV_URL", "DJEV_URL", "SEMIF_URL", "SUBPROTO_MLX_URL")
# The page's two red words: everything else a row can say is news, not failure.
RED = (style.DOWN, style.MISSING)


def _row(section, label, state, value, fix=None, note=None):
    return {"section": section, "label": label, "state": state,
            "value": value, "note": note, "fix": fix}


PYPROJECT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "pyproject.toml")


def _py_floor():
    """(the requires-python floor, where this build read it from).

    An installed wheel carries no `pyproject.toml` beside it, so the second value is
    not decoration: it tells the reader whether the floor on the page is *this
    checkout's* metadata or the default the release was built with. Printing "3.9"
    either way, without saying which, is a claim about a file that may not exist.
    """
    try:
        with open(PYPROJECT, encoding="utf-8", errors="replace") as handle:
            match = re.search(r'requires-python\s*=\s*"[^"]*?(\d+)\.(\d+)',
                              handle.read())
    except OSError:
        return FLOOR, "the shipped default"
    if not match:
        return FLOOR, "the shipped default"
    return (int(match.group(1)), int(match.group(2))), os.path.basename(PYPROJECT)


def _python_row():
    floor, seen_at = _py_floor()
    text = "%d.%d.%d" % sys.version_info[:3]
    if tuple(sys.version_info[:2]) < floor:
        return _row("environment", "python", style.MISSING, text,
                    fix="subproto requires Python >= %d.%d (%s) — install a newer "
                        "interpreter and rerun this with it" % (floor[0], floor[1], seen_at))
    return _row("environment", "python", style.OK, text,
                note=">= %d.%d, read from %s — this interpreter: %s"
                     % (floor[0], floor[1], seen_at, sys.executable))


def _dir_row(config):
    path = config.data_dir
    probe = os.path.join(path, PROBE_NAME)
    try:
        # The witness is the write, not the stat: creating the dir is allowed.
        os.makedirs(path, exist_ok=True)
        with open(probe, "w") as handle:
            handle.write("witness")
        os.remove(probe)
    except OSError:
        return _row("environment", "data dir", style.MISSING, path,
                    fix="make that directory writable, or point SUBPROTO_HOME "
                        "at one you own")
    return _row("environment", "data dir", style.OK, path,
                note="a file was written here and removed — telemetry.db "
                     "lands in this directory")


def _bind(port, host="127.0.0.1"):
    """(is it free, the port the kernel answered with), asked of the kernel.

    Port 0 is a real question here, not a placeholder: it means "find any free
    port", and the row then reports the one it was given.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return True, sock.getsockname()[1]
    except OSError:
        return False, port
    finally:
        sock.close()


def port_free(port, host="127.0.0.1"):
    """True when this exact bind succeeds — asked of the kernel, not of netstat."""
    return _bind(port, host)[0]


def _port_row(port):
    free, chosen = _bind(port)
    if free:
        return _row("environment", "port", style.OK, "%d  127.0.0.1" % chosen,
                    note="you asked for any free port, and that is the one it took"
                    if not port else None)
    return _row("environment", "port", style.DOWN, "%d  127.0.0.1" % port,
                fix="something already holds 127.0.0.1:%d — stop it, or start "
                    "elsewhere: subproto up --port %d" % (port, port + 1))


def _keys_row():
    present = [name for name in KEY_VARS if os.environ.get(name)]
    if present:
        return _row("environment", "provider keys", style.PRESENT,
                    ", ".join(present),
                    note="names only — a key's value, prefix or length never "
                         "reaches this page")
    return _row("environment", "provider keys", style.WARN,
                "none in this environment",
                fix="export your provider's key (ANTHROPIC_API_KEY, "
                    "OPENAI_API_KEY or GEMINI_API_KEY) before subproto up — "
                    "the proxy forwards it and never stores it")


def _laya_row():
    if laya_server.laya_importable():
        return _row("model backends", "laya runtime", style.INSTALLED,
                    "importable in this interpreter")
    return _row("model backends", "laya runtime", style.NOT_READY,
                "not importable here (python %d.%d)" % sys.version_info[:2],
                fix='the real checkpoint needs pip install "laya[serve]" on a '
                    "Python >= 3.10 environment — subproto's lexical server "
                    "runs anywhere and needs nothing")


def _probed_rows(config):
    """One row per configured endpoint — probes deduplicated by URL, so one
    shared server answers ``GET /health`` once however many names point at it."""
    adapters = dict(registry.configured_adapters(config))
    chosen, label = registry.resolve(config)
    if chosen is not None and chosen.base_url not in [
            a.base_url for a in adapters.values()]:
        # A raw URL in SUBPROTO_MODEL is reachable but named by nothing.
        adapters[label or chosen.label] = chosen
    if not adapters:
        return [_row("model backends", "backend", None,
                    "not configured — every slot answers with the "
                    "in-process heuristics")]
    by_url = {}
    for name, adapter in adapters.items():
        entry = by_url.setdefault(adapter.base_url, {"names": [],
                                                     "adapter": adapter})
        entry["names"].append(name)
    rows = []
    for url in sorted(by_url):
        entry = by_url[url]
        names = ", ".join(sorted(entry["names"]))
        health = entry["adapter"].health()
        if health.get("ok"):
            rows.append(_row("model backends", names, style.UP, url))
            continue
        envs = sorted(set(registry.REGISTRY.get(n, {}).get("env")
                          for n in entry["names"]) - set([None]))
        rows.append(_row("model backends", names, style.DOWN, url,
                         fix="start that server, or unset %s — without a "
                             "backend the slots fall back to heuristics and "
                             "the proxy still runs"
                             % ", ".join(envs or ["SUBPROTO_MODEL"])))
    return rows


def check_all(config, port=None):
    """Every diagnostic row, in page order. Health probes use the adapter's
    own ``timeout_for(config)`` budget, so all-dead endpoints still answer
    this page in about a second."""
    rows = [_python_row(), _dir_row(config), _port_row(
        port if port is not None else config.port), _keys_row()]
    rows.extend(_model_rows(config))
    return rows


def _model_rows(config):
    return [_laya_row()] + _probed_rows(config)


def _meta(rows):
    needs = len([r for r in rows if r.get("fix")])
    return "%d checks, %d to fix" % (len(rows), needs) if needs \
        else "%d checks, nothing to fix" % len(rows)


def render(rows, color=False):
    """The page: a header, then each section's grid rows with their notes and
    remediation lines hung in the content column."""
    out = [style.header("doctor", _meta(rows), color=color)]
    section = None
    for r in rows:
        if r["section"] != section:
            section = r["section"]
            out.append("")
            if section:
                out.append(style.section(section, indent=style.INDENT,
                                         color=color))
        value = str(r["value"] or "")
        if r["state"]:
            value = style.state(r["state"], color=color) + \
                ("  " + value if value else "")
        out.append(style.row(r["label"], value, styled=True, color=color))
        for extra in (r["note"], r["fix"]):
            if extra:
                out.append(style.prose(extra, color=color))
    return "\n".join(out)


def as_json(rows):
    """The same rows as a document, with the verdict it stands or falls on."""
    return {"ok": not any(r["state"] in RED for r in rows),
            "checks": [{key: val for key, val in r.items() if val is not None}
                       for r in rows]}


def _parser():
    ap = argparse.ArgumentParser(prog="subproto doctor",
                                 description="diagnose this machine")
    ap.add_argument("--port", type=int, help="the port to test for free space")
    ap.add_argument("--home", help="state dir to test for write access")
    ap.add_argument("--config", help="path to config json")
    ap.add_argument("--json", action="store_true",
                    help="print the rows as one document, for a bug report")
    ap.add_argument("--color", action="store_true", dest="color", default=None)
    ap.add_argument("--no-color", action="store_false", dest="color")
    return ap


def run(args=None):
    """Print the page; 0 when nothing is red, 1 when something is."""
    ns = args if isinstance(args, argparse.Namespace) else _parser().parse_args(args)
    want = getattr(ns, "port", None)
    config = Config.load(getattr(ns, "config", None),
                         data_dir=getattr(ns, "home", None),
                         port=want)
    # `--port 0` is a question ("find one"), so it must not fall through to the default.
    rows = check_all(config, port=want if want is not None else config.port)
    if getattr(ns, "json", False):
        print(json.dumps(as_json(rows), indent=1, sort_keys=True))
    else:
        print(render(rows, color=style.enabled(
            sys.stdout, force=getattr(ns, "color", None))))
    return 1 if any(r["state"] in RED for r in rows) else 0
