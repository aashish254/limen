#!/usr/bin/env bash
# Smoke test for a *packaged* install of subproto (wheel or sdist), run from a
# temp dir with an isolated SUBPROTO_HOME. Never touches the repo checkout.
#
# Usage:  tools/check_install.sh            # expects `subproto` on PATH
#         PATH=/path/to/venv/bin:$PATH tools/check_install.sh
set -u

PORT="${1:-0}"
if [ "$PORT" = "0" ]; then
  PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1]); s.close()')
fi
export SUBPROTO_HOME=$(mktemp -d)
trap 'rm -rf "$SUBPROTO_HOME"' EXIT

fail=0
run() {  # run <label> <cmd...>
  local label="$1"; shift
  echo "== $label"
  if ! "$@"; then echo "!! $label FAILED"; fail=1; fi
  echo
}

if ! command -v subproto >/dev/null 2>&1; then
  echo "!! 'subproto' not on PATH — install the wheel first:"
  echo "   python3 -m venv /tmp/sp && /tmp/sp/bin/pip install --no-index --find-links dist subproto"
  echo "   PATH=/tmp/sp/bin:\$PATH tools/check_install.sh"
  exit 1
fi

run "--help"        subproto --help
run "demo --slots"  subproto demo --port "$PORT" --slots
run "report"        subproto report

# `--port 0` asks the kernel for a free port instead of testing the default 8787.
# A machine already running `subproto up` has a working install, and doctor
# correctly exits 1 on a port it cannot bind — which is not this script's failure.
run "doctor" subproto doctor --port 0

exit "$fail"
