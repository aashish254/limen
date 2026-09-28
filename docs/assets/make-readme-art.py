#!/usr/bin/env python3
"""Regenerate the two images the README embeds.

Both are crops of the built Pages page, so neither can drift from what the site shows:
`logo.png` is the page's own brand mark — the glyph alone, because the README's `# Limen`
heading is the wordmark and a page does not need the name twice — and `results-grid.png` is
the page's figures section, same eight plates, same order, same captions and same
provenance stamps, at the width the README column actually renders at.

    python3 tools/make_site.py && python3 docs/assets/make-readme-art.py

Needs the Playwright CLI (`npx --no-install playwright`), a Chromium it can launch, and
Pillow to report what came out.
"""

import os
import re
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = os.path.join(ROOT, "_site", "index.html")
OUTDIR = os.path.join(ROOT, "docs", "assets")
SCALE = 2                      # rasterised at 2x so the charts read at README width

# The page is one dark canvas; the art pages keep it and only re-flow the grid, because a
# plate is 976 units wide, sits in a horizontal scroller on the page, and is stacked with
# its caption rather than side-by-side with it at this width.
OVERRIDE = """
body{margin:0}
.art{width:%(width)dpx;background:var(--ink)}
.figs{grid-template-columns:minmax(0,1fr)}
.fig{grid-template-columns:minmax(0,1fr)}
.figscroll{overflow:visible}
.figscroll svg{width:100%%;height:auto}
.sec{padding:44px 0 20px}
"""


def read_page():
    if not os.path.isfile(SITE):
        raise SystemExit("%s is missing — run python3 tools/make_site.py first" % SITE)
    return open(SITE).read()


def piece(html, pattern, what):
    m = re.search(pattern, html, re.S)
    if not m:
        raise SystemExit("the built page no longer contains %s: the markup moved, "
                         "not the design" % what)
    return m.group(0)


MARK_SIDE = 168                # the tile, in CSS px — GitHub shows intrinsic px, so this is
MARK_GLYPH = 140               # what it displays at; the glyph fills ~70% of the tile
MARK_RADIUS = 13               # CSS px, and the PIL mask uses the same radius times SCALE


def shoot(tmp, name, body, width, height=100, margin=28, crop=True, tile=None):
    """Render `body` on a `width` x `height` CSS canvas, scaled by SCALE, to docs/assets.

    `crop` measures the art off the render (whatever is not the page's own ink) so nothing
    has to be hand-tuned. `tile` skips that and cuts the square canvas loose on a transparent
    rounded card instead, for the case where the canvas *is* the composition.
    """
    doc = os.path.join(tmp, name[:-4] + ".html")
    page = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<style>%s</style><style>%s</style></head>"
            "<body><div class='art' style='transform:scale(%d);transform-origin:0 0'>"
            "%s</div></body></html>") % (CSS, OVERRIDE % {"width": width}, SCALE, body)
    with open(doc, "w") as f:
        f.write(page)
    out = os.path.join(OUTDIR, name)
    subprocess.run(
        ["npx", "--no-install", "playwright", "screenshot", "--full-page",
         "--viewport-size=%d,%d" % (width * SCALE, height * SCALE), "file://" + doc, out],
        check=True, capture_output=True)
    from PIL import Image
    im = Image.open(out).convert("RGB")
    if crop:
        # The viewport is a guess at the canvas; the art is whatever is not the page's own
        # ink, so the crop is measured off the render rather than off a hand-tuned width.
        from PIL import ImageChops
        bg = Image.new("RGB", im.size, im.getpixel((2, 2)))
        box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 8 else 0).getbbox()
        if box:
            m = margin * SCALE
            im = im.crop((max(0, box[0] - m), max(0, box[1] - m),
                          min(im.width, box[2] + m), min(im.height, box[3] + m)))
    if tile:
        # A square of flat ink on GitHub's near-black canvas reads as a missing image. The
        # corners go transparent and a hairline follows the same radius, so it reads as a
        # card that was drawn. The mask is cut at 4x so its edge is antialiased.
        from PIL import ImageDraw
        side = tile * SCALE
        mask = Image.new("L", (side * 4, side * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, side * 4 - 1, side * 4 - 1],
                                               radius=MARK_RADIUS * SCALE * 4, fill=255)
        im = im.resize((side, side), Image.LANCZOS)
        im.putalpha(mask.resize((side, side), Image.LANCZOS))
        im = im.resize((tile, tile), Image.LANCZOS)
    im.save(out)
    print("%-20s %5dx%-5d px  %s" % (name, im.width, im.height,
                                     "%d KB" % (os.path.getsize(out) // 1024)))


html = read_page()
CSS = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
brand = piece(html, r'<a class="brand".*?</a>', "the nav brand band")
mark = piece(brand, r"<svg.*?</svg>", "the brand mark inside it")
section = piece(html, r'<section class="sec" id="figures">.*?</section>',
                "the figures section")

tmp = tempfile.mkdtemp(prefix="limen-art-")
try:
    # The page pulls its two faces from assets/fonts relative to itself; mirror that so
    # the art is set in the same families rather than a fallback.
    os.symlink(os.path.join(ROOT, "_site", "assets"), os.path.join(tmp, "assets"))
    shoot(tmp, "logo.png",
          "<style>.art{border-radius:%dpx;box-shadow:inset 0 0 0 1px rgba(242,240,235,.14)}"
          ".mark svg{width:%dpx;height:%dpx;display:block}</style>"
          "<div class='mark' style='display:flex;align-items:center;justify-content:center;"
          "height:%dpx'>%s</div>" % (MARK_RADIUS, MARK_GLYPH, MARK_GLYPH, MARK_SIDE, mark),
          MARK_SIDE, height=MARK_SIDE, crop=False, tile=MARK_SIDE)
    shoot(tmp, "results-grid.png", section, 1040)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
