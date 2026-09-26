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

- [x] **T20** `dataset.py`: `build_training_split(cfg, val_frac)` → deterministic, seeded train/val split; export to a Laya-compatible supervision format (`{"state","question","options","answer"}`) with de-dup by body_sha.
- [x] **T21** tests: split is stable across runs, no leakage (val∩train empty), de-dup works, format validated.
- [x] **T22** `train/finetune_mlx.py`: documented LoRA script that consumes the exported split; import-guarded so it is honest about needing MLX + weights to run (not executed here).

## Report completeness (SPEC §6) + docs sync

- [x] **T23** `report.py`: add cache-hit-rate, per-decision latency, and slot-precision sections to text + json render; ensure `render_json` stays backward-compatible.
- [x] **T24** test: report text/json contains the new sections given a corpus with decisions + labels.
- [x] **T25** Sync `SPEC.md` + `README.md` status markers to reality (FR-4/8/9 → shipped-or-blocked, no overclaim per I6).

## Cross-cutting verification

- [x] **T26** Full suite green on 3.11 **and** 3.9; `python3 -m compileall` 0 warnings; `bench/ab.py --mock` + `bench/live.py --mock` both run end-to-end with no key.
- [x] **T27** `run_tests.sh` one-command gate (compileall + pytest + both benches + `subproto demo --slots`), used as the pre-commit proof for every task above.

## v2 SPI — pluggable System One backend (ROADMAP §1 / SPEC FR-4). Code-only, no gate, buildable now.

- [x] **S01** `subproto/systemone/` package: `ModelAdapter` base + generic `HTTPScoreAdapter` (`/health`+`/score`) + `registry.resolve(config)` with named adapters (laya/openjev/djev/semif/http/heuristic).
- [x] **S02** `laya.LayaClient` becomes a thin `HTTPScoreAdapter` subclass (label `laya`) — full back-compat with existing tests.
- [x] **S03** `config`: `model`/`model_url` (env `SUBPROTO_MODEL`/`SUBPROTO_MODEL_URL`) resolved, with `LAYA_URL` preserved as the default `laya` path.
- [x] **S04** `engine`: resolve its decision backend through the registry; record the adapter's **label** per decision (default stays `laya`); unknown/None → heuristic.
- [x] **S05** `cli`: `subproto models` lists the registry + active selection; `--model` flag on `up`/`demo`.
- [x] **S06** tests: registry resolution + configurable label + heuristic fallback + end-to-end "engine records `openjev`" + LayaClient back-compat. (13 in `test_systemone.py`; suite 77 → 90.)
- [x] **S07** pyproject packages += `subproto.systemone`; full gate green on 3.9 & 3.11.

## v2 SPI — remaining code-only items from ROADMAP §2 "v2 Ships" (no gate).

- [x] **S08** Per-slot model selection: `SUBPROTO_MODEL_TOOL_GATE` / `SUBPROTO_MODEL_COMPACT` (and `model_by_slot` in config) resolve independently, falling back to the global `SUBPROTO_MODEL`; slots that never ask the model a question (`context`, `effort`) are reported as not-model-backed rather than silently pinned.
- [x] **S09** `bench/ablation.py` compares **adapters** (any registry label, scored through the `ModelAdapter` interface) against the heuristic baseline, not just a hard-coded laya column; committed evidence stays deterministic.
- [x] **S10** Tests + docs for S08/S09; gate green on 3.9 & 3.11. (Suite 90 → 99; gate now also runs `ablation --adapters` and `subproto models`.)

## v2 → v3 bridge — the Router: per-slot model choice from evidence, not a name (ROADMAP §1 Router row). Code-only, opt-in, mock-verifiable.

Manual selection (`SUBPROTO_MODEL[_<slot>]`) names one model and *hopes* it is good.
The Router instead reads what the system has actually measured — per-adapter slot
precision (`bench/ablation.py`) and per-backend decision latency (`decision_ms`) —
and hands each un-pinned slot's question to the best-scoring **configured** adapter
within an optional latency budget. Honesty (I6): an adapter with no measured precision
is never auto-picked as "best", and precision ties route to `heuristic` (no endpoint,
no network hop) — so on today's synthetic evidence (laya == heuristic, Δ0) the Router
correctly stays on heuristics rather than fabricating an edge. Opt-in via
`SUBPROTO_ROUTER=on`; every routed decision records *why*.

- [x] **S11** `subproto/systemone/router.py`: `load_evidence(path)` (precision per adapter from `bench/ablation.json`), `rank(candidates, evidence, budget_ms)` (measured-only, budget-filtered, heuristic-preferring ties, deterministic), and `Router(config, evidence=None, budget_ms=None)` with `.available` + `.select(slot) -> (adapter, label, reason)` + `.plan()`. `registry.configured_adapters(config)` supplies the candidate set. `config`: `router` (`SUBPROTO_ROUTER`), `router_budget_ms` (`SUBPROTO_ROUTER_BUDGET_MS`).
- [x] **S12** `engine`: when routing is on, non-pinned model-backed slots resolve through the `Router` and record a `router` reason; explicit `SUBPROTO_MODEL_<slot>` / `model_by_slot` pins and empty-evidence degrade to the manual registry path. `subproto models` shows the router plan (which adapter it *would* use per slot, with the evidence that drove it) when enabled.
- [x] **S13** `subproto/tests/test_router.py`: opt-in (default off → manual), pin respected, best-measured selection, no-evidence → manual degrade, latency-budget excludes a slow-but-precise adapter, precision tie → heuristic (I6 honesty), the shipped `ablation.json` evidence routes to heuristic, and an end-to-end engine decision carrying the routed `backend` + reason. Suite 99 → 115, green on 3.9 & 3.11.
- [x] **S14** ROADMAP §1 Router row → shipped (opt-in, evidence-driven); `subproto models --router` + a `SUBPROTO_ROUTER=on` demo in the gate; full `run_tests.sh` green on 3.9 & 3.11.

> Real *labelled* precision on live traffic stays the V2-A gate; the Router is wired to
> consume it the day `bench/ablation.py` is re-run against the `subproto label` corpus —
> only the number changes, not this module.

## v2 — gated backlog (SPEC §13). `BLOCKED(gate)`: each needs a human/external action first; never falsely checked.

- [~] **V2-A** Real-traffic validation across ≥2 vendors + cache-hit (I2) check — gate: user runs their own agents (no incremental $). Upgrades projections → measured.
- [~] **V2-B** Billed hero number: `bench/live.py` vs the real provider over a public 20-task SWE-bench set, model held constant, with CI — gate: approved API budget + public task suite.
- [~] **V2-C** Real quantized MLX Laya on-device + non-zero ablation precision delta + CPU latency curve — gate: weight download + Apple MLX runtime (no $). (Closes **T16**.)
- [~] **V2-D** LoRA fine-tune on the split + ship v0 routing model + publish a dataset slice — gate: V2-A labels + a training run.
- [~] **V2-E** Tagged PyPI release + demo GIF + HN/PH launch post positioned per SPEC §1.3 — gate: human launch actions. (Closes **T19**.)
