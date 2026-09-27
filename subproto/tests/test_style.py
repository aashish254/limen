"""T37 — the design layer, checked on every surface instead of one command's copy.

Four things make a terminal page look generated, and each is a rule this file
holds the whole toolchain to:

* **a frame** — a `+---+` or `│` box wraps into noise at 60 columns, and is the
  single most reliable tell of a page nobody designed;
* **hue as decoration** — colour must mean something, so green/amber/red are legal
  on exactly one thing: a state word. (Dim and bold are structure, not colour —
  they say *qualifier* and *value*.)
* **alignment that drifts** — every figure in a block has to land on one column,
  or the block is a list of sentences rather than a readout;
* **a line nobody asked for** — a wide row at the end of a table is a table, but a
  *sentence* may only run long when it is a path or a command, because those are
  the two things a reader copies out.

A fifth rule is about this file: every subcommand either renders into `PAGES` or sits
in `NOT_PAGES` with a reason, so a new surface cannot slip past the design layer by
going unmentioned.

Each surface is rendered twice, once with colour forced on, and the coloured text
must reduce to the plain text character for character under `strip`. That one
invariant catches the styling bugs that are invisible on a screen: a value wrapped
in a second bold, a unit painted bright, a row whose escapes end early.
"""

import argparse
import contextlib
import io
import os
import re
import subprocess
import sys

import pytest

from subproto import cli, graph as graph_mod, live, style

from test_learn import _re_read_home

# Box-drawing and rule lines. A hyphen inside a flag (`--dry-run`) is not a frame;
# a row of four or more rule characters is.
BOX = re.compile(u"[┌┐└┘├┤┬┴┼─│═║╔╗╚╝]")
RULE = re.compile(r"^\s*[+\-=_|]{4,}\s*$")
# The two shapes a reader copies, and so the two allowed to run past the margin.
COPYABLE = re.compile(r"\S/|\.json|python|^.*--\w")
MAX_W = 100

# The vocabulary, spelled out of the constants so a surface inventing a sixth word
# for "no" fails here rather than in a screenshot.
STATES = (style.OK, style.UP, style.YES, style.READY, style.PRESENT,
          style.INSTALLED, style.DELIVERED, style.POTENTIAL, style.WARN,
          style.SHORT, style.OFF, style.NOT_READY, style.DOWN, style.MISSING,
          style.BLOCK)
HUES = ("31", "32", "33")           # red, green, amber — and nothing else
ESCAPES = re.compile(r"\033\[([0-9;]*)m([^\033]*)")
# The one field on a page that is genuinely a clock, masked by the reproducibility
# check below rather than worked around by picking only clock-free pages.
CLOCK = re.compile(r"read at [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8}")

REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tests", "fixtures", "sample_repo")

# (surface, argv) for every command that prints a page. `inject` prints env vars for
# `eval`, so it is checked for frames and width but not for a header.
PAGES = {
    "report": ("report",),
    "learn": ("learn", "--dry-run"),
    "models": ("models",),
    "live": ("live", "--once"),
    "retrain": ("retrain",),
    "label": ("label", "1", "tool_gate", "good"),
    "where": ("where", "fix the retry backoff"),
    "compile": ("compile", "fix the retry backoff in payments"),
    "show": ("show", "1"),
    "inject": ("inject", "claude"),
}

# Two of those surfaces are receipts, not pages: `inject` prints shell exports for
# `eval`, and `label` confirms one write. They are still held to the grid, the hue
# rule and the margin — but a header on a line you are about to paste into a script
# is noise, so the header rule names them instead of ignoring them.
RECEIPTS = ("inject", "label")

# The commands PAGES does not render, each with the reason. This table exists so that
# adding a subcommand forces a decision about it: either it gets a page here, or it
# gets a reason. An unlisted command fails the test below.
NOT_PAGES = {
    "up": "starts a listener and blocks; the pages it feeds are covered by the proxy e2e suite",
    "demo": "replays traffic, then prints the report page, which is in PAGES",
    "graph": "writes an index and prints one count",
    "audit": "writes the audit JSON; its stdout is that document",
    "export": "writes a JSONL file and prints where it went",
    "split": "writes train/val and prints the counts",
    "doctor": "names the machine — a real interpreter path, a port just chosen — so it "
              "cannot render identically twice; its grid is asserted in test_doctor.py",
}


def test_every_command_is_a_page_or_carries_a_reason():
    sub = [a for a in cli.build_parser()._actions
           if isinstance(a, argparse._SubParsersAction)][0]
    missing = sorted(set(sub.choices) - set(PAGES) - set(NOT_PAGES))
    assert not missing, "new subcommand with no style decision: %s" % missing
    for name, argv in PAGES.items():
        assert name in sub.choices, "PAGES renders %r, which the parser has no such"
    for name, why in NOT_PAGES.items():
        assert name in sub.choices, "exempted command no longer exists: %s" % name
        assert len(why) > 30, "%s is exempted without a real reason" % name


