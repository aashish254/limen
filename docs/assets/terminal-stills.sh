#!/usr/bin/env bash
# How docs/assets/terminal/*.png were made, so a reader can tell a real session from a
# mockup: every pixel is a terminal that ran the installed wheel and printed what it
# printed. Nothing here types a page in by hand, and nothing here edits one after the
# fact — the only post-processing is choosing which frame is the still.
#
# Needs: the wheel on PATH (pip install . into a venv), asciinema 3.x, agg, Pillow.
#
#   VENV=$HOME/.venvs/subproto bash docs/assets/terminal-stills.sh
#
# The prices on these pages are the in-repo mock's own tariff at $0 of API spend; the
# caption beside each image says so. A screenshot of a number nobody ran is the thing
# this project is not.
set -euo pipefail

VENV="${VENV:-$HOME/.venvs/subproto}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/docs/assets/terminal"
WORK="$(mktemp -d /tmp/lm-XXXX)"
FONT="${FONT:-20}"
THEME="${THEME:-github-dark}"

# name:cols:commands — one line per still, commands split on `|`. A command written with a
# leading `@` is set-up: it runs, prints nothing, and is not photographed, so a `show`
# can be taken against a session that exists without the roll spending 40 rows on demo.
#
# The grid is 100 columns, because that is the design's measure and a still wider than the
# page would be a still of a different product. `show` is the one surface that risks
# overflowing it: it prints the stored body's path so the reader can open it, and a path
# that wraps is a path nobody copies. The capture solves that by keeping the scratch home
# short — /tmp/lm-XXXX/show/.subproto/... — rather than by widening the canvas.
# The row count is not a choice either: each page is measured first and the terminal is
# given exactly that many rows, so nothing scrolls out of the frame and the still is the
# whole answer rather than the end of it.
SURFACES=(
  "doctor:100:subproto --version|subproto doctor --port 0"
  "demo-slots:100:subproto demo --slots"
  "report:100:@subproto demo|subproto report"
  "show-compiled:100:@subproto demo --slots|@SUBPROTO_APPLY=tool_gate,compact SUBPROTO_COMPILE=on subproto demo --slots|subproto show 3"
  "live-meter:100:@subproto demo|subproto live --once"
)

env_for() {
    export HOME="$WORK/$1"
    export SUBPROTO_HOME="$HOME/.subproto"
    export PATH="$VENV/bin:$PATH"
    export TERM=xterm-256color LINES="${rows:-24}" COLUMNS="${cols:-100}"
    mkdir -p "$SUBPROTO_HOME"
}

# One pass counts the lines the pages produce, the other draws them. Same commands, same
# grid, so the count cannot disagree with the picture.
run_surface() {
    local spec="$1" cmds="$2" measure="${3:-}"
    local rows="${4:-}" cols="${5:-100}"
    local IFS='|'
    # shellcheck disable=SC2206
    local parts=($cmds)
    env_for "$spec"
    local total=0 c
    for c in "${parts[@]}"; do
        if [ "${c:0:1}" = "@" ]; then
            eval "${c:1}" >/dev/null 2>&1
            [ -n "$measure" ] || clear
            continue
        fi
        if [ -n "$measure" ]; then
            total=$((total + 2 + $( { eval "$c"; } 2>&1 | wc -l | tr -d ' ')))
            continue
        fi
        printf '\n$ %s\n' "$c"
        sleep 0.4
        eval "$c"
        sleep 1.6
    done
    [ -n "$measure" ] && echo "$total"
}

if [ "${1:-}" = "roll" ]; then run_surface "$2" "$5" "" "$3" "$4"; exit 0; fi
if [ "${1:-}" = "measure" ]; then run_surface "$2" "$5" measure x "$4"; exit 0; fi

mkdir -p "$OUT"
for spec in "${SURFACES[@]}"; do
    name="${spec%%:*}"; rest="${spec#*:}"
    cols="${rest%%:*}"; cmds="${rest#*:}"
    rows="$(COLUMNS="$cols" bash "$0" measure "$name" x "$cols" "$cmds" | tail -1)"
    cast="$WORK/$name.cast"
    # `script` gives the recorder a pty, so the pages decide colour the way they do in a
    # real terminal; a capture of the plain page would prove nothing about the design.
    script -q /dev/null asciinema rec --overwrite --idle-time-limit 0.4 \
        --command "bash $0 roll $name $rows $cols \"$cmds\"" "$cast" >/dev/null 2>&1
    # The headless recorder cannot read a window size, so the cast header says 0x0 and the
    # renderer is told the grid the content was actually laid out for.
    python3 - "$cast" "$rows" "$cols" "$WORK/$name-sized.cast" <<'PY'
import json, sys
src, rows, cols, dst = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
lines = open(src).read().splitlines()
head = json.loads(lines[0])
head["term"] = {"cols": cols, "rows": rows, "type": "xterm-256color"}
head["width"], head["height"] = cols, rows
open(dst, "w").write(json.dumps(head) + "\n" + "\n".join(lines[1:]) + "\n")
PY
    agg -q --theme "$THEME" --font-size "$FONT" --line-height 1.4 --cols "$cols" --rows "$rows" \
        --idle-time-limit 0.4 "$WORK/$name-sized.cast" "$WORK/$name.gif"
    # The last frame is the settled page: everything printed, nothing mid-scroll.
    python3 - "$WORK/$name.gif" "$OUT/$name.png" <<'PY'
import sys
from PIL import Image, ImageSequence
gif, dst = sys.argv[1], sys.argv[2]
frames = [f.convert("RGB") for f in ImageSequence.Iterator(Image.open(gif))]
frames[-1].save(dst)
print("%-18s %s" % (dst.split("/")[-1], "x".join(map(str, frames[-1].size))))
PY
done

echo "themes: $THEME at font-size $FONT, from a wheel installed in $VENV"
rm -rf "$WORK"
