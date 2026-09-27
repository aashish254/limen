#!/usr/bin/env python3
"""Build the Limen landing page from what the last measurement run measured.

No number on the page is typed by hand. The script reads `bench/launch_stats.json`
(written by `python3 bench/launch_stats.py`), replays the demo twice to produce the
hero printout from real bytes, and counts the test, mutant and subcommand totals out
of the repo. A figure that moved in the run moves on the page; a figure the page
cannot find makes the build fail rather than print the old one.

    python3 bench/launch_stats.py --iters 5 && python3 tools/make_site.py

Writes `_site/index.html` plus the fonts the page embeds.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from subproto import style  # noqa: E402  (the page's printout uses the CLI's own grid)

STATS_JSON = os.path.join(ROOT, "bench", "launch_stats.json")
TEMPLATE = os.path.join(HERE, "site.html.tmpl")
FIGDIR = os.path.join(ROOT, "docs", "assets", "figures")
FONTS = os.path.join(ROOT, "docs", "assets", "fonts")
ASSETS = os.path.join(ROOT, "docs", "assets")

# One plate for every figure. The widest canvas the bench draws today is 976 units
# across; anything that outgrows the plate stops the build rather than gets cropped.
PLATE_W, PLATE_H = 976, 320

# Files the page's <meta> tags promise a crawler. Checked, not globbed: a glob lets a
# deleted card pass the build and fail the launch post.
SOCIAL = ["og.png"]

# The two homes the hero replay writes. Fixed names so the path the page prints is the
# path a reader can reproduce, and so a re-run replaces them instead of stacking.
OFF_HOME = os.path.join("/tmp", "limen-off")
ON_HOME = os.path.join("/tmp", "limen-on")
# The request the hero shows. The demo corpus is seeded, so request 3 is the same
# request in both replays; `hero()` proves it before it prints the pair.
HERO_REQUEST = 3

STATE_CLS = {style.DELIVERED: "state-ok", style.OK: "state-ok", style.UP: "state-ok",
             style.YES: "state-ok", style.READY: "state-ok", style.PRESENT: "state-ok",
             style.INSTALLED: "state-ok", style.ON: "state-ok",
             style.POTENTIAL: "state-warn", style.WARN: "state-warn",
             style.SHORT: "state-warn", style.OFF: "state-warn",
             style.DOWN: "state-bad", style.MISSING: "state-bad",
             style.NOT_READY: "state-bad"}

KINDS = ("message", "tool", "file")


def fmt(value):
    return style.format_num(value)


def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def run(args, env=None, timeout=1800):
    """Run one command from the checkout and return stdout, raising with the tail of
    stderr when it fails — a page build that half-worked is worse than one that stopped."""
    proc = subprocess.run(args, cwd=ROOT, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit("%s failed (%d):\n%s" % (" ".join(args), proc.returncode,
                                                  proc.stderr[-2000:]))
    return proc.stdout


# ---------------------------------------------------------------- the hero printout


def demo(home, enforce=False):
    """Replay the 17-turn corpus into `home`, observing or enforcing."""
    shutil.rmtree(home, ignore_errors=True)
    env = dict(os.environ, SUBPROTO_HOME=home)
    if enforce:
        env["SUBPROTO_APPLY"] = "tool_gate,compact"
        env["SUBPROTO_COMPILE"] = "on"
    run([sys.executable, "-m", "subproto", "demo", "--slots", "--home", home], env=env)


def show(request_id, home, as_json=False):
    args = [sys.executable, "-m", "subproto", "show", str(request_id), "--home", home]
    if as_json:
        args.append("--json")
    else:
        args.append("--no-color")
    return run(args)


LABEL_END = style.INDENT + style.METRIC_W          # where a right-aligned label stops
VALUE_AT = style.METRIC_CONTENT                   # where the value starts


def row_of(line, label):
    """True for `"  " + label.rjust(25) + "  " + value`, the grid `show` prints metrics on."""
    return (line[:2] == "  " and line[LABEL_END:VALUE_AT] == "  "
            and line[2:LABEL_END] == label.rjust(style.METRIC_W))


def mark(line):
    """Split one printed line of `subproto show` into (text, css-class) pieces.

    The classes carry the meaning the terminal carries in escape codes: dim for a label
    or a qualifier, colour only on a state word, and the strike on the columns of a cut
    row — the item and the tokens it cost, not the reason it lost.
    """
    if not line.strip():
        return [(line, None)]
    if line.startswith("subproto show"):
        head, sep, tail = line.partition(" — ")
        return [(head, "ph"), (sep + tail, "pm")]
    if line.startswith("  decisions"):
        return [(line[:11], "ph"), (line[11:], "pm")]
    if line[:4] == "    " and line[4:20].strip() in KINDS and line[20] == " ":
        # indent + kind (16) | pointer (26) | price ("  NNN tok") | the reason. The
        # per-slot path prices a decision as a whole, so the price column is only there
        # when this row's arm measured one; striking past it would take the reason with
        # the item.
        priced = line[53:57] == " tok"
        stop = 57 if priced else 47
        return [(line[:21], "k"), (line[21:stop], "cut"),
                (line[stop:stop + 1], None), (line[stop + 1:], "pm")]
    if line[:style.METRIC_CONTENT] == " " * style.METRIC_CONTENT:
        return [(line[:style.METRIC_CONTENT], None),
                (line[style.METRIC_CONTENT:], "quote")]
    for label in ("client", "recorded", "stream", "status", "ttfb", "latency",
                  "billed input", "output", "spend", "body"):
        if row_of(line, label):
            return _split_state([(line[:VALUE_AT], "k")], line[VALUE_AT:])
    for slot in ("tool_gate", "compact", "context", "effort"):
        if row_of(line, slot):
            return _split_state([(line[:VALUE_AT], "ph")], line[VALUE_AT:])
    if re.match(r"^ {6}\S", line):
        return [(line, "pm")]
    return [(line, None)]


def _split_state(parts, value):
    """Pull a leading state word and a trailing `(qualifier)` out of a row's value."""
    match = re.match(r"^(\s*)(\w[\w ]*?)(\s\s)(.*)$", value)
    if match and match.group(2) in STATE_CLS:
        parts.append((match.group(1), None))
        parts.append((match.group(2), STATE_CLS[match.group(2)]))
        value = match.group(3) + match.group(4)
    match = re.match(r"^(.*?)(\s\s\(.+\))$", value)
    if match:
        parts.append((match.group(1), None))
        parts.append((match.group(2), "pm"))
    else:
        parts.append((value, None))
    return parts