def _run(home, color, name, argv, repo_graph=None):
    """One surface's text, with colour decided by the test rather than the pipe."""
    args = list(argv)
    if name in ("where", "compile"):
        args += ["--graph", repo_graph]
    buf = io.StringIO()
    real = style.enabled
    style.enabled = lambda stream=None, force=None: color
    try:
        with contextlib.redirect_stdout(buf):
            assert cli.main(args + ["--home", str(home)]) == 0, args
    finally:
        style.enabled = real
    return buf.getvalue()


def _pages(home, repo_graph, color):
    return dict((name, _run(home, color, name, argv, repo_graph))
                for name, argv in PAGES.items())


@pytest.fixture
def home(tmp_path):
    """A real two-turn session whose second turn re-reads what the first one cut,
    so regret, versions and the harvest all have something to print."""
    config, telemetry = _re_read_home(tmp_path)
    telemetry.close()
    return tmp_path / "home"


@pytest.fixture
def repo_graph(tmp_path):
    target = str(tmp_path / "sample-graph.json")
    graph_mod.save(graph_mod.build(REPO), target)
    return target


@pytest.fixture
def pages(home, repo_graph):
    out = _pages(home, repo_graph, False)
    empty = sorted(k for k, v in out.items() if not v.strip())
    assert not empty, "these surfaces printed nothing: %s" % empty
    return out


# ---------------------------------------------------------------- the colour gate

class _Tty(object):
    def isatty(self):
        return True


class _Pipe(object):
    def isatty(self):
        return False


def test_colour_needs_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    assert style.enabled(_Pipe()) is False
    assert style.enabled(_Tty()) is True


