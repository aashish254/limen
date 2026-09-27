#!/usr/bin/env python3
"""The launch figures: the same traffic with the system off and with it on, cut by category.

`bench/live.py` answers one question — does it hold — and prints one pooled row per
metric. A reader deciding whether to install wants the cut *by kind*: which workloads
save most, what is actually being dropped, where accuracy is held, and what the system
charges when it is wrong. This script runs the arms once and re-cuts the same rows five
ways, then writes one SVG per panel.

Everything is measured here at $0: the mock upstream, no key, no egress beyond loopback.
Two panels read artifacts that were measured in earlier runs (`laya_latency.json`,
`ablation.json`) rather than re-measuring them, and say so.

    python3 bench/launch_stats.py [--iters 5] [--seed 7] [--skip-regret]

Writes bench/launch_stats.json and docs/assets/figures/*.svg.
"""
import argparse
import datetime
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIGDIR = os.path.join(ROOT, "docs", "assets", "figures")
sys.path.insert(0, HERE)

import live  # noqa: E402  (bench/live.py, the arm runner)

# Author-labelled families over the 20 tasks in tasks.sample.jsonl. The label is a
# claim about the task, not a measurement, so it is spelled out here rather than
# inferred from a filename pattern a reader cannot check.
FAMILIES = {
    "retry-connection": "payments and retries",
    "idempotency-key": "payments and retries",
    "config-retry-policy": "payments and retries",
    "rate-limit-middleware": "payments and retries",
    "gateway-timeout": "services and infra",
    "db-pool-sizing": "services and infra",
    "grpc-timeout-propagation": "services and infra",
    "cache-invalidation": "services and infra",
    "migration-foreign-key": "services and infra",
    "sql-injection-fix": "security and auth",
    "secret-rotation": "security and auth",
    "webhook-replay-attack": "security and auth",
    "auth-token-refresh": "security and auth",
    "test-retry-coverage": "tests and build",
    "flaky-test-quarantine": "tests and build",
    "build-flake-nondeterminism": "tests and build",
    "sentry-trace": "data correctness",
    "timezone-date-bug": "data correctness",
    "pagination-off-by-one": "data correctness",
    "error-surface": "data correctness",
}
SHORT = {
    "payments and retries": "payments",
    "services and infra": "services",
    "security and auth": "security",
    "tests and build": "tests",
    "data correctness": "data",
    "other": "other",
}

# The page's own tokens, mirrored from subproto/style.py: colour sits on a verdict and
# nowhere else. `without` is graphite because it is a baseline, not a failure.
INK = "#101317"
MUTED = "#5B6472"
GRAPHITE = "#9AA1AA"
GREEN = "#1E7A34"
AMBER = "#A66A00"
RED = "#B3261E"
RULE = "#D5D9E0"
PAPER = "#FFFFFF"
MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace"


def _mean(seq):
    return sum(seq) / float(len(seq)) if seq else 0.0


def _p50(seq):
    s = sorted(seq)
    return s[len(s) // 2] if s else 0.0


def _red(a, b):
    return 100.0 * (a - b) / a if a else 0.0


# --------------------------------------------------------------------------- panels


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _tw(text, size=12.0):
    """Width of a string in this type: the panels are set in a monospace face, so the
    measure is exact rather than estimated."""
    return len(str(text)) * size * 0.62


def _nice(peak):
    """Round an axis maximum up to a value whose quarters are readable numbers.

    A power-of-two ladder alone puts 1,261 ms on a 2,000 ms axis, which throws away half
    the plot; the 1/1.5/2/2.5/3/4/5/6/8 ladder keeps the headroom under 25 %.
    """
    if peak <= 0:
        return 1.0
    step = 1.0
    while True:
        for mult in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8):
            if peak <= mult * step:
                return float(mult * step)
        step *= 10.0


def _fmt_tick(v):
    if abs(v) >= 1000:
        return "{:,.0f}".format(v)
    if abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return "%.1f" % v


