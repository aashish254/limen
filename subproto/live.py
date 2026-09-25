"""A live terminal savings meter — the shareable screenshot (FR-9 / M5).

`render(snapshot)` is a pure function so it can be unit-tested and screenshotted
in CI (`subproto live --once`); `run` tails the telemetry DB and repaints.

The number is honest: when slots are only observing it reports *potential*
(headroom from recorded decisions); when enforcement is on it reports *delivered*
(input actually removed from the wire). Invariant I6 — never a projection sold
as a measurement.
"""

import os
import sys
import time

from . import pricing
from .telemetry import CATEGORIES

# ANSI: only emitted when the target is a TTY; render() stays pure otherwise.
_GREEN, _DIM, _BOLD, _RESET = "\033[32m", "\033[2m", "\033[1m", "\033[0m"


def _savings_from_decisions(decisions_json):
    import json
    if not decisions_json:
        return 0
    try:
        ds = json.loads(decisions_json)
    except ValueError:
        return 0
    return sum(int(d.get("savings_est_tok") or 0) for d in ds)


def snapshot(tel, mode="auto"):
    rows = tel.query(
        "SELECT count(*) n, sum(in_tok+cw_tok) in_tok, sum(cr_tok) cr_tok, "
        "sum(out_tok) out_tok, sum(cost_usd) cost FROM requests")
    t = rows[0] if rows else {}
    n = int(t.get("n") or 0)
    in_tok = int(t.get("in_tok") or 0)
    cost = float(t.get("cost") or 0.0)
    dec = tel.query("SELECT decisions, interventions FROM requests ORDER BY id DESC LIMIT 2000")
    saved_tok = sum(_savings_from_decisions(r["decisions"]) for r in dec)
    enforced = any(r.get("interventions") for r in dec)
    if mode == "auto":
        mode = "delivered" if enforced else "potential"
    # value of the saved fresh-input tokens at the blended rate already observed
    saved_usd = cost * (saved_tok / in_tok) if in_tok else 0.0
    return {
        "n": n,
        "in_tok": in_tok,
        "cost_usd": cost,
        "saved_tok": saved_tok,
        "saved_pct": 100.0 * saved_tok / in_tok if in_tok else 0.0,
        "after_usd": max(0.0, cost - saved_usd),
        "mode": mode,
    }


def render(snap, color=False):
    def c(code, s):
        return (code + s + _RESET) if color else s
    n = snap.get("n", 0)
    lines = []
    top = "┌" + "─" * 46 + "┐" if color else "+" + "-" * 46 + "+"
    lines.append(c(_BOLD, top))
    title = " subproto · System One meter".ljust(46)
    lines.append(("│" + title + "│") if color else ("|" + title + "|"))
    lines.append(c(_DIM, ("├" + "─" * 46 + "┤") if color else ("+" + "-" * 46 + "+")))
    if not n:
        body = "  no traffic yet — point an agent at the proxy ".ljust(46)
        lines.append(("│" + body + "│") if color else ("|" + body + "|"))
    else:
        word = "saved" if snap["mode"] == "delivered" else "save "
        line1 = " %s %d%% · %s tok   (%s)" % (
            word, int(round(snap["saved_pct"])), _fmt(snap["saved_tok"]), snap["mode"])
        line2 = " $%.2f  →  $%.2f     ·  %d req" % (
            snap["cost_usd"], snap["after_usd"], n)
        lines.append(("│" + line1.ljust(46) + "│") if color else ("|" + line1.ljust(46) + "|"))
        lines.append(("│" + line2.ljust(46) + "│") if color else ("|" + line2.ljust(46) + "|"))
    lines.append(c(_BOLD, ("└" + "─" * 46 + "┘") if color else ("+" + "-" * 46 + "+")))
    return "\n".join(lines)


def _fmt(x):
    x = int(x)
    return "{:,}".format(x)


def run(tel, interval=1.0, once=False, out=None):
    out = out or sys.stdout
    color = hasattr(out, "isatty") and out.isatty()
    snap = snapshot(tel)
    if once:
        out.write(render(snap, color=color) + "\n")
        return snap
    try:
        while True:
            snap = snapshot(tel)
            frame = render(snap, color=color)
            if color:
                out.write("\033[2J\033[H")  # clear + home
            out.write(frame + "\n")
            out.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return snap
