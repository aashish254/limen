#!/usr/bin/env bash
# One-command verification gate. Every task in TODO.md is checked against this.
# Runs the whole suite + all benches under BOTH 3.11 and (when present) 3.9, then
# the demo. Exits non-zero on the first failure (set -e). No API key, no spend.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY="${PYTHON:-python3.11}"
# A "second" interpreter: prefer a real 3.9, else fall back to whatever `python3` is.
PY2="python3.9"
command -v "$PY2" >/dev/null 2>&1 || PY2="python3"

pkg_compile() { "$1" -W error -m compileall -q subproto fakeup bench train; }

port() { echo $(( (RANDOM % 500) + 8500 )); }

run_suite() {
  local py="$1" label="$2"
  echo "----- [$label] byte-compile (all packages) -----"
  pkg_compile "$py"
  echo "----- [$label] pytest -----"
  "$py" -m pytest -q
  echo "----- [$label] bench/ab.py (projection, mock) -----"
  "$py" bench/ab.py --mock 20 >/dev/null && echo "ab.py ok"
  echo "----- [$label] bench/live.py (pass-rate-held, mock) -----"
  "$py" bench/live.py --mock >/dev/null && echo "live.py ok"
  echo "----- [$label] bench/ablation.py (laya-vs-heuristic) -----"
  "$py" bench/ablation.py >/dev/null && echo "ablation.py ok"
  echo "----- [$label] subproto demo (no key) -----"
  "$py" -m subproto demo --port "$(port)" --slots >/dev/null && echo "demo ok"
}

echo "== PRIMARY INTERPRETER: $PY ($("$PY" --version 2>&1)) =="
run_suite "$PY" "3.11"

echo
echo "== SECONDARY INTERPRETER: $PY2 ($("$PY2" --version 2>&1)) =="
if [ "$PY2" = "$PY" ]; then
  echo "only one interpreter available; skipping cross-version pass"
else
  run_suite "$PY2" "3.9"
fi

echo
echo "ALL GATES PASS"