def _legend(x, y, entries, size=12):
    """Swatch-and-label pairs, wrapped onto further rows rather than a wider canvas.

    Returns the elements, the end of the widest row, and the extra height the rows need,
    so the caller can push the footnote down instead of overlapping it.
    """
    out, rows, cur, end_max = [], 1, x, x
    for label, colour in entries:
        item = 16 + _tw(label, size) + 26
        if cur > x and cur + item > CANVAS_MAX - 8:
            rows += 1
            cur = x
        yy = y + 13 * (rows - 1)
        out.append('<rect x="%d" y="%d" width="11" height="11" fill="%s"/>'
                   % (cur, yy, colour))
        out.append('<text x="%d" y="%d" font-family="%s" font-size="%d" fill="%s">%s</text>'
                   % (cur + 16, yy + 10, MONO, size, INK, esc(label)))
        cur += item
        end_max = max(end_max, cur)
    return out, end_max, 13 * (rows - 1)


def _wrap(text, px, size):
    """Greedy word wrap by measured width. The panels are monospace, so this is exact."""
    lines, cur = [], ""
    for word in str(text).split():
        trial = (cur + " " + word).strip()
        if cur and _tw(trial, size) > px:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


CANVAS_MAX = 980


def svg_wrap(width, height, title, subtitle, body, footnote=None):
    """One panel: title, subtitle, plot, and a footnote that says what was measured.

    A heading that needs more room takes another line rather than a wider canvas. The page
    scales these to its own column, so a 1,700-unit panel comes back as 7-pixel type — the
    same defect as a clipped title, arrived at from the other direction.
    """
    measure = CANVAS_MAX - 24
    t_lines = _wrap(title, measure, 16)
    s_lines = _wrap(subtitle, measure, 12)
    f_lines = _wrap(footnote, measure, 11) if footnote else []
    width = int(max(width, max(_tw(l, 16) for l in t_lines) + 24,
                    max(_tw(l, 12) for l in s_lines) + 24,
                    max([_tw(l, 11) for l in f_lines] + [0]) + 24))
    head = 19 * (len(t_lines) - 1) + 15 * (len(s_lines) - 1)
    tail = 13 * (len(f_lines) - 1)
    canvas_h = height + head + tail
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'width="%d" height="%d" role="img" aria-label="%s">'
           % (width, canvas_h, width, canvas_h, esc(title))]
    out.append('<rect width="%d" height="%d" fill="%s"/>' % (width, canvas_h, PAPER))
    for i, line in enumerate(t_lines):
        out.append('<text x="0" y="%d" font-family="%s" font-size="16" font-weight="700" '
                   'fill="%s">%s</text>' % (20 + 19 * i, MONO, INK, esc(line)))
    for j, line in enumerate(s_lines):
        out.append('<text x="0" y="%d" font-family="%s" font-size="12" fill="%s">%s</text>'
                   % (38 + head - 15 * (len(s_lines) - 1) + 15 * j, MONO, MUTED, esc(line)))
    out.append('<g transform="translate(0,%d)">%s</g>' % (head, "".join(body)))
    for k, line in enumerate(f_lines):
        out.append('<text x="0" y="%d" font-family="%s" font-size="11" fill="%s">%s</text>'
                   % (canvas_h - 6 - tail + 13 * k, MONO, MUTED, esc(line)))
    out.append("</svg>")
    return "\n".join(out)