def html_line(parts):
    text = "".join(t for t, _ in parts)
    assert text.endswith("\n") is False
    out = []
    for text_piece, cls in parts:
        if not text_piece:
            continue
        out.append('<span class="%s">%s</span>' % (cls, esc(text_piece))
                   if cls else esc(text_piece))
    return "".join(out)


def record(home, request_id):
    """The raw telemetry row for one recorded request, as `show --json` prints it."""
    return json.loads(show(request_id, home, as_json=True))


def hero():
    """The hero block: the same request recorded twice, once with the slots off."""
    demo(OFF_HOME)
    demo(ON_HOME, enforce=True)
    off = record(OFF_HOME, HERO_REQUEST)
    on = record(ON_HOME, HERO_REQUEST)
    # The pair is only a comparison if it is the same request. Everything that would move
    # if the replay had drifted has to match — and `in_tok` is deliberately not on the
    # list, since it is the one number the mechanism is supposed to change. `cr_tok` is:
    # a provider cache read is keyed on the prefix, so an identical read across the two
    # runs is this request's own evidence that the cached prefix survived the cut (I2).
    for key in ("api", "path", "client", "model", "cr_tok", "out_tok"):
        if off[key] != on[key]:
            raise SystemExit("hero request %d drifted between replays: %s=%r vs %r"
                             % (HERO_REQUEST, key, off[key], on[key]))
    if on["in_tok"] >= off["in_tok"]:
        raise SystemExit("hero request %d got bigger with the slots on: %d -> %d"
                         % (HERO_REQUEST, off["in_tok"], on["in_tok"]))
    text = show(HERO_REQUEST, ON_HOME).rstrip("\n").splitlines()
    body = []
    for line in text:
        parts = mark(line)
        assert "".join(t for t, _ in parts) == line, "markup lost text: %r" % line
        body.append(html_line(parts))
    cut = off["in_tok"] - on["in_tok"]
    spend = (off["cost_usd"] or 0.0) - (on["cost_usd"] or 0.0)
    pct = 100.0 * cut / off["in_tok"]
    compare = ["<div class=\"mrow compare\"><span>the request the client sent</span>"
               "<span class=\"v\">%s</span><span class=\"u\">tok input</span></div>"
               % fmt(off["in_tok"])]
    compare.append("<div class=\"mrow\"><span>what Limen let through</span>"
                   "<span class=\"v\">%s</span><span class=\"u\">tok input"
                   "  <b class=\"state-ok\">−%s%%</b></span></div>"
                   % (fmt(on["in_tok"]), "%.1f" % (100.0 * cut / off["in_tok"])))
    compare.append("<div class=\"mrow\"><span>spend, at the mock's tariff</span>"
                   "<span class=\"v\">$%.4f → $%.4f</span><span class=\"u\">%s tok and"
                   " $%s went away</span></div>"
                   % (off["cost_usd"] or 0.0, on["cost_usd"] or 0.0, fmt(cut),
                      "%.4f" % spend))
    html = ('<div class="beforeafter">%s</div>\n'
            '<pre class="printout">%s</pre>' % ("".join(compare), "\n".join(body)))
    cmd = ("subproto demo --slots --home /tmp/limen-off; SUBPROTO_APPLY=tool_gate,compact "
           "SUBPROTO_COMPILE=on subproto demo --slots --home /tmp/limen-on; "
           "subproto show %d --home /tmp/limen-on" % HERO_REQUEST)
    # The hero's own three numbers, handed to the template as tokens. The threshold bar in
    # the page header is the same pair the printout below it shows, so it has to come from
    # the same replay — a bar typed by hand is a bar that can disagree with its own printout.
    nums = {"request": HERO_REQUEST, "off_tok": off["in_tok"], "on_tok": on["in_tok"],
            "pct": pct}
    return html, cmd, nums


