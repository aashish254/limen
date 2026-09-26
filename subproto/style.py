"""The terminal look: one grid, one accent, and no decoration that breaks on paste.

Every printed surface in subproto exists to be copied — into a README, an issue, a
demo GIF, a terminal three panes wide. So the two rules this module enforces are
*no fixed-width frames* (a 46-column box wraps into noise at 60) and *one reason to
reach for colour* (state, never styling). Everything else is alignment.

The grid: a label column right-aligned at ``LABEL_W``, content starting at
``INDENT + LABEL_W + 2``, data rows under a section at ``SUB_INDENT``. Numbers are
right-aligned against their own unit column so a readout can be scanned downward.

Colour is decided once, by :func:`enabled`: a real TTY, and no ``NO_COLOR``. Under
pytest's ``capsys``, in a pipe, and in a GIF capture the answer is plain text — which
is the same text minus escapes, so alignment holds either way.
"""

import os
import re
import sys
from textwrap import wrap as _wrap

# The grid. Two leading spaces, a 14-wide label column, two spaces of gutter.
INDENT = 2
LABEL_W = 14
CONTENT = INDENT + LABEL_W + 2
SUB_INDENT = 4

RESET, DIM, BOLD, GREEN, AMBER, RED = (
    "\033[0m", "\033[2m", "\033[1m", "\033[32m", "\033[33m", "\033[31m")

# State is the only thing allowed colour, and it uses one shared vocabulary: a
# surface may add numbers beside these, but not invent a fifth word for "no".
OK, UP, YES, READY, PRESENT = "ok", "up", "yes", "ready", "present"
WARN, SHORT, INSTALLED = "warn", "short", "installed"
# `on`/`off` are one pair: a startup banner answers "is this switched on" for
# several things at once, and a thing that is switched off is not a thing that is
# missing — telling them apart is the reader's whole question at startup.
ON, OFF = "on", "off"
# The meter's own two states, in the same vocabulary rather than page-local words.
POTENTIAL, DELIVERED = "potential", "delivered"
DOWN, MISSING, NOT_READY = "not available", "missing", "not ready"
# The vocabulary's one glyph: a blocking reason is a state, and it wears the same
# amber as the words beside it rather than inventing a second alarm colour.
BLOCK = "!"

# Red is reserved for a thing that is actually broken. A gate that has not
# cleared yet is news, not a failure, so it takes the accent rather than the alarm.
_CODES = {OK: GREEN, UP: GREEN, YES: GREEN, READY: GREEN,
          PRESENT: GREEN, INSTALLED: GREEN, DELIVERED: GREEN,
          POTENTIAL: AMBER,
          ON: GREEN, WARN: AMBER, SHORT: AMBER, NOT_READY: AMBER, OFF: AMBER,
          BLOCK: AMBER,
          DOWN: RED, MISSING: RED}

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _pad(text, width):
    """Pad to a column by *visible* length. A caller may hand over a value it has
    already painted — a state word inside a table cell — and counting its escapes
    would move the column for a coloured terminal and not for a pipe."""
    text = str(text)
    fill = max(0, abs(width) - len(strip(text)))
    return (" " * fill + text) if width > 0 else (text + " " * fill)


def enabled(stream=None, force=None):
    """True when escapes should be emitted for this stream.

    ``force`` is an explicit answer for this one call: it beats every heuristic,
    including ``NO_COLOR``. With no argument given, the process-wide ``--color`` /
    ``--no-color`` choice (see :func:`override`) answers next, and the stream's own
    ``isatty`` answers last.
    """
    if force is not None:
        return bool(force)
    if _FORCE is not None:
        return _FORCE
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream if stream is not None else sys.stdout
    return bool(getattr(stream, "isatty", None) and stream.isatty())


# The `--color` / `--no-color` answer for this process, or None to leave the decision
# to the heuristics. One variable because one run prints one page to one terminal.
_FORCE = None


def override(value):
    """Record the flag choice. ``None`` hands the decision back to the heuristics, so
    a command run without the flag cannot leak a forced answer into the next one."""
    global _FORCE
    _FORCE = None if value is None else bool(value)


def paint(text, code=None, color=False):
    if not code or not color:
        return text
    return code + text + RESET


def strip(text):
    """Remove escapes — for the tests that assert the plain text still reads."""
    return _ANSI_RE.sub("", text)


def header(command, *meta, **kw):
    """`subproto report — 17 requests`, the metadata dim.

    An em dash, not a `·`: the dot was also standing inside about-text, so the
    separator meant two things at once. Exactly one dash opens a header — after
    the first, further metadata is a comma-separated list, or a page title with
    three parts cannot be read as term-then-qualifier. Metadata that came out
    empty is left out rather than printed as a dangling dash.
    """
    color = bool(kw.get("color"))
    parts = [paint("subproto %s" % command, BOLD, color)]
    for i, item in enumerate([m for m in meta if str(m)]):
        parts.append(paint(" — " if i == 0 else ", ", DIM, color))
        parts.append(paint(str(item), DIM, color))
    return "".join(parts)


def section(title, meta=None, indent=INDENT, color=False):
    """A named block, dim, at the indent its own rows sit under. `meta` is the
    parenthetical qualifier that scopes what the block measures."""
    text = " " * indent + str(title)
    if meta:
        text += "  %s" % meta
    return paint(text, DIM, color)