def test_no_color_and_a_dumb_term_kill_it(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert style.enabled(_Tty()) is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert style.enabled(_Tty()) is False


def test_an_explicit_flag_outranks_every_heuristic(monkeypatch):
    """`--color` in CI is how a page gets screenshotted; `--no-color` in a terminal
    is how it gets pasted. Both must beat isatty, and beat NO_COLOR."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert style.enabled(None, force=True) is True
    assert style.enabled(None, force=False) is False


def test_the_flag_reaches_the_page(home):
    """`--color` has to survive the whole way down to the paint: pytest's stdout is
    a pipe, so a capture run in CI would otherwise print the plain page and nobody
    would notice until the GIF came out grey. Nothing is monkeypatched here — the
    flag is the only thing deciding, exactly as in the terminal."""
    def page(*extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cli.main(["report", "--home", str(home)] + list(extra)) == 0
        return buf.getvalue()

    assert "\033[" not in page()                  # a pipe, no flag: plain
    assert "\033[" in page("--color")             # the flag outranks the pipe
    assert "\033[" not in page("--color", "--no-color")
    # And a forced answer for one command is not the answer for the next one.
    assert "\033[" not in page()


# ---------------------------------------------------------------- the vocabulary

def test_every_state_word_is_spelled_as_well_as_coloured():
    """A state has to survive a paste into an issue with no escapes in it, so the
    word carries the meaning and the hue only repeats it."""
    for kind in STATES:
        assert style.strip(style.state(kind, color=True)) == kind


def test_an_unknown_state_gets_no_colour():
    """Two pages using two words for the same situation is how a toolchain starts
    disagreeing with itself; the new word prints uncoloured until it is adopted."""
    assert style.state("unavailable", color=True) == "unavailable"
    assert style.state(style.MISSING, color=True) != style.MISSING


def test_hue_is_only_ever_on_a_state_word(home, repo_graph):
    """Walking every escape with the text under it is what turns "colour means
    state" from a comment into a check."""
    seen = set()
    for name, page in _pages(home, repo_graph, True).items():
        for code, payload in ESCAPES.findall(page):
            if not any(h in code.split(";") for h in HUES):
                continue
            word = payload.strip()
            if word in STATES:
                seen.add(word)
                continue
            raise AssertionError("%s gave a hue to %r (%s)" % (name, payload, code))
    assert len(seen) >= 3, "the check above saw no states: %s" % seen


# ---------------------------------------------------------------- the grid

def test_stripping_the_coloured_page_gives_the_plain_page(home, repo_graph):
    """The strongest single check here: hue must be additive. A value wrapped
    twice, or a unit painted bright, makes the two pages diverge.

    The two passes are rendered a second apart, so the harvest clock is masked on both
    sides — a wall clock crossing a second is not a divergence in the paint, and the
    page that is allowed to move is already named by `test_a_page_is_the_same_page_twice`.
    """
    plain = dict((name, CLOCK.sub("<clock>", text))
                 for name, text in _pages(home, repo_graph, False).items())
    for name in PAGES:
        painted = CLOCK.sub("<clock>", _run(home, True, name, PAGES[name], repo_graph))
        assert style.strip(painted) == plain[name], name


def test_a_page_is_the_same_page_twice(home, repo_graph):
    """Reproducibility is the claim this tool makes, and a witness page that
    reshuffles its own rows between two runs cannot make it. The one field allowed
    to move is the harvest clock, so it is masked rather than the check narrowed."""
    for name in ("compile", "report", "learn"):
        first = CLOCK.sub("<clock>", _run(home, False, name, PAGES[name], repo_graph))
        again = CLOCK.sub("<clock>", _run(home, False, name, PAGES[name], repo_graph))
        assert first == again, name


def test_a_block_of_rows_shares_one_number_column():
    rows = [style.row(label, style.field(n, width=7), label_w=16)
            for label, n in (("billed input", 1000), ("cached input", 816000),
                             ("output", 42))]
    # Right edges flush at column 25, which is the only way a readout scans downward.
    assert [r[-7:] for r in rows] == ["  1,000", "816,000", "     42"], rows


def test_identifiers_left_align_and_numbers_do_not():
    left = style.table("compact", ("app/services/retry.py", "", -24), name_w=9)
    right = style.table("compact", (4200, "tok", 6), name_w=9)
    assert left == "    compact   app/services/retry.py", left
    assert right == "    compact    4,200 tok", right


def test_a_phrase_is_cut_at_a_word_and_a_qualifier_needs_no_ellipsis():
    claim = "value/token 0.002559 < kept floor 0.002577 (floor set by tool_result#9)"
    assert style.clip(claim, 46) == "value/token 0.002559 < kept floor 0.002577"
    assert style.clip("the optimiser ran out of budget before the last turn",
                      24) == "the optimiser ran out…"


# ---------------------------------------------------------------- no frames

def test_no_surface_draws_a_box_or_a_rule(pages):
    for name, text in pages.items():
        for line in text.splitlines():
            assert not BOX.search(line), "%s: %r" % (name, line)
            assert not RULE.match(line), "%s: %r" % (name, line)


def test_no_line_runs_wide_unless_it_is_meant_to_be_copied(pages):
    for name, text in pages.items():
        for line in text.splitlines():
            if len(line) <= MAX_W or COPYABLE.search(line):
                continue
            raise AssertionError("%s prints a sentence nobody asked for: %r"
                                 % (name, line))


def test_the_live_meter_kept_its_number_and_lost_its_box():
    snap = {"n": 42, "in_tok": 100000, "cost_usd": 1.02, "saved_tok": 38000,
            "saved_pct": 38.0, "after_usd": 0.63, "mode": "delivered"}
    text = live.render(snap, color=True)
    assert not BOX.search(text)
    assert style.strip(text) == live.render(snap, color=False)
    assert "38%" in text and "38,000" in text and "$0.63" in text


# ---------------------------------------------------------------- one header form

def test_every_page_opens_with_the_command_it_was_called_as(pages):
    for name, text in pages.items():
        if name in RECEIPTS:
            continue
        first = style.strip(text.splitlines()[0])
        assert first.startswith("subproto %s —" % name), "%s: %r" % (name, first)


def test_a_receipt_is_one_line_plus_the_file_it_touched(pages):
    """A confirmation is not a page: it names the thing it did, points at the file,
    and stops. Growing a header onto it is the slop this check keeps out."""
    for name in RECEIPTS:
        lines = [l for l in pages[name].splitlines() if l.strip()]
        assert len(lines) <= 2, "%s: %r" % (name, lines)


def test_a_header_carries_exactly_one_separator(pages):
    """`—` means *term, then its qualifier*. A header with two of them cannot be
    read that way, which is why further metadata is a comma-separated list."""
    for name, text in pages.items():
        if name in RECEIPTS:
            continue
        first = style.strip(text.splitlines()[0])
        assert first.count(" — ") == 1, "%s: %r" % (name, first)


def test_the_command_name_leads_its_header(home, repo_graph):
    """Bold is the grid's way in: on the first line the bright run is exactly the
    command, and the metadata behind the dash is dim."""
    line = _run(home, True, "report", PAGES["report"]).splitlines()[0]
    runs = ESCAPES.findall(line)
    assert [p for c, p in runs if c == "1"] == ["subproto report"], line
    assert [p for c, p in runs if c == "2"][0].strip() in ("—", "— "), line


def test_a_console_that_cannot_encode_the_page_still_gets_one(tmp_path):
    """The stream is part of the design layer, and it is not this tool's to choose.

    Every page here opens with an em dash and can print back a string the user
    typed — a version name, a path out of their repository. On a cp1252 console
    (Windows' default, or `LANG=C` in a container) a Devanagari name raises
    UnicodeEncodeError *inside print*, so the command dies after it had already
    worked out the answer. This runs a real child under a real hostile
    `PYTHONIOENCODING`, because the defect lives in the stream and no in-process
    capture can see it: pytest's own stdout is UTF-8 whatever this machine says.
    """
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    home = str(tmp_path / "home")
    name = "personal-चरण"                     # not a character cp1252 owns

    def child(*argv):
        return subprocess.run([sys.executable, "-m", "subproto"] + list(argv),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=root, env=env)

    added = child("models", "--home", home, "--add", name, "--url", "http://127.0.0.1:1")
    assert added.returncode == 0, added.stderr
    assert "Traceback" not in added.stdout, added.stdout
    shown = child("models", "--home", home)
    assert shown.returncode == 0, shown.stderr
    assert name in shown.stdout, shown.stdout