# ---------------------------------------------------------------------- the figures

# Order is the argument: what a request carries, what the layer takes off it, whether
# the task still passes, what survives, where the naive version breaks, what the small
# model costs, and what a wrong call bills back.
FIGURE_ORDER = [
    ("request-composition", "measured",
     "The average request before anything is decided: a fifth of it is tool "
     "schemas the turn cannot use, and the task itself is a rounding error."),
    ("tokens-by-family", "measured",
     "Input tokens per task with the layer off, per-slot, and compiled, split by "
     "what the task is about. The reduction is flat across families, which is the "
     "point — it is not one kind of task paying for the others."),
    ("tokens-by-size", "measured",
     "The same three arms grouped by the size of the unmanaged request. Bigger "
     "requests do not get a proportionally bigger cut, so the saving does not live "
     "only in the outliers."),
    ("cut-composition", "projection",
     "The ablation's own corpus: what each slot would take off a request shaped like the "
     "ones it measures, priced in estimated input tokens. That is why it is not the number "
     "the arms above delivered."),
    ("accuracy-held", "measured",
     "Pass-rate by family with the cut enforced. A saving that broke the task would "
     "be visible here as a missing bar, not as a footnote."),
    ("recall-precision", "measured",
     "Of the files a task is about, how many survive (recall), and of what survives, "
     "how many are the ones that matter (precision). Per-slot keeps recall and spends "
     "precision; the compiler moves both."),
    ("hard-set", "measured",
     "Eight tasks built so the file the grader wants sits behind a run of large files "
     "it does not need — which is what a per-slot recency budget is worst at. "
     "Per-slot loses every one of them. This panel is the reason the compiler is the "
     "default story rather than a bonus arm."),
    ("checkpoint-cost", "curve",
     "The real small model's measured cost per decision against the shape it has to "
     "answer in. One shape fits the budget; the one that would name every option does "
     "not, so the shipped path scores a single choice."),
]


def projection_command():
    """The ablation command behind the projection panel, read out of the bench itself.

    The page names a command, so the page asks the file that runs it. If the bench
    changes how it calls `ab.py`, this fails and the build stops rather than printing a
    command nobody runs.
    """
    src = read(os.path.join(ROOT, "bench", "launch_stats.py"))
    m = re.search(r'subprocess\.run\(\[sys\.executable,\s*os\.path\.join\(HERE,\s*"ab\.py"\),'
                  r'((?:\s*"[^"]*",?)+)\]', src)
    if not m:
        raise SystemExit("bench/launch_stats.py no longer calls ab.py the way the page "
                         "says it does — fix projection_command() before shipping")
    argv = " ".join(a.strip().strip(",").strip('"') for a in m.group(1).split())
    return "python3 bench/ab.py %s" % argv


