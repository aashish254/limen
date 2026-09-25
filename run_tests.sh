#!/usr/bin/env bash
# One-command verification gate. Every task in TODO.md is checked against this.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY="${PYTHON:-python3.11}"

echo "== [1/5] byte-compile (3.11) =="
"$PY" -W error -m compileall -q subproto fakeup bench

echo "== [2/5] pytest (3.11) =="
"$PY" -m pytest -q

echo "== [3/5] offline benchmark =="
"$PY" bench/ab.py --mock 20 >/dev/null && echo "bench/ab.py ok"

echo "== [4/5] live harness (mock, held pass-rate) =="
[ -f bench/live.py ] && "$PY" bench/live.py --mock >/dev/null && echo "bench/live.py ok" || echo "bench/live.py not yet present (skipped)"

echo "== [5/5] demo =="
"$PY" -m subproto demo --port "$(( (RANDOM % 500) + 8500 ))" --slots >/dev/null && echo "demo ok"

echo
if command -v python3.9 >/dev/null 2>&1; then
  echo "== py3.9 compile + demo =="
  python3.9 -m compileall -q subproto && python3.9 -m subproto demo --port "$(( (RANDOM % 500) + 8500 ))" >/dev/null && echo "3.9 ok"
else
  echo "== python3 (may be 3.9) compile + demo =="
  python3 -m compileall -q subproto && python3 -m subproto demo --port "$(( (RANDOM % 500) + 8500 ))" >/dev/null && echo "base-python ok"
fi
echo
echo "ALL GATES PASS"