def grouped_bars(title, subtitle, labels, series, unit="tok", fmt="{:,.0f}",
                 delta=True, footnote=None, height=300, y_max=None):
    """Vertical grouped bars, one group per label, one bar per series entry.

    `series` is [(name, colour, [value per label])]. The delta label above each group is
    the point of the figure, so it sits above the tallest value in the group rather than
    above one bar, where it used to land on a number.
    """
    pad_l, pad_r, top = 66, 18, 62
    # A long category label is the thing that overflows a grouped panel, not the bars.
    need = pad_l + pad_r + sum(_tw(label, 12) + 26 for label in labels)
    w = max(560, int(need), 40 * len(labels) * len(series) + 90)
    plot_h = height - top - 74
    plot_w = w - pad_l - pad_r
    peak = _nice(y_max or max([v for _, _, vals in series for v in vals] + [1.0]))
    body = []
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = top + plot_h - frac * plot_h
        body.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
                    % (pad_l, y, w - pad_r, y, RULE))
        body.append('<text x="%d" y="%.1f" text-anchor="end" font-family="%s" font-size="11" '
                    'fill="%s">%s</text>'
                    % (pad_l - 9, y + 4, MONO, MUTED, _fmt_tick(peak * frac)))
    group_w = float(plot_w) / len(labels)
    bar_w = min(38.0, (group_w * 0.66) / len(series))
    for i, label in enumerate(labels):
        cx = pad_l + group_w * (i + 0.5)
        x0 = cx - (bar_w * len(series) + 6 * (len(series) - 1)) / 2.0
        tops = []
        for s, (name, colour, vals) in enumerate(series):
            v = vals[i]
            h = max(1.0, plot_h * (v / peak))
            x = x0 + s * (bar_w + 6)
            y = top + plot_h - h
            tops.append(y)
            body.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                        % (x, y, bar_w, h, colour))
            body.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
                        'font-size="11" fill="%s">%s</text>'
                        % (x + bar_w / 2, y - 5, MONO, INK, fmt.format(v)))
        if delta and len(series) > 1:
            first, last = series[0][2][i], series[-1][2][i]
            d = _red(first, last)
            if abs(d) < 999:
                mark = ("-%.1f%%" % d) if d > 0 else (("+%.1f%%" % -d) if d < 0 else "0%")
                colour = series[-1][1] if d > 0 else MUTED
                body.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
                            'font-size="12" font-weight="700" fill="%s">%s</text>'
                            % (cx, min(tops) - 21, MONO, colour, mark))
        body.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="%s" font-size="12" '
                    'fill="%s">%s</text>' % (cx, top + plot_h + 20, MONO, INK, esc(label)))
    leg, lx, leg_h = _legend(pad_l, top + plot_h + 36, [(n, c) for n, c, _ in series])
    body.extend(leg)
    if unit:
        body.append('<text x="%d" y="%d" font-family="%s" font-size="11" fill="%s">%s</text>'
                    % (w - pad_r, top + plot_h + 46 + leg_h, MONO, MUTED, esc(unit)))
    w = max(w, lx + pad_r)
    return svg_wrap(w, height + leg_h, title, subtitle, body, footnote)


def stacked_h(title, subtitle, rows, total_label, footnote=None):
    """One horizontal bar split into segments — the composition of what a request holds."""
    w, top, row_h = 760, 80, 34
    height = top + row_h * len(rows) + 66
    peak = max(sum(v for _, v in segs) for _, segs in rows) or 1.0
    pad_l = int(max([_tw(label, 12) for label, _ in rows] + [120])) + 14
    plot_w = w - pad_l - 120
    body = []
    palette = [GREEN, AMBER, GRAPHITE, "#3F6E8F", "#7A5C99"]
    for r, (label, segs) in enumerate(rows):
        y = top + r * row_h
        x = pad_l
        body.append('<text x="0" y="%d" font-family="%s" font-size="12" fill="%s">%s</text>'
                    % (y + 17, MONO, INK, esc(label)))
        for s, (name, v) in enumerate(segs):
            wseg = plot_w * (v / peak)
            if wseg <= 0:
                continue
            body.append('<rect x="%.1f" y="%d" width="%.1f" height="20" fill="%s"/>'
                        % (x, y, wseg, palette[s % len(palette)]))
            if wseg > 52:
                body.append('<text x="%.1f" y="%d" font-family="%s" font-size="11" '
                            'fill="#FFFFFF">%s</text>'
                            % (x + 5, y + 14, MONO, esc("{:,.0f}".format(v))))
            x += wseg
        body.append('<text x="%.1f" y="%d" font-family="%s" font-size="12" font-weight="700" '
                    'fill="%s">%s</text>'
                    % (x + 10, y + 15, MONO, INK, esc("{:,.0f}".format(sum(v for _, v in segs)))))
    leg, lx, leg_h = _legend(pad_l, height - 38,
                             [("%s  %s" % (name, "{:,.0f}".format(val)),
                               palette[s % len(palette)])
                              for s, (name, val) in enumerate(rows[0][1])], size=11)
    body.extend(leg)
    body.append('<text x="0" y="%d" font-family="%s" font-size="11" fill="%s">%s</text>'
                % (top - 14, MONO, MUTED, esc(total_label)))
    return svg_wrap(max(w, lx + 20), height + leg_h, title, subtitle, body, footnote)