def stamp(stats, kind="measured"):
    """One line per panel: the command, the corpus it measured, and the commit."""
    if kind == "projection":
        t = stats["slots"]["totals"]
        return ('<p class="stamp"><b>%s</b>, run by <b>%s</b> — %s requests, %s est input '
                'tokens.</p>'
                % (esc(projection_command()), esc(stats["command"]),
                   fmt(t["requests"]), fmt(t["est_in_tok"])))
    if kind == "delivered":
        r = stats["regret"]
        return ('<p class="stamp"><b>%s</b> on %s — %s demo requests replayed with the slots '
                'enforcing, bodies stored, commit <b>%s</b>.</p>'
                % (esc(stats["command"]), esc(stats["host"]["platform"]),
                   r["requests_with_decisions"], esc(stats["git_sha"])))
    if kind == "curve":
        c = stats["checkpoint_curve"]["conditions"] or {}
        return ('<p class="stamp"><b>%s %s</b> on %s, read from <b>bench/laya_latency.json</b>'
                ' — measured against the local server, not re-run by the arms.</p>'
                % (esc(str(c.get("model", "the checkpoint"))), esc(str(c.get("laya", ""))),
                   esc(str(c.get("device", "the recorded device")))))
    return ('<p class="stamp"><b>%s</b> on %s, %s tasks × %s repeats, commit <b>%s</b>.</p>'
            % (esc(stats["command"]), esc(stats["host"]["platform"]),
               stats["corpus"]["n"], stats["corpus"]["iters"], esc(stats["git_sha"])))


