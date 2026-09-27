#!/usr/bin/env bash
# How docs/assets/first-30-seconds.gif was made — so a reader knows it is a real
# terminal session and not a mockup. The .cast beside it replays it with
# `asciinema play first-30-seconds.cast`.
#
# Needs: the installed wheel on PATH (`pip install .` into a venv), asciinema 3.x, agg.
# The three commands are exactly the README's quick start.
set -euo pipefail

VENV="${VENV:-$HOME/.venvs/subproto}"      # the install under test
HOME_DIR="${HOME_DIR:-/tmp/subproto-hero-home}"
TERM_ROWS=26 TERM_COLS=100

roll() {
    export HOME="$HOME_DIR"
    export SUBPROTO_HOME="$HOME/.subproto"
    export PATH="$VENV/bin:$PATH"
    export TERM=xterm-256color LINES=$TERM_ROWS COLUMNS=$TERM_COLS
    stty rows $TERM_ROWS cols $TERM_COLS 2>/dev/null || true
    mkdir -p "$SUBPROTO_HOME"
    clear
    step() { printf '\n$ %s\n' "$*"; sleep 0.5; "$@"; sleep 2.4; clear; }
    step subproto --version
    step subproto doctor --port 0
    step subproto demo --slots
    printf '$ subproto live --once\n'
    sleep 0.5
    subproto live --once
}

if [ "${1:-}" = "roll" ]; then roll; exit 0; fi

# `script` gives the recorder a pty, so the pages decide colour the way they do in a
# real terminal — a capture of the plain page would prove nothing about the design.
script -q /dev/null asciinema rec --overwrite --idle-time-limit 0.6 \
    --command "bash $0 roll" /tmp/hero.cast
# The headless recorder cannot read a window size, so the cast header says 0x0; the
# content was laid out for 100x26, and that is what the renderer is told.
python3 - /tmp/hero.cast <<'PY'
import json, sys
lines = open(sys.argv[1]).read().splitlines()
head = json.loads(lines[0])
head["term"] = {"cols": 100, "rows": 26, "type": "xterm-256color"}
open("/tmp/hero-sized.cast", "w").write(json.dumps(head) + "\n" + "\n".join(lines[1:]) + "\n")
PY
agg -q --theme github-light --font-size 15 --line-height 1.35 \
    --font-family "SF Mono,Menlo,DejaVu Sans Mono" --cols 100 --rows 26 \
    --idle-time-limit 0.6 /tmp/hero-sized.cast /tmp/hero.gif
# One row of the empty bottom margin is cropped; the tallest page in the roll needs 25.
python3 - /tmp/hero.gif <<'PY'
import sys
from PIL import Image, ImageSequence
im = Image.open(sys.argv[1])
row = (im.height - 2) // 26
frames = [f.convert("RGB").crop((0, 0, im.width, im.height - row))
          for f in ImageSequence.Iterator(im)]
durs = [max(f.info.get("duration", 100), 60) for f in ImageSequence.Iterator(im)][:len(frames)]
frames[0].save("first-30-seconds.gif", save_all=True, append_images=frames[1:],
               duration=durs, loop=0, optimize=True)
PY