def budget_bars(title, subtitle, labels, vals, budget, footnote=None):
    """Latency shapes against the shipped per-decision budget line."""
    w, height = 620, 280
    top, pad_l, plot_h = 58, 62, 150
    plot_w = w - pad_l - 20
    peak = _nice(max(vals + [budget]) * 1.15)
    body = []
    for frac in (0.0, 0.5, 1.0):
        y = top + plot_h - frac * plot_h
        body.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
                    % (pad_l, y, w - 20, y, RULE))
        body.append('<text x="%d" y="%.1f" text-anchor="end" font-family="%s" font-size="11" '
                    'fill="%s">%d</text>'
                    % (pad_l - 8, y + 4, MONO, MUTED, int(peak * frac)))
    by = top + plot_h - plot_h * (budget / peak)
    body.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="2" '
                'stroke-dasharray="6 4"/>' % (pad_l, by, w - 20, by, RED))
    body.append('<text x="%d" y="%.1f" font-family="%s" font-size="11" fill="%s">budget %d ms</text>'
                % (pad_l + 4, by - 6, MONO, RED, budget))
    bw = min(64.0, plot_w / len(labels) * 0.5)
    for i, label in enumerate(labels):
        cx = pad_l + (plot_w / len(labels)) * (i + 0.5)
        h = plot_h * (vals[i] / peak)
        colour = GREEN if vals[i] <= budget else RED
        body.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                    % (cx - bw / 2, top + plot_h - h, bw, h, colour))
        body.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" font-size="12" '
                    'fill="%s">%d</text>' % (cx, top + plot_h - h - 6, MONO, INK, vals[i]))
        body.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="%s" font-size="12" '
                    'fill="%s">%s</text>' % (cx, top + plot_h + 20, MONO, INK, esc(label)))
    return svg_wrap(w, height, title, subtitle, body, footnote)


def write_svg(name, text):
    if not os.path.isdir(FIGDIR):
        os.makedirs(FIGDIR)
    path = os.path.join(FIGDIR, name + ".svg")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return path


# --------------------------------------------------------------------------- legs


def arms_leg(tasks, iters, seed):
    rows = live.run_arms(tasks, iters, seed)
    for row in rows:
        row["family"] = FAMILIES.get(row["id"], "other")
    return rows