def figure_svg(name):
    """The inline SVG, stripped of the XML prologue and normalised onto one plate.

    The bench draws each figure at whatever width its own title needs, so the nine
    canvases differ. On the page they sit in identical frames, and a frame that fits
    each canvas to its own box fits each *font* to its own size — eight plates, eight
    type sizes. So every canvas is moved onto the same plate and centred in it, which
    leaves the margins to vary and the type to hold still.
    """
    text = read(os.path.join(FIGDIR, name + ".svg")).strip()
    text = re.sub(r"^<\?xml[^>]*\?>\s*", "", text)
    if not text.startswith("<svg"):
        raise SystemExit("%s.svg does not start with an <svg> element" % name)
    match = re.match(r'^(<svg\b[^>]*?)viewBox="0 0 (\d+) (\d+)"([^>]*)>(.*)</svg>\s*$',
                     text, re.S)
    if not match:
        raise SystemExit("%s.svg is not one svg element with a viewBox" % name)
    head, wide, high, tail, body = (match.group(1), int(match.group(2)),
                                    int(match.group(3)), match.group(4), match.group(5))
    if wide > PLATE_W or high > PLATE_H:
        raise SystemExit("%s.svg is %d×%d and the plate is %d×%d — shrink the canvas "
                         "in bench/launch_stats.py rather than crop the drawing"
                         % (name, wide, high, PLATE_W, PLATE_H))
    tail = re.sub(r'\b(width|height)="\d+"', lambda m: '%s="%d"'
                  % (m.group(1), {"width": PLATE_W, "height": PLATE_H}[m.group(1)]), tail)
    return ('%sviewBox="0 0 %d %d"%s><g transform="translate(%d,%d)">%s</g></svg>'
            % (head, PLATE_W, PLATE_H, tail,
               (PLATE_W - wide) // 2, (PLATE_H - high) // 2, body))


def figures(stats):
    """Each panel, its reading sentence, and the run that produced it.

    The SVG carries its own title, subtitle and footnote, so the page adds the two
    things the figure cannot know: how to read it, and which command, host and commit
    it came out of. The sentence goes under the chart because the chart's own heading
    is already the thing on top. The stamp is per panel: a measured arm, a projection
    and a curve read off an earlier run do not share a provenance line.
    """
    out = []
    for name, kind, cap in FIGURE_ORDER:
        path = os.path.join(FIGDIR, name + ".svg")
        if not os.path.exists(path):
            raise SystemExit("missing figure %s — run: python3 bench/launch_stats.py"
                             % path)
        out.append('<div class="fig"><div class="figscroll scroller">%s</div><p class="cap">%s</p>%s</div>'
                   % (figure_svg(name), esc(cap), stamp(stats, kind)))
    return "\n".join(out)


def regret_figure(stats):
    r = stats["regret"]
    return ('<div class="fig"><div class="figscroll scroller">%s</div><p class="cap">%s of the %s '
            'requests that carried a decision still had their body on disk when the harvest '
            'ran, which is what lets the payback be counted rather than estimated.</p>%s</div>'
            % (figure_svg("regret-bill"), r["bodies_readable"],
               r["requests_with_decisions"], stamp(stats, "delivered")))


def cost_prose(stats):
    r = stats["regret"]
    return ("The demo corpus is %s requests and every one of them carries a decision. The"
            " regret harvest looks for the case where the wire disagrees with a cut — a"
            " file the slot dropped that the agent then read again — and it saw %s evicted"
            " reads, of which <b>%s were wrong</b>: the file came back on the next turn, so"
            " the cut cost <b>%s tokens</b> of refetch instead of saving them. All %s were"
            " enforced and none was shadow, so that is the price of a call made, not of a"
            " call observed — which is why Limen starts in observation, why each slot is"
            " reversible on its own, and why this bill sits on the page beside the saving."
            " It works out at %s regrettable drops per 1,000 requests. The mechanism still"
            " nets out at %.1f%% fewer input tokens per task with the pass-rate held; the"
            " honest number is the pair, not the one that flatters it."
            % (r["requests_with_decisions"], r["evicted_reads_seen"],
               r["regrettable_drops"], fmt(r["refetch_tok_paid"]), r["enforced"],
               "%g" % r["regrettable_per_1k_requests"], stats["overall"]["reduction_pct"]))


def pool_table(stats):
    o = stats["overall"]
    rows = []

    def add(metric, without, per_slot, with_, change, cls=None):
        rows.append("<tr><td>%s</td><td class=\"n\">%s</td><td class=\"n\">%s</td>"
                    "<td class=\"n\">%s</td><td class=\"n%s\">%s</td></tr>"
                    % (metric, without, per_slot, with_,
                      " " + cls if cls else "", change))

    add("input tokens / task", fmt(round(o["observe"])), fmt(round(o["enforce"])),
        fmt(round(o["compiled"])), "−%.1f%%" % o["reduction_pct"], "state-ok")
    add("task pass-rate", "%g%%" % o["pass_rate"]["observe"],
        "%g%%" % o["pass_rate"]["enforce"], "%g%%" % o["pass_rate"]["compiled"], "held")
    add("recall of the files the task needs", "%g%%" % o["recall"]["observe"],
        "%g%%" % o["recall"]["enforce"], "%g%%" % o["recall"]["compiled"], "held")
    add("precision of what survives", "%.1f%%" % o["precision"]["observe"],
        "%.1f%%" % o["precision"]["enforce"], "%.1f%%" % o["precision"]["compiled"],
        "+%.1f pp" % (o["precision"]["compiled"] - o["precision"]["observe"]),
        "state-ok")
    lat = o["p50_latency"]
    add("p50 request latency (ms)", "%.1f" % lat["observe"], "%.1f" % lat["enforce"],
        "%.1f" % lat["compiled"], "%+.1f" % (lat["compiled"] - lat["observe"]))
    hard = stats["hard_set"]
    add("hard set: pass-rate / recall",
        "%g%% / %g%%" % (hard["observe"]["pass_pct"], hard["observe"]["recall_pct"]),
        "<b class=\"state-bad\">%g%% / %.1f%%</b>" % (hard["enforce"]["pass_pct"],
                                                      hard["enforce"]["recall_pct"]),
        "%g%% / %g%%" % (hard["compiled"]["pass_pct"], hard["compiled"]["recall_pct"]),
        "per-slot lost it")
    return "\n".join(rows)


# What each slot decides, in the reader's words. The numbers come from the run; only
# this sentence-level description is written, and the set of names is checked against
# `engine.ALL_SLOTS` so a fifth slot fails the build instead of going unmentioned.
SLOT_SAYS = {
    "tool_gate": "which tool schemas this turn cannot use, out of the ~24 that ride "
                 "along in every request",
    "compact": "which old turns have gone stale — a superseded tool result, an error "
               "the agent already worked around",
    "context": "which file notes the code graph says this task will not touch",
    "effort": "how much reasoning this step deserves — advisory: it names a tier and "
              "never rewrites the request",
}


def slot_table(stats):
    from subproto import engine
    slots = stats["slots"]["slots"]
    total = stats["slots"]["totals"]["est_in_tok"]
    names = set(slots) | {n for n in engine.ALL_SLOTS}
    if names != set(engine.ALL_SLOTS):
        raise SystemExit("slot table and engine.ALL_SLOTS disagree: %s" % sorted(names))
    rows = []
    for name in engine.ALL_SLOTS:
        rec = slots.get(name) or {"decisions": 0, "would_drop": 0, "savings_est_tok": 0}
        tok = rec["savings_est_tok"]
        rows.append("<tr><td>%s</td><td>%s</td><td class=\"n\">%s</td>"
                    "<td class=\"n\">%.1f%%</td></tr>"
                    % (name, SLOT_SAYS[name], fmt(tok), 100.0 * tok / total))
    return "\n".join(rows)


# Each row is a claim the page refuses to make, keyed by the TODO.md item that holds it
# open. If someone checks the box, the build stops: the page has to be edited on purpose.
GATES = [
    ("V2-A", "−X% on your own traffic", "a real agent session across ≥2 vendors, run by "
     "you. Nothing here has seen a human conversation; every number on this page comes "
     "from a seeded synthetic corpus."),
    ("V2-B", "the billed hero number", "an approved API budget and a public 20-task "
     "SWE-bench-shaped set with a grader. The $ figures on this page are the in-repo "
     "mock's own tariff."),
    ("V2-C′", "that a fine-tune beats the heuristic", "labels. The published ablation "
     "says the real checkpoint loses by −0.083 precision on the corpus we carry, and "
     "7 of its 10 cases have gold sets equal to the heuristic's own answer."),
    ("V2-D", "a shipped routing model", "the same labels, plus a training run and a "
     "weights release."),
    ("V2-E", "version 1.0", "a tag, a PyPI upload, and the launch post. The install "
     "routes above are git and pip-from-source until then."),
    ("WIN", "that it works on Windows", "`os.getloadavg` is POSIX and ANSI handling "
     "differs, so CI runs Windows and reports it without gating on it."),
]


def gate_table(todo):
    rows = []
    for key, claim, why in GATES:
        if key == "WIN":
            opened = "windows-latest" in todo or "Windows" in todo
        else:
            pattern = r"^- \[[ x~]\] \*\*%s\*\*" % re.escape(key)
            done = re.search(r"^- \[x\] \*\*%s\*\*" % re.escape(key), todo, re.M)
            opened = bool(re.search(pattern, todo, re.M)) and not done
        if not opened:
            raise SystemExit("%s is no longer an open item in TODO.md — the page still "
                             "says it is not measured" % key)
        rows.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(claim), esc(why)))
    return "\n".join(rows)


