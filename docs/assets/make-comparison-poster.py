#!/usr/bin/env python3
"""One image of every comparison this tree has measured, for a feed that shows no links.

The page needs a browser; a LinkedIn post needs a picture. So this composes the page's own
eight plates — same SVGs, same captions, same provenance stamps, extracted from the built
`_site/index.html` rather than redrawn — under a header whose four figures are read out of
`bench/launch_stats.json`. Nothing here is typed: a number that stopped being true in the
bench stops appearing in the image.

    python3 tools/make_site.py && python3 docs/assets/make-comparison-poster.py

Needs the Playwright CLI (`npx --no-install playwright`), a Chromium it can launch, and
Pillow to measure what came out. The output is not committed: it is a share artifact, and a
3 MB PNG in the tree is a claim that goes stale the moment the bench runs again.

    python3 docs/assets/make-comparison-poster.py --out ~/Downloads/linkdinpst/16-poster.png
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = os.path.join(ROOT, "_site", "index.html")
STATS = os.path.join(ROOT, "bench", "launch_stats.json")
DEFAULT_OUT = os.path.join(ROOT, "docs", "assets", "comparison-poster.png")
SCALE = 1                    # the canvas is already ~3x the plate's own width, so the
COLS = 3                     # plates land at their designed size rather than shrunk

# The five colours the plates themselves use. The poster borrows them rather than adding a
# sixth, so the header cannot look like a different document than the panels under it.
INK, MUTE, RULE, GOOD, BAD = ("#101317", "#5B6472", "#D5D9E0", "#1E7A34", "#B3261E")


def block(html, at, what):
    """The `<div>` that starts at offset `at`, with its divs balanced.

    A non-greedy regex stops at the first `</div>` inside the panel, and each plate has two.
    Counting the tokens is the difference between taking the whole figure and taking its
    scroll container.
    """
    depth, j = 1, at
    while True:
        nxt_open = html.find("<div", j + 1)
        nxt_close = html.find("</div>", j + 1)
        if nxt_close == -1:
            raise SystemExit("%s is never closed — the markup moved, not the design" % what)
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open
        else:
            j = nxt_close
            depth -= 1
            if depth == 0:
                return html[at:j + len("</div>")]


def plates(html):
    """The eight `.fig` panels of the figures section, in the page's own order."""
    section = re.search(r'<section class="sec" id="figures">.*?</section>', html, re.S)
    if not section:
        raise SystemExit("the built page has no #figures section")
    body = section.group(0)
    out, at = [], 0
    while True:
        at = body.find('<div class="fig">', at)
        if at == -1:
            return out
        out.append(block(body, at, "a figure panel"))
        at += len('<div class="fig">')


def headline(stats):
    """The four figures, each labelled with the arms it compares.

    `reduction_pct` is observe→compiled; the precision pair is per-slot→compiled, because
    that is the pair README quotes and the two must not disagree in a screenshot.
    """
    o = stats["overall"]
    return [
        (GOOD, "−%.1f%%" % o["reduction_pct"],
         "input tokens per task, observe → compiled",
         "%s → %s" % ("{:,.0f}".format(o["observe"]), "{:,.0f}".format(o["compiled"]))),
        (INK, "%d%% → %d%%" % (o["pass_rate"]["observe"], o["pass_rate"]["compiled"]),
         "task pass rate held at the same cut",
         "the gate is pass-rate, not tokens"),
        (INK, "%.1f%% → %.1f%%" % (o["precision"]["enforce"], o["precision"]["compiled"]),
         "precision of what survives, per-slot → compiled",
         "recall holds at %d%%" % o["recall"]["compiled"]),
        (INK, "$0",
         "API spend for every figure on this sheet",
         stats["corpus"]["upstream"]),
    ]


CSS = """
@page{margin:0}
*{box-sizing:border-box}
body{margin:0;background:#fff}
.poster{width:%(wide)dpx;background:#fff;color:%(ink)s;
  font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  padding:64px 56px 56px}
.kicker{font-size:13px;letter-spacing:.10em;text-transform:uppercase;color:%(mute)s}
h1{font-family:'Source Serif 4',Georgia,'Times New Roman',serif;font-weight:600;
  font-size:54px;line-height:1.1;letter-spacing:-.01em;margin:14px 0 0;max-width:17.5em}
.meta{font-size:13.5px;line-height:1.75;color:%(mute)s;margin:18px 0 0;max-width:82em}
.meta b{color:%(ink)s;font-weight:500}
.cost{font-family:'Source Serif 4',Georgia,'Times New Roman',serif;font-size:19px;
  line-height:1.55;color:%(ink)s;margin:16px 0 0;max-width:70em}
.cost b{font-weight:600}
.cost .lit{font-family:var(--mono);font-size:16px;letter-spacing:-.01em}
.rule{height:1px;background:%(rule)s;margin:40px 0 0}
.nums{display:grid;gap:0 40px;margin:36px 0 0}
.num{padding:0 0 0 18px;border-left:2px solid %(rule)s}
.num .v{font-family:'Source Serif 4',Georgia,'Times New Roman',serif;font-weight:600;
  font-size:44px;line-height:1.05;letter-spacing:-.01em}
.num .l{font-size:13px;line-height:1.5;color:%(ink)s;margin:8px 0 0}
.num .s{font-size:12px;line-height:1.5;color:%(mute)s;margin:3px 0 0}
.figs{display:grid;grid-template-columns:repeat(%(cols)d,minmax(0,1fr));
  gap:46px 40px;margin:44px 0 0}
.fig{margin:0;display:grid;grid-template-columns:minmax(0,1fr);row-gap:14px;align-items:start}
.figscroll{background:#fff;overflow:hidden}
.figscroll svg{display:block;width:100%%;height:auto}
.cap{font-family:'Source Serif 4',Georgia,'Times New Roman',serif;font-size:17px;
  line-height:1.5;color:%(ink)s;margin:0;padding-left:16px;border-left:2px solid %(rule)s}
.stamp{font-size:11px;line-height:1.6;color:%(mute)s;margin:0;padding-left:16px;
  border-left:2px solid %(rule)s}
.stamp b{color:%(mute)s;font-weight:500}
.stamp b:first-child{color:%(bad)s}
.foot{font-size:12px;line-height:1.7;color:%(mute)s;margin:44px 0 0;max-width:82em}
.foot b{color:%(ink)s;font-weight:500}
:root{--mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace}
"""