def group_by(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return groups


def cut_by_family(rows):
    groups = group_by(rows, lambda r: r["family"])
    names = sorted(groups, key=lambda k: -_mean([r["observe"]["in_tok"] for r in groups[k]]))
    obs = [_mean([r["observe"]["in_tok"] for r in groups[k]]) for k in names]
    enf = [_mean([r["enforce"]["in_tok"] for r in groups[k]]) for k in names]
    cmp_ = [_mean([r["compiled"]["in_tok"] for r in groups[k]]) for k in names]
    pas = {arm: [_pct([r[arm]["pass"] for r in groups[k]]) for k in names]
           for arm in ("observe", "enforce", "compiled")}
    return {
        "labels": [SHORT.get(n, n) for n in names],
        "families": names,
        "counts": [len(groups[k]) for k in names],
        "observe": obs, "enforce": enf, "compiled": cmp_,
        "reduction_pct": [_red(a, b) for a, b in zip(obs, cmp_)],
        "pass_rate": pas,
    }


def _pct(seq):
    return 100.0 * _mean(seq)


def cut_by_size(rows):
    """Quartiles of the *unmanaged* request, so the bucket cannot be chosen by the result."""
    ordered = sorted(rows, key=lambda r: r["observe"]["in_tok"])
    n = len(ordered)
    buckets = [ordered[i * n // 4:(i + 1) * n // 4] for i in range(4)]
    labels = ["Q1 smallest", "Q2", "Q3", "Q4 largest"]
    obs = [_mean([r["observe"]["in_tok"] for r in b]) for b in buckets]
    cmp_ = [_mean([r["compiled"]["in_tok"] for r in b]) for b in buckets]
    rng = [[min(r["observe"]["in_tok"] for r in b), max(r["observe"]["in_tok"] for r in b)]
           for b in buckets]
    return {
        "labels": labels, "observe": obs, "compiled": cmp_,
        "reduction_pct": [_red(a, b) for a, b in zip(obs, cmp_)],
        "tok_ranges": rng, "counts": [len(b) for b in buckets],
    }


def slot_leg():
    """What is actually being cut, per slot, on the deterministic synthetic corpus."""
    out = subprocess.run([sys.executable, os.path.join(HERE, "ab.py"), "--mock", "24", "--json"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def regret_leg(port):
    home = tempfile.mkdtemp(prefix="launch-regret-")
    env = dict(os.environ, SUBPROTO_HOME=home, SUBPROTO_STORE_BODIES="1",
               SUBPROTO_APPLY="tool_gate,compact", SUBPROTO_COMPILE="on")
    subprocess.run([sys.executable, "-m", "subproto", "demo", "--port", str(port),
                    "--slots", "--audit"], cwd=ROOT, env=env,
                   stdout=subprocess.DEVNULL, check=True)
    out = subprocess.run([sys.executable, "-m", "subproto", "report", "--home", home, "--json"],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
    report = json.loads(out)
    er = report.get("eviction_regret") or {}
    er["home"] = home
    return er


def checkpoint_leg():
    from subproto.systemone import router as router_mod

    with open(os.path.join(HERE, "laya_latency.json"), encoding="utf-8") as handle:
        curve = json.load(handle)
    # The shipped path, asked of the product: the evidence moved into the package with
    # S46/M4, and a bench copy of that path is how a figure quietly goes stale.
    with open(router_mod.DEFAULT_ABLATION_JSON, encoding="utf-8") as handle:
        abl = json.load(handle)
    return curve, abl


def composition_leg(tasks):
    """What the average request in the corpus actually carries.

    The arms measure what the system *removes*; a reader cannot judge that number without
    the denominator. `build_request` is deterministic and labels every part it adds, so the
    request can be taken apart with the same estimator the proxy uses, and each part weighed
    on its own. The parts are measured separately and then scaled to the whole request,
    because the estimator charges per-message overhead once per body it reads.
    """
    from subproto.proxy import analyze_request

    parts = {}
    totals = []
    for task in tasks:
        body = live.build_request(task, 0)
        total = analyze_request(body)["est_in_tok"]
        totals.append(total)
        flags = {c["path"]: ("big read" if c.get("big")
                             else "stale read" if c.get("stale") else "recent read")
                 for c in task["context"]}
        required = set(task["expected_files"])

        def weigh(sub):
            return analyze_request(sub)["est_in_tok"]

        bare = {"system": body["system"], "messages": []}
        sys_tok = weigh(bare)
        task_tok = weigh({"system": body["system"], "messages": body["messages"][:1]}) - sys_tok
        tool_tok = weigh({"system": body["system"], "tools": body["tools"],
                          "messages": body["messages"][:1]}) - sys_tok - task_tok
        parts.setdefault("system prompt", []).append(sys_tok)
        parts.setdefault("task instruction", []).append(task_tok)
        parts.setdefault("tool specs (24)", []).append(tool_tok)
        for i in range(1, len(body["messages"]) - 1, 2):
            pair = body["messages"][i:i + 2]
            use = pair[0]["content"][0]
            path = use["input"]["file_path"]
            if path in required:
                kind = "the files the task needs"
            elif path.startswith("app/misc/scrollpad"):
                kind = "filler turns"
            elif flags.get(path) == "big read":
                kind = "a big read that is needed"
            else:
                kind = "stale reads"
            parts.setdefault(kind, []).append(weigh({"messages": pair}))
    mean_total = _mean(totals)
    raw = dict((k, _mean(v)) for k, v in parts.items())
    scale = mean_total / sum(raw.values()) if raw else 1.0
    ordered = sorted(((k, v * scale) for k, v in raw.items()), key=lambda kv: -kv[1])
    return {"mean_request_tok": mean_total, "scale": scale, "parts": ordered}


def hard_leg(path, iters, seed):
    """The set built to break the naive version of this idea.

    On these tasks a per-slot budget drops files the grader needs, so the arm that looks
    cheaper is the arm that fails. It is on the page because it is the one result that
    argues for the compiler rather than for the idea.
    """
    rows = live.run_arms(live.load_tasks(path), iters, seed)
    out = {}
    for arm in live.ARMS:
        out[arm] = {
            "in_tok": _mean([r[arm]["in_tok"] for r in rows]),
            "pass_pct": _pct([r[arm]["pass"] for r in rows]),
            "recall_pct": _pct([r[arm]["recall"] for r in rows]),
            "precision_pct": _pct([r[arm]["precision"] for r in rows]),
        }
    out["n"] = len(rows)
    return out


def s32_leg():
    """The compiler's own cost, re-measured in one process. A timing leg: nothing else
    should be on the machine while it runs, and the number moves with host load."""
    out = subprocess.run([sys.executable, os.path.join(HERE, "s32_probe.py"), "4"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    line = [l for l in out.splitlines() if "gate" in l or "ms" in l]
    return {"raw": line, "text": out.strip().splitlines()[-1] if out.strip() else ""}


def render_figures(stats):
    """The panels, drawn from the recorded json rather than from a live measurement.

    Every number and every label here comes out of `stats`, which is what a run wrote.
    That is what lets a caption be corrected without re-measuring the arms: the figures
    are a view of the record, not a second experiment that can disagree with the first.
    """
    fam = stats["by_family"]
    size = stats["by_size"]
    slots = stats["slots"]
    comp = stats["composition"]
    hard = stats["hard_set"]
    overall = stats["overall"]
    regret = stats["regret"]
    n_tasks = stats["corpus"]["n"]
    iters = stats["corpus"]["iters"]

    written = []
    written.append(write_svg(
        "tokens-by-family",
        grouped_bars(
            "input tokens per task, by what the task is about",
            "%d tasks, %d repeats each, same mock upstream. The compiler spends one budget "
            "over the whole request." % (n_tasks, iters),
            fam["labels"],
            [("the layer off", GRAPHITE, fam["observe"]),
             ("per-slot budgets", AMBER, fam["enforce"]),
             ("one compiled budget", GREEN, fam["compiled"])],
            footnote="measured on the mock at $0; a request-shape proxy, not a billed invoice. "
                     "Families are author labels."))
    )
    written.append(write_svg(
        "tokens-by-size",
        grouped_bars(
            "the cut by request size, quartiles of the unmanaged request",
            "buckets are set by what the request costs before the system sees it, so a "
            "task cannot be moved into a flattering bucket",
            size["labels"],
            [("the layer off", GRAPHITE, size["observe"]),
             ("the layer on", GREEN, size["compiled"])],
            footnote="mean input tokens per task in each quartile; %s"
                     % ", ".join("%s %d-%d" % (l, int(a), int(b)) for l, (a, b)
                                 in zip(size["labels"], size["tok_ranges"])))))
    sl = slots["slots"]
    order = sorted(sl, key=lambda k: -sl[k]["savings_est_tok"])
    saved_by_slot = [(k, sl[k]["savings_est_tok"]) for k in order if sl[k]["savings_est_tok"]]
    saved_total = sum(v for _, v in saved_by_slot)
    written.append(write_svg(
        "cut-composition",
        stacked_h(
            "what each slot would cut, in estimated input tokens",
            "a projection per slot over the deterministic synthetic corpus "
            "(%s requests, %s est input tokens)"
            % ("{:,}".format(slots["totals"]["requests"]),
               "{:,}".format(slots["totals"]["est_in_tok"])),
            [("the synthetic corpus", saved_by_slot or [("nothing", 0)])],
            "total %s tok" % "{:,.0f}".format(saved_total),
            footnote="a projection from measured prompt shapes (I6): these are tokens that "
                     "would not be sent, not a provider invoice."))
    )
    written.append(write_svg(
        "request-composition",
        stacked_h(
            "what the average request carries before anything is decided",
            "mean of %d tasks, %s est input tokens, weighed part by part with the estimator "
            "the proxy itself uses"
            % (n_tasks, "{:,.0f}".format(comp["mean_request_tok"])),
            [("mean request", comp["parts"])],
            "total %s tok" % "{:,.0f}".format(comp["mean_request_tok"]),
            footnote="parts are weighed separately and scaled to the measured total (x%.3f). "
                     "Only the two rightmost categories are what the slots may touch."
                     % comp["scale"]))
    )
    written.append(write_svg(
        "hard-set",
        grouped_bars(
            "the set built to break the naive version of this idea",
            "%d long requests where the file the task needs sits behind a run of large "
            "files it does not need. A per-slot budget spends itself on the newest and "
            "drops the one that matters; one joint budget over the whole request does not."
            % hard["n"],
            ["task pass-rate", "recall of required files", "precision of what survived"],
            [("three slots, three budgets", RED,
              [hard["enforce"]["pass_pct"], hard["enforce"]["recall_pct"],
               hard["enforce"]["precision_pct"]]),
             ("one compiled budget", GREEN,
              [hard["compiled"]["pass_pct"], hard["compiled"]["recall_pct"],
               hard["compiled"]["precision_pct"]])],
            unit="%", fmt="{:.1f}", y_max=100.0, delta=False,
            footnote="this is why the shipping arm is the compiler. A slot that saves more "
                     "tokens than it is allowed to is not a saving, it is a failure."))
    )
    written.append(write_svg(
        "accuracy-held",
        grouped_bars(
            "task pass-rate, by family: the floor the cut has to clear",
            "a task passes when the files it needed are still in the request. The gate is "
            "-1 pp; the observed delta is %.1f pp."
            % (overall["pass_rate"]["compiled"] - overall["pass_rate"]["observe"]),
            fam["labels"],
            [("the layer off", GRAPHITE, fam["pass_rate"]["observe"]),
             ("the layer on", GREEN, fam["pass_rate"]["compiled"])],
            unit="% of tasks passing", fmt="{:.0f}", y_max=100.0, delta=False,
            footnote="same %d tasks, %d repeats, mock upstream. Held at 100%%, family by "
                     "family, is the claim; a pooled average could hide one broken family."
                     % (n_tasks, iters)))
    )
    written.append(write_svg(
        "recall-precision",
        grouped_bars(
            "what survives the cut: recall and precision of the evidence",
            "recall = required files still present. precision = the share of what survived "
            "that was required.",
            ["recall of required files", "precision of what survived"],
            [("the layer off", GRAPHITE,
              [overall["recall"]["observe"], overall["precision"]["observe"]]),
             ("the layer on", GREEN,
              [overall["recall"]["compiled"], overall["precision"]["compiled"]])],
            unit="%", fmt="{:.1f}", y_max=100.0, delta=False,
            footnote="precision starts low because an unmanaged request carries every tool "
                     "spec and every stale turn; the cut raises it only if recall holds."))
    )
    shapes = stats["checkpoint_curve"]["shapes"] or []
    if shapes:
        cond = stats["checkpoint_curve"]["conditions"] or {}
        per_shape = _mean([s["ms"]["n"] for s in shapes if s.get("ms")])
        written.append(write_svg(
            "checkpoint-cost",
            budget_bars(
                "the real checkpoint's cost per decision, against the shipped budget",
                "%s %s on %s, %d decisions per shape, measured through the server that "
                "would use it" % (cond.get("model", "the checkpoint"),
                                  cond.get("laya", ""),
                                  cond.get("device", "the recorded device"), per_shape),
                [s.get("shape", "?") for s in shapes],
                [int(round((s.get("ms") or {}).get("p50", 0))) for s in shapes],
                int(stats["checkpoint_curve"]["budget_ms"] or 350),
                footnote="read from bench/laya_latency.json, measured earlier; not re-run "
                         "here. Only the shape under the red line is usable in the engine."))
        )
    if regret:
        paid = int(regret.get("refetch_tok_paid") or 0)
        written.append(write_svg(
            "regret-bill",
            grouped_bars(
                "what enforcing costs when it is wrong",
                "tokens paid back by re-reads of drops the traffic asked for again, against "
                "the tokens the same corpus saves",
                ["tokens saved", "tokens paid back"],
                [("this corpus", GREEN, [saved_total, paid])],
                delta=False,
                footnote="%d regrettable drops on replayed demo traffic with the slots "
                         "enforcing; the payback is %.1f%% of the saving, and it is the "
                         "price of enforcing rather than of observing."
                         % (int(regret.get("regrettable_drops") or 0),
                            100.0 * paid / saved_total if saved_total else 0.0))
        ))
    return written


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=os.path.join(HERE, "tasks.sample.jsonl"))
    ap.add_argument("--hard", default=os.path.join(HERE, "tasks.hard.jsonl"))
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--skip-regret", action="store_true")
    ap.add_argument("--skip-timing", action="store_true",
                    help="skip the s32 leg (the only arm-independent timing run here)")
    ap.add_argument("--figures-only", action="store_true",
                    help="redraw the panels from bench/launch_stats.json and measure nothing")
    args = ap.parse_args()

    if args.figures_only:
        record_path = os.path.join(HERE, "launch_stats.json")
        if not os.path.exists(record_path):
            raise SystemExit("no %s to redraw from" % record_path)
        with open(record_path, encoding="utf-8") as handle:
            recorded = json.load(handle)
        drawn = render_figures(recorded)
        for path in drawn:
            print("  %s" % os.path.relpath(path, ROOT))
        print("redrew %d panels from the recorded run of %s (commit %s), measured nothing"
              % (len(drawn), recorded["generated"], recorded["git_sha"]))
        return 0

    tasks = live.load_tasks(args.tasks)
    print("arms: %d tasks x 3 arms x %d iters, mock upstream, $0" % (len(tasks), args.iters))
    rows = arms_leg(tasks, args.iters, args.seed)
    comp = composition_leg(tasks)
    hard = hard_leg(args.hard, args.iters, args.seed)
    print("hard set: %d tasks, per-slot pass %.0f%% recall %.1f%%, compiled pass %.0f%% recall %.0f%%"
          % (hard["n"], hard["enforce"]["pass_pct"], hard["enforce"]["recall_pct"],
             hard["compiled"]["pass_pct"], hard["compiled"]["recall_pct"]))
    fam = cut_by_family(rows)
    size = cut_by_size(rows)
    overall = {
        "observe": _mean([r["observe"]["in_tok"] for r in rows]),
        "enforce": _mean([r["enforce"]["in_tok"] for r in rows]),
        "compiled": _mean([r["compiled"]["in_tok"] for r in rows]),
        "p50_latency": {arm: _p50([r[arm]["latency_ms"] for r in rows])
                        for arm in ("observe", "enforce", "compiled")},
        "pass_rate": {arm: _mean([r[arm]["pass"] for r in rows]) * 100.0
                      for arm in ("observe", "enforce", "compiled")},
        "recall": {arm: _mean([r[arm]["recall"] for r in rows]) * 100.0
                   for arm in ("observe", "enforce", "compiled")},
        "precision": {arm: _mean([r[arm]["precision"] for r in rows]) * 100.0
                      for arm in ("observe", "enforce", "compiled")},
    }
    overall["reduction_pct"] = _red(overall["observe"], overall["compiled"])

    slots = slot_leg()
    curve, abl = checkpoint_leg()
    regret = None if args.skip_regret else regret_leg(live.free_port())
    timing = None if args.skip_timing else s32_leg()

    stats = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "host": {"platform": "%s %s" % (platform.system(), platform.release()),
                 "python": platform.python_version(),
                 "load_average": os.getloadavg()},
        "git_sha": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.strip(),
        "command": " ".join(["python3 bench/launch_stats.py"] + sys.argv[1:]),
        "corpus": {"tasks": os.path.basename(args.tasks), "n": len(tasks),
                   "arms": list(live.ARMS), "iters": args.iters, "seed": args.seed,
                   "upstream": "in-repo mock, $0, no key"},
        "overall": overall,
        "by_family": fam,
        "by_size": size,
        "composition": comp,
        "hard_set": hard,
        "slots": slots,
        "regret": regret,
        "checkpoint_curve": {"conditions": curve.get("conditions"),
                             "shapes": [{k: s.get(k) for k in ("shape", "shipped", "passes", "ms")}
                                        for s in curve.get("shapes") or []],
                             "budget_ms": curve.get("budget_ms")},
        "ablation": {k: abl.get(k) for k in ("n_cases", "heuristic", "corpus")},
        "s32": timing,
    }
    out = os.path.join(HERE, "launch_stats.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=1, sort_keys=False)
    print("wrote %s" % out)

    written = render_figures(stats)
    print("figures: %d written to docs/assets/figures" % len(written))
    for path in written:
        print("  %s" % os.path.relpath(path, ROOT))
    overall = stats["overall"]
    fam = stats["by_family"]
    print("overall: %.1f%% fewer input tokens, pass-rate %.1f%% -> %.1f%%, recall %.1f%% -> %.1f%%"
          % (overall["reduction_pct"], overall["pass_rate"]["observe"],
             overall["pass_rate"]["compiled"], overall["recall"]["observe"],
             overall["recall"]["compiled"]))
    for label, red in zip(fam["labels"], fam["reduction_pct"]):
        print("  %-10s -%.1f%%" % (label, red))
    return 0


if __name__ == "__main__":
    sys.exit(main())
