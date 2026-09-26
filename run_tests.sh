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

# expect <needle> <cmd...> — the command must print `needle`, or the gate fails loudly.
expect() {
  local needle="$1"; shift
  if ! "$@" | grep -q "$needle"; then
    echo "GATE FAILED: expected \"$needle\" from: $*" >&2
    exit 1
  fi
  echo "ok: $* :: \"$needle\""
}

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
  echo "----- [$label] bench/live.py --require-better (S23 accuracy gate, hard set) -----"
  "$py" bench/live.py --mock --tasks bench/tasks.hard.jsonl --require-better >/dev/null \
    && echo "live.py hard set: BETTER"
  echo "----- [$label] bench/ablation.py (adapter-vs-heuristic) -----"
  "$py" bench/ablation.py >/dev/null && echo "ablation.py ok"
  "$py" bench/ablation.py --adapters laya,openjev,djev >/dev/null && echo "ablation --adapters ok"
  echo "----- [$label] subproto demo (no key) -----"
  "$py" -m subproto demo --port "$(port)" --slots >/dev/null && echo "demo ok"
  echo "----- [$label] the label flywheel over the demo's own traffic (S30) -----"
  DH="$(mktemp -d)"
  SUBPROTO_HOME="$DH" SUBPROTO_COMPILE=on "$py" -m subproto demo --port "$(port)" --slots >/dev/null
  expect "eviction regret" "$py" -m subproto learn --home "$DH"
  "$py" -m subproto learn --home "$DH" --json >/dev/null && echo "ok: learn --json"
  expect "nothing written" "$py" -m subproto learn --home "$DH" --dry-run
  expect "left as it is" "$py" -m subproto learn --home "$DH"
  expect "eviction regret" "$py" -m subproto report --home "$DH"
  if "$py" -m subproto report --home "$DH" --no-learn | grep -q "eviction regret"; then
    echo "GATE FAILED: report --no-learn still harvested" >&2; exit 1
  fi
  echo "ok: report --no-learn skips the harvest"
  expect "labelled_by_traffic" "$py" -m subproto export --home "$DH"
  "$py" -m subproto split --home "$DH" >/dev/null && echo "ok: split"
  echo "----- [$label] mutation gate: every label-flywheel rule must be tested -----"
  expect "MUTATION GATE: OK" "$py" bench/mutation_gate.py
  echo "----- [$label] subproto models (SPI registry) -----"
  "$py" -m subproto models --home "$(mktemp -d)" >/dev/null && echo "models ok"
  "$py" -m subproto models --home "$(mktemp -d)" --json >/dev/null && echo "models --json ok"
  echo "----- [$label] subproto Router (evidence-driven per-slot choice) -----"
  "$py" -m subproto models --home "$(mktemp -d)" --router >/dev/null && echo "models --router ok"
  "$py" -m subproto models --home "$(mktemp -d)" --router --json >/dev/null && echo "models --router --json ok"
  LAYA_URL="http://127.0.0.1:9" SUBPROTO_ROUTER=on \
    "$py" -m subproto demo --port "$(port)" --slots >/dev/null && echo "router demo ok"
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
