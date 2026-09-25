# subproto — TODO (derived from SPEC.md)

Atomic, testable tasks. A box is only checked when its verification passes in this
workspace (`python3.11 -m pytest -q` green + the named command runs). Tasks that
require resources locked out by SPEC §11 (real API spend, real MLX weight download,
actual PyPI publish, real HN post) are marked **BLOCKED(external)** and never
falsely checked — invariant I6: measured, not claimed.

Legend: `[ ]` open · `[x]` done · `[~]` blocked-external

## M1 — mock-first sprint (SPEC §12)

- [x] **T01** `usage.py`: parse Gemini `generateContent` usage (`usageMetadata`: promptTokenCount / candidatesTokenCount / thoughtsTokenCount / cachedContentTokenCount), streamed + non-streamed.
- [x] **T02** `usage.py`: parse OpenAI `/responses` usage (`response.completed` event + non-stream `response.usage`, incl. `input_tokens_details.cached_tokens` and `output_tokens_details.reasoning_tokens`).
- [x] **T03** `proxy.py`: `dialect_for` + relay route `generateContent`/`streamGenerateContent` → `gemini`, `/v1/responses` → `responses`; add `gemini_upstream` config + `x-goog-api-key` injection.
- [x] **T04** `fakeup/server.py`: add `/v1/responses` and `:generateContent`/`:streamGenerateContent` endpoints emitting those exact shapes.
- [x] **T05** tests: unit for gemini + responses usage; e2e through the proxy asserting tokens/cost recorded for both dialects.
- [x] **T06** Latency plumbing: `engine.py` records per-decision `decision_ms`; `telemetry.py` adds an idempotent `engine_ms` column + stores it; `report.py` shows decision-latency p50/p95.
- [x] **T07** Telemetry schema migration: idempotent `ALTER TABLE ADD COLUMN` for any new columns so existing DBs upgrade cleanly (test old-schema → new).
- [x] **T08** `bench/live.py`: pass-rate-held A/B harness against `fakeup` — same task run observe-vs-enforce, mock grader, emits input-token Δ, p50 ttfb Δ, pass-rate Δ with bootstrap CI; writes `bench/live_results.md`.
- [x] **T09** `bench/live.py` SWE-bench-shaped task adapter (loader reads a JSONL of `{task, context, expected_files, grader}`); ship a small `bench/tasks.sample.jsonl` that runs on the mock.
- [x] **T10** tests: run the live harness in-process against fakeup; assert it completes, pass-rate is held (Δ ≥ −1%), and emits a numeric token reduction.
- [x] **T11** `WIRING.md`: exact base_url/env per tool (Claude Code, Codex, Gemini CLI, Cline, aider, OpenCode, Antigravity) + a test that the proxy serves every documented path.
- [x] **T12** Invariant tests: I5 byte-for-byte passthrough (no-slot proxy request bytes == response forwarded); I2 context slot appends only (never deletes/reorders prefix) when applied; I3 tail-protection holds under tiny budgets.

## FR-4 / M2 — Laya backend (mock-verifiable adapter; real MLX weights = external)

- [x] **T13** `subproto/laya_server.py`: HTTP scoring server implementing the `/health` + `/score` contract `laya.py` expects; deterministic lexical scorer, with an MLX path guarded behind `if importlib.util.find_spec("mlx")`.
- [x] **T14** test: start `laya_server` on an ephemeral port, point engine `laya_url` at it, assert `backend == "laya"` is recorded for tool_gate and laya scores drive keep/drop.
- [x] **T15** `bench/ablation.py`: laya-vs-heuristic precision/agreement on a labelled synthetic ground-truth set; integrates into the report's "slot precision".
- [~] **T16** Real quantized MLX Laya checkpoint on-device + published CPU/MLX latency curve — BLOCKED(external): needs ~808MB HF weights + Apple MLX runtime.

## FR-9 / M5 — the shareable surface (TUI is testable; publish/post = external)

- [x] **T17** `subproto/live.py`: ANSI terminal savings meter (pure `render(snapshot) -> str`) + `subproto live` command tailing telemetry; `--once` snapshot mode for CI/screenshots.
- [x] **T18** test: `live.render()` produces the "saved X% / N tok · $A → $B" line from a telemetry snapshot; `subproto live --once` exits 0 and prints the meter.
- [~] **T19** Tagged PyPI release + Product-Hunt/HN launch post + recorded demo GIF — BLOCKED(external): human actions + real API spend for the on-screen bill.

## FR-5 / M4 — dataset moat (pipeline testable; actual training = external)

- [ ] **T20** `dataset.py`: `build_training_split(cfg, val_frac)` → deterministic, seeded train/val split; export to a Laya-compatible supervision format (`{"state","question","options","answer"}`) with de-dup by body_sha.
- [ ] **T21** tests: split is stable across runs, no leakage (val∩train empty), de-dup works, format validated.
- [ ] **T22** `train/finetune_mlx.py`: documented LoRA script that consumes the exported split; import-guarded so it is honest about needing MLX + weights to run (not executed here).

## Report completeness (SPEC §6) + docs sync

- [ ] **T23** `report.py`: add cache-hit-rate, per-decision latency, and slot-precision sections to text + json render; ensure `render_json` stays backward-compatible.
- [ ] **T24** test: report text/json contains the new sections given a corpus with decisions + labels.
- [ ] **T25** Sync `SPEC.md` + `README.md` status markers to reality (FR-4/8/9 → shipped-or-blocked, no overclaim per I6).

## Cross-cutting verification

- [ ] **T26** Full suite green on 3.11 **and** 3.9; `python3 -m compileall` 0 warnings; `bench/ab.py --mock` + `bench/live.py --mock` both run end-to-end with no key.
- [ ] **T27** `run_tests.sh` one-command gate (compileall + pytest + both benches + `subproto demo --slots`), used as the pre-commit proof for every task above.