def row(label, value="", label_w=LABEL_W, indent=INDENT, color=False, styled=False):
    """One grid row: right-aligned dim label, then the value in the content column.

    Pass the same ``label_w`` to every row of a block — that is what puts the
    numbers in one column. ``styled=True`` means the caller already composed the
    value out of `field`/`unit`/`state`, so it must not be wrapped again: nested
    escapes would re-bold the dim units and end the row's styling early.
    """
    text = " " * indent + paint(_pad(label, label_w), DIM, color)
    if value == "":
        return text
    return text + "  " + (str(value) if styled else paint(str(value), BOLD, color))


# One label column for a whole surface, as wide as its longest label, so every
# figure in the document lands on the same right edge. That is the whole system.
METRIC_W = 25
NUM_W = 12
# Where a metric block's values start — and therefore where its prose must hang to
# read as belonging to the number column rather than to the labels beside it.
METRIC_CONTENT = INDENT + METRIC_W + 2


def table(name, *data, **kw):
    """One row of a data table: the row's subject padded bright, then its figures.

    Each figure is a (value, label, width) triple with the label trailing in dim, so
    `14,280 in` can never run into the next column — which is exactly how the
    hand-built %-format rows used to break. A negative width left-aligns, the way
    Python's own format spec reads it: numbers take positive widths and share a
    right edge, identifiers take negative ones and share a left edge.
    """
    color = bool(kw.get("color"))
    name_w = kw.get("name_w", 22)
    indent = kw.get("indent", SUB_INDENT)
    out = [" " * indent + paint(_pad(name, -abs(name_w)), BOLD, color)]
    for item in data:
        value, label, width = item
        # Only whole counts get separators; a percentage must keep its decimals.
        text = format_num(value) if isinstance(value, int) and not isinstance(
            value, bool) else str(value)
        out.append(paint(" " + _pad(text, width), None, color))
        if label:
            out.append(paint(" " + label, DIM, color))
    return "".join(out).rstrip()



def clip(text, width):
    """A phrase cut to fit, at a word boundary.

    Truncating at a fixed column cut reasons mid-number, which is worse than a
    shorter reason: the reader cannot tell what was removed. A trailing
    parenthetical is a qualifier rather than the claim, so dropping it leaves a
    complete phrase and needs no ellipsis.
    """
    text = str(text)
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    tail = text[len(cut):].strip()
    if tail.startswith("(") and tail.endswith(")"):
        return cut
    return cut + "…"


def field(value, width=NUM_W, color=False):
    """A value in the block's number column. Counts get separators; everything —
    count, money, or phrase — shares the column's right edge so a block scans down."""
    if isinstance(value, int) and not isinstance(value, bool):
        text = format_num(value)
    else:
        text = str(value)
    return paint(_pad(text, width), BOLD, color)


def detail(label, value, tail=None, label_w=METRIC_W, color=False):
    """A sub-block readout row: number column shared across the block, qualifier in
    dim after it. This is how `regrettable drops 0 (enforced 0, shadow 0)` is built."""
    body = field(value, color=color)
    if tail is not None:
        body += paint("  (%s)" % tail, DIM, color)
    return row(label, body, label_w=label_w, color=color, styled=True)


def num(value, width=10, color=False):
    """A right-aligned, thousands-separated count — one column for every metric."""
    return paint(_pad(format_num(value), width), DIM, color)


def format_num(value):
    return "{:,}".format(int(value))


def pct(value, width=6, color=False):
    return paint(_pad("%.1f%%" % value, width), DIM, color)


def money(value, width=8, color=False):
    return paint(_pad("$%.2f" % value, width), DIM, color)


def unit(text, color=False):
    """A trailing unit is never louder than its number."""
    return paint(" " + str(text), DIM, color)


def state(kind, color=False):
    """The only coloured word in the toolchain, and it prints its own meaning as
    text — so a copy-paste into an issue keeps the verdict without the escapes."""
    kind = str(kind)
    return paint(kind, _CODES.get(kind), color)


def reason(text, indent=SUB_INDENT, color=False):
    """A blocking reason: the vocabulary's own marker, then the text hanging in the
    content column. Replaces the `·` bullet, which was also the header separator."""
    return (" " * indent + state(BLOCK, color) + " "
            + paint(str(text), None, color))


def warn(text, color=False):
    """A state that is not the one the reader wanted, in the surface's one accent.
    Used for `off` / `observation mode`, where the absence is the news."""
    return paint(str(text), AMBER, color)


def sub(title, indent=CONTENT, color=False):
    """A named sub-group inside a block, so a run of indented rows says what kind
    of thing they are. Dim, and one column left of the rows it names — the same
    relationship a `section` has to its table."""
    return " " * indent + paint(str(title), DIM, color)


def note(text, indent=CONTENT, color=False):
    """Prose belonging to the row above it, hanging in the content column."""
    return " " * indent + paint(str(text), DIM, color)


def prose(text, indent=CONTENT, width=76, color=False):
    """Wrap without introducing a frame: the grid's own indent is the only marker.

    Long tokens are never split. A wrapped path or flag is worse than a wide line —
    the reader copies the thing that ran long, and `subproto -grap h.json` is not a
    file. Lines that must stay on one line are the caller's job to keep short.
    """
    prefix = " " * indent
    chunks = _wrap(str(text), width=max(20, width - indent),
                   break_long_words=False, break_on_hyphens=False) or [""]
    return "\n".join(prefix + paint(c, DIM, color) for c in chunks)


def action(text, indent=SUB_INDENT, color=False):
    """A command to run — the thing the reader came for, so it stays bright."""
    return " " * indent + paint(str(text), None, color)