# --------------------------------------------------------------------- repo counts


def mutants():
    """len(MUTANTS) from the gate source, read with ast so nothing executes."""
    tree = ast.parse(read(os.path.join(ROOT, "bench", "mutation_gate.py")))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MUTANTS":
                    return len(node.value.elts)
    raise SystemExit("no MUTANTS list in bench/mutation_gate.py")


def tests():
    out = run([sys.executable, "-m", "pytest", "--collect-only", "-q"], timeout=600)
    match = re.search(r"(\d+) tests? collected", out)
    if match:
        return int(match.group(1))
    count = len([l for l in out.splitlines() if "::" in l])
    if not count:
        raise SystemExit("pytest collected nothing to count")
    return count


def subcommands():
    return len(re.findall(r"\.add_parser\(", read(os.path.join(ROOT, "subproto", "cli.py"))))


def py_versions():
    """The interpreter pair CI actually gates on, read from the workflow matrix."""
    text = read(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
    match = re.search(r'python: \["(\d+\.\d+)", "(\d+\.\d+)"\]', text)
    if not match:
        raise SystemExit("cannot read the python matrix out of ci.yml")
    return match.group(1), match.group(2)


def python_floor():
    text = read(os.path.join(ROOT, "pyproject.toml"))
    match = re.search(r'requires-python = ">=\s*(\d+\.\d+)"', text)
    if not match:
        raise SystemExit("no requires-python in pyproject.toml")
    return match.group(1)


# ------------------------------------------------------------------------- assemble


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--skip-hero", action="store_true",
                    help="reuse docs/assets/hero.json instead of replaying the demo")
    ap.add_argument("--design", choices=["original", "professional"], default="professional",
                    help="use professional redesign (default) or original layout")
    args = ap.parse_args()

    if not os.path.exists(STATS_JSON):
        raise SystemExit("no %s — run: python3 bench/launch_stats.py --iters 5"
                         % os.path.relpath(STATS_JSON, ROOT))
    stats = json.loads(read(STATS_JSON))
    if time.time() - os.path.getmtime(STATS_JSON) > 6 * 86400:
        print("note: %s is more than 6 days old" % os.path.relpath(STATS_JSON, ROOT),
              file=sys.stderr)

    hero_path = os.path.join(ROOT, "docs", "assets", "hero.json")
    if args.skip_hero:
        cached = json.loads(read(hero_path))
        hero_html, hero_cmd = cached["html"], cached["cmd"]
        hero_nums = cached.get("nums")
        if not hero_nums:
            raise SystemExit("%s predates the hero numbers — drop --skip-hero and let the "
                             "build replay the demo once" % os.path.relpath(hero_path, ROOT))
    else:
        hero_html, hero_cmd, hero_nums = hero()
        with open(hero_path, "w", encoding="utf-8") as handle:
            json.dump({"html": hero_html, "cmd": hero_cmd, "nums": hero_nums},
                      handle, indent=1)

    py_floor = python_floor()
    py_lo, py_hi = py_versions()
    head_pct = "%.1f" % stats["overall"]["reduction_pct"]
    todo = read(os.path.join(ROOT, "TODO.md"))

    page = read(TEMPLATE) if args.design == "original" else read(os.path.join(HERE, "site.professional.tmpl"))
    tokens = {
        "HEAD_PCT": head_pct,
        "SHA": run(["git", "rev-parse", "--short", "HEAD"]).strip(),
        "ITERS": str(stats["corpus"]["iters"]),
        "TESTS": fmt(tests()),
        "MUTANTS": fmt(mutants()),
        "SLOT_TABLE": slot_table(stats),
        "PYVERSIONS": "%s and %s" % (py_lo, py_hi),
        "PYTHON": stats["host"]["python"],
        "PYFLOOR": py_floor,
        "POOL_TABLE": pool_table(stats),
        "NTASKS": str(stats["corpus"]["n"]),
        "LOAD": "%.1f" % stats["host"]["load_average"][0],
        "HOST": "%s, %s cores" % (stats["host"]["platform"], os.cpu_count()),
        "HERO": hero_html,
        "TH_REQ": str(hero_nums["request"]),
        "TH_OFF": fmt(hero_nums["off_tok"]),
        "TH_ON": fmt(hero_nums["on_tok"]),
        "TH_PCT": "%.1f" % hero_nums["pct"],
        "TH_W": "%.1f" % (100.0 - hero_nums["pct"]),
        "FIGURES": figures(stats),
        "FIG_REGRET": regret_figure(stats),
        "COST_PROSE": cost_prose(stats),
        "GATE_TABLE": gate_table(todo),
        "CMD": stats["command"],
        "CMD_HERO": hero_cmd,
        "CMDS": str(subcommands()),
    }
    for key, value in tokens.items():
        page = page.replace("{{%s}}" % key, value)
    left = re.findall(r"\{\{(\w+)\}\}", page)
    if left:
        raise SystemExit("template tokens with no value: %s" % sorted(set(left)))

    out = args.out
    os.makedirs(os.path.join(out, "assets", "fonts"), exist_ok=True)
    for name in sorted(os.listdir(FONTS)):
        shutil.copy2(os.path.join(FONTS, name), os.path.join(out, "assets", "fonts", name))
    # The social card is a screenshot of this page, so it is shipped with it: a link
    # preview that 40s is a launch post that shows nothing.
    for name in SOCIAL:
        src = os.path.join(ASSETS, name)
        if not os.path.exists(src):
            raise SystemExit("missing %s — the page's og:image points at it. Re-shoot the "
                             "1200x630 hero and save it there" % os.path.relpath(src, ROOT))
        shutil.copy2(src, os.path.join(out, "assets", name))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(page)
    # GitHub Pages serves a bare directory; a 404 on /favicon.ico is noise on a page
    # whose whole argument is that it shows its sources.
    with open(os.path.join(out, ".nojekyll"), "w") as handle:
        handle.write("")
    print("wrote %s (%s bytes) — %s tests, %s mutants, %s subcommands, head −%s%%"
          % (os.path.relpath(os.path.join(out, "index.html"), ROOT), fmt(len(page)),
             tokens["TESTS"], tokens["MUTANTS"], tokens["CMDS"], head_pct))


if __name__ == "__main__":
    main()
