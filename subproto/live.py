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
from . import style as _style
from .telemetry import CATEGORIES


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
        "sum(out_tok) out_tok, sum(cost_usd) cost, sum(est_in_tok) est_in "
        "FROM requests")
    t = rows[0] if rows else {}
    n = int(t.get("n") or 0)
    in_tok = int(t.get("in_tok") or 0)
    cost = float(t.get("cost") or 0.0)
    dec = tel.query("SELECT decisions, interventions FROM requests ORDER BY id DESC LIMIT 2000")
    saved_tok = sum(_savings_from_decisions(r["decisions"]) for r in dec)
    enforced = any(r.get("interventions") for r in dec)
    if mode == "auto":
        mode = "delivered" if enforced else "potential"
    # The savings are estimated against the whole prompt shape, so the denominator
    # must be the whole estimated input (report.opportunity_gaps uses the same basis).
    # Using only the cache-excluded billed input would divide a full-prompt numerator
    # by a mostly-cached denominator and report a nonsense >100% "saving" (I6).
    denom = int(t.get("est_in") or 0) or (in_tok + int(t.get("cr_tok") or 0))
    # value of the saved fresh-input tokens at the blended rate already observed
    saved_usd = cost * (saved_tok / denom) if denom else 0.0
    return {
        "n": n,
        "in_tok": in_tok,
        "cost_usd": cost,
        "saved_tok": saved_tok,
        "saved_pct": 100.0 * saved_tok / denom if denom else 0.0,
        "denom_tok": int(denom) if denom else 0,
        "after_usd": max(0.0, cost - saved_usd),
        "mode": mode,
    }


def render(snap, color=False):
    """The meter, on the same grid as every other surface.

    This used to draw a fixed 46-column box (and a `+---+` one when colour was
    off). A frame is the wrong object here: the number is the point, and a box
    wraps into noise the moment it is pasted into a README or a narrow terminal.
    """
    n = snap.get("n", 0)
    lines = [_style.header("live", "System One meter", color=color)]
    if not n:
        lines.append("")
        lines.append(_style.row("traffic", "no traffic yet",
                                label_w=_style.METRIC_W, color=color))
        lines.append(_style.note("point an agent at the proxy, then come back",
                                 color=color))
        return "\n".join(lines)
    delivered = snap["mode"] == "delivered"
    # One figure column for all four rows: `field(..., 9)` each, so the eye travels
    # straight down the numbers instead of hunting for where each row's begins.
    lines.append("")
    lines.append(_style.row(
        "tokens " + ("saved" if delivered else "saveable"),
        _style.field("%d%%" % int(round(snap["saved_pct"])), width=9, color=color)
        + "   "
        + _style.field(snap["saved_tok"], width=9, color=color)
        + _style.unit("tok", color=color), label_w=_style.METRIC_W, color=color,
        styled=True))
    # The percentage is a ratio against the whole estimated prompt, not the billed
    # input printed two rows down. Without the denominator on the page the two
    # figures look contradictory (66,493 / 179,280 reads 37%, not 32%), so the
    # basis is stated as its own row rather than left in a comment in the source.
    if snap.get("denom_tok"):
        lines.append(_style.row(
            "prompt incl. cached",
            _style.field(snap["denom_tok"], width=9, color=color)
            + _style.unit("tok", color=color), label_w=_style.METRIC_W,
            color=color, styled=True))
    lines.append(_style.row(
        "spend before", _style.field("$%.2f" % snap["cost_usd"], width=9,
                                     color=color),
        label_w=_style.METRIC_W, color=color, styled=True))
    lines.append(_style.row(
        "spend after", _style.field("$%.2f" % snap["after_usd"], width=9, color=color),
        label_w=_style.METRIC_W, color=color, styled=True))
    lines.append(_style.row(
        "traffic", _style.field(n, width=9, color=color)
        + _style.unit("req", color=color) + "   "
        + _style.field(snap["in_tok"], width=9, color=color)
        + _style.unit("tok in", color=color), label_w=_style.METRIC_W, color=color,
        styled=True))
    lines.append("")
    # I6: potential is headroom, delivered is measurement, and the meter never
    # lets one read as the other.
    lines.append("    " + _style.state(
        _style.DELIVERED if delivered else _style.POTENTIAL, color=color)
        + _style.paint(" — %s" % ("removed from the wire" if delivered else
                                  "headroom; slots are observing, not enforcing"),
                       _style.DIM, color))
    return "\n".join(lines)



def run(tel, interval=1.0, once=False, out=None):
    out = out or sys.stdout
    # The refresh is a control code, not a colour: NO_COLOR still gets a meter in
    # place, it just gets a monochrome one. Painting goes through the one gate every
    # other surface uses, so `--no-color` and a pipe answer the same here as elsewhere.
    frame = bool(getattr(out, "isatty", None) and out.isatty())
    color = _style.enabled(out)
    snap = snapshot(tel)
    if once:
        out.write(render(snap, color=color) + "\n")
        return snap
    try:
        while True:
            snap = snapshot(tel)
            rendered = render(snap, color=color)
            if frame:
                out.write("\033[2J\033[H")  # clear + home
            out.write(rendered + "\n")
            out.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    return snap
