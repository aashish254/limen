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
  echo "----- [$label] model versions: register, switch, degrade honestly, roll back (S33) -----"
  # The registered endpoint is port 9 on purpose: the gate's witness is that a dead
  # version cannot quietly answer, and the reason has to reach stdout.
  expect "unreachable" "$py" -m subproto models --home "$DH" --add dead-v1 \
    --url http://127.0.0.1:9 --tier personal --note "gate checkpoint"
  "$py" -m subproto models --home "$DH" --use dead-v1 >/dev/null && echo "ok: --use"
  expect '"version": "heuristic"' "$py" -m subproto models --home "$DH" --json
  SUBPROTO_HOME="$DH" SUBPROTO_APPLY=tool_gate,compact \
    "$py" -m subproto demo --port "$(port)" --slots >/dev/null
  expect "by model version" "$py" -m subproto report --home "$DH"
  "$py" -m subproto export --home "$DH" >/dev/null
  if ! grep -q '"model_version"' "$DH"/dataset-*.jsonl; then
    echo "GATE FAILED: the export dropped model_version" >&2; exit 1
  fi
  echo "ok: export carries model_version"
  expect "rolled back to" "$py" -m subproto models --home "$DH" --rollback
  echo "----- [$label] cadence: the split it counts is the split it runs (S34) -----"
  # The trainer is *really* invoked here, over the demo's own traffic. It cannot
  # finish on this machine (no MLX, no published weights: V2-D), and that is the
  # witness — the exit it returns has to be the exit the record carries, and a run
  # that did not finish must not become a selectable model version.
  RH="$(mktemp -d)"
  SUBPROTO_HOME="$RH" SUBPROTO_APPLY=tool_gate,compact \
    "$py" -m subproto demo --port "$(port)" --slots >/dev/null
  "$py" -m subproto learn --home "$RH" >/dev/null
  expect "decision      NOT READY" "$py" -m subproto retrain --home "$RH" --min-per-slot 99999
  if [ -e "$RH/training_history.json" ]; then
    echo "GATE FAILED: a plain status check wrote a cadence record" >&2; exit 1
  fi
  echo "ok: retrain with no flag records nothing"
  expect "planned:" "$py" -m subproto retrain --home "$RH" --record --min-per-slot 99999
  FLOOR="--min-per-slot 40 --min-answers 2 --min-days 0"
  expect "decision      READY" "$py" -m subproto retrain --home "$RH" $FLOOR
  RC=0
  "$py" -m subproto retrain --home "$RH" --run --json $FLOOR > "$RH/retrain.json" || RC=$?
  "$py" - "$RH" "$RC" <<'PYGATE'
import json, os, sys
home, rc = sys.argv[1], int(sys.argv[2])
st = json.load(open(os.path.join(home, "retrain.json")))
rows = json.load(open(os.path.join(home, "training_history.json")))
last = rows[-1]
assert st["recorded"] == "ran", st["recorded"]
assert st["exit"] is not None, "a ran row with no exit is a claim, not a measurement"
assert last["exit"] == st["exit"] and last["version"] == "lora-v1", last
assert last["status"] == st["recorded"], (last["status"], st["recorded"])
if not st["toolchain"]["mlx"]:
    assert st["exit"], "no MLX here, so a zero exit would be a fabricated training run"
assert st["split_written"]["split_sha"] == st["split_sha"], st["split_written"]
assert os.path.exists(os.path.join(home, "training", "train.jsonl")), \
    "--run named a split no step wrote"
assert rc != 0, "the CLI reported a failed trainer as success"
print("ok: --run wrote the split it hashed (exit %s, the CLI returned %s)" % (st["exit"], rc))
PYGATE
  RC2=0
  OUT="$("$py" -m subproto retrain --home "$RH" --run $FLOOR 2>&1)" || RC2=$?
  case "$OUT" in
    *"same rows as the page"*) echo "ok: the page's hash matches the rows on disk" ;;
    *) echo "GATE FAILED: the run page did not verify its own split: $OUT" >&2; exit 1 ;;
  esac
  case "$OUT" in
    *"ran it: exit"*) ;; *) echo "GATE FAILED: the page hid the trainer's exit: $OUT" >&2; exit 1 ;;
  esac
  "$py" - "$RH" <<'PYGATE'
import json, os, sys
rows = json.load(open(os.path.join(sys.argv[1], "training_history.json")))
ran = [r for r in rows if r["status"] == "ran"]
assert [r["version"] for r in ran] == ["lora-v1", "lora-v2"], [r["version"] for r in ran]
assert [r["status"] for r in rows] == ["planned", "ran", "ran"], [r["status"] for r in rows]
assert all(r["exit"] for r in ran), "a second attempt on the same split was called a success"
print("ok: every attempt is on the log, in order, under its own version")
PYGATE
  USE_OUT="$("$py" -m subproto models --home "$RH" --use lora-v1 2>&1 || true)"
  case "$USE_OUT" in
    *"no version 'lora-v1' registered"*)
      echo "ok: a run that did not finish cannot be activated" ;;
    *) echo "GATE FAILED: --use accepted an unfinished run: $USE_OUT" >&2; exit 1 ;;
  esac
  if [ -e "$RH/models.json" ]; then
    echo "GATE FAILED: a rejected --use still wrote the manifest" >&2; exit 1
  fi
  echo "ok: a rejected --use leaves the manifest alone"
  expect "by model version" "$py" -m subproto report --home "$RH"

  echo "----- [$label] mutation gate: every rule in the flywheel, compiler and versioning -----"
  expect "MUTATION GATE: OK" "$py" bench/mutation_gate.py
  echo "----- [$label] S32 paired decision-cost probe (both arms printed) -----"
  # The probe exits 0 whatever it measures: a slower compiled arm is a result, not a
  # broken build. The gate here is only that it printed its numbers.
  S32="$("$py" bench/s32_probe.py 4)"
  echo "$S32"
  case "$S32" in *"gate (compiled <= per-slot):"*) ;; *)
    echo "GATE FAILED: s32_probe printed no gate line" >&2; exit 1;;
  esac
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