def build(html, stats, width):
    faces = "\n".join(re.findall(r"@font-face\s*{[^}]*}", html))
    if "Source Serif 4" not in faces:
        raise SystemExit("the built page carries no @font-face for its own display face")
    fmt = {"wide": width, "ink": INK, "mute": MUTE, "rule": RULE, "bad": BAD, "cols": COLS}
    nums = "".join(
         "<div class='num'><div class='v' style='color:%s'>%s</div>"
         "<div class='l'>%s</div><div class='s'>%s</div></div>" % t
         for t in headline(stats))
    o, c, r = stats["overall"], stats["corpus"], stats["regret"]
    head = (
        "<div class='kicker'>Limen &middot; subproto &mdash; a System One decision layer "
        "for coding agents</div>"
        "<h1>What comes off a coding agent&rsquo;s prompt, and what it costs.</h1>"
        "<p class='meta'><b>%s</b> on <b>%s</b>, python %s, %s tasks &times; %s repeats, "
        "seed %s, commit <b>%s</b>. Every plate below keeps its own stamp, and the "
        "dollars are the in-repo mock&rsquo;s own tariff.</p>"
        "<p class='cost'>Two costs the run prints rather than hides: the parity gate "
        "reports <span class='lit'>%s</span>, and enforcing the cut billed back <b>%s tokens</b> on <b>%d "
        "regrettable drops</b> across the %d requests that had decisions.</p>"
        % (esc(stats["command"]), esc(stats["host"]["platform"]), esc(stats["host"]["python"]),
           c["n"], c["iters"], c["seed"], esc(stats["git_sha"]),
           esc(stats["s32"]["text"].strip()), "{:,}".format(r["refetch_tok_paid"]),
           r["regrettable_drops"], r["requests_with_decisions"]))
    return ("<!doctype html><html><head><meta charset='utf-8'><style>%s</style>"
            "<style>%s</style></head><body><div class='poster' style='transform:scale(%d);"
            "transform-origin:0 0'>%s<div class='rule'></div>"
            "<div class='nums' style='grid-template-columns:repeat(%d,minmax(0,1fr))'>%s"
            "</div><div class='figs'>%s</div><p class='foot'>%s</p></div></body></html>") % (
        faces, CSS % fmt, SCALE, head, len(headline(stats)), nums,
        "\n".join(plates(html)),
        esc("Reproduced by one command on a clone: `pip install subproto && subproto demo "
            "--slots`, then `python3 bench/launch_stats.py --iters 5`. No key, no network "
            "beyond the provider you point it at. The page this sheet is cut from: ")
        + "<b>https://aashish254.github.io/limen/</b>")


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=3060,
                    help="CSS px of the poster canvas; the raster is this times %d" % SCALE)
    args = ap.parse_args()
    if not os.path.isfile(SITE):
        raise SystemExit("%s is missing — run python3 tools/make_site.py first" % SITE)
    html = open(SITE).read()
    stats = json.load(open(STATS))
    page = build(html, stats, args.width)
    tmp = tempfile.mkdtemp(prefix="limen-poster-")
    try:
        os.symlink(os.path.join(ROOT, "_site", "assets"), os.path.join(tmp, "assets"))
        doc = os.path.join(tmp, "poster.html")
        with open(doc, "w") as f:
            f.write(page)
        subprocess.run(
            ["npx", "--no-install", "playwright", "screenshot", "--full-page",
             "--viewport-size=%d,%d" % (args.width * SCALE, 900 * SCALE),
             "file://" + doc, args.out], check=True, capture_output=True)
        from PIL import Image, ImageChops
        im = Image.open(args.out).convert("RGB")
        bg = Image.new("RGB", im.size, im.getpixel((2, 2)))
        box = ImageChops.difference(im, bg).convert("L").point(
            lambda v: 255 if v > 8 else 0).getbbox()
        if box:
            # Trim the empty band below the sheet, which is the viewport guess. Horizontally
            # nothing is cut: the canvas's own padding is part of the composition, and a
            # bbox crop would leave the left margin and eat the right one.
            im = im.crop((0, 0, im.width, min(im.height, box[3] + 56 * SCALE)))
        im.save(args.out)
        print("%-34s %5dx%-5d px  %d KB  %d plates"
              % (os.path.basename(args.out), im.width, im.height,
                 os.path.getsize(args.out) // 1024, len(plates(html))))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
