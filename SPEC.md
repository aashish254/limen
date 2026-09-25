# subproto — Product & Engineering Spec

> **One line:** A local *System One* layer that sits in front of any terminal coding
> agent and makes it cheaper and faster by deciding — with a ~400M model, not the
> frontier one — what context, tools, and effort each turn actually needs.

Status: **P0 + P1 + M1-sprint shipped and tested.** A three/four-dialect transparent
proxy (OpenAI chat + `responses`, Anthropic `messages`, Gemini `generateContent`),
telemetry + waste/slot-precision report, 4 decision slots (observation + opt-in
enforce), code graph, dataset export + label loop + deterministic train/val split, a
guarded MLX LoRA script, a local Laya scoring server + ablation harness, a live
terminal savings meter, and a **mock-measured** pass-rate-held benchmark (−27.9%
input tokens, pass-rate held, bootstrap CI, no key). The remaining gaps are gated on
external resources — a real quantized checkpoint, non-zero API spend, and a public
task suite — and stay labelled **BLOCKED(external)**, never falsely checked.

---

## 1. The goal (what "winning" means)

We are chasing **two things at once**, and the whole plan is built so they don't
trade off against each other:

1. **Genuine, measured utility.** Real users point Claude Code / Codex / Gemini
   CLI / Cline / aider / OpenCode / Antigravity at it and demonstrably spend
   **less money, with equal or better task success**, because the agent stopped
   replaying irrelevant context and stopped asking the big model to make trivial
   filtering decisions.
2. **Virality / star-worthiness.** It is the obvious next repo in the lineage of
   [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) /
   [Laya](https://huggingface.co/convai/laya) / openclaw / omniroute — installable
   in one command, instantly legible, and with **one shareable number** on the
   README hero image.

**The North-Star metric (our "hero number"):**

> On a fixed task suite with the model held constant and task **pass-rate held
> equal**, subproto reduces **input tokens by ≥ 30%** and **p50 time-to-first-token
> by ≥ 20%**, at **zero correctness loss** (≤ 1% pass-rate delta), reproducible
> with one command and no API key against the mock, and with real keys against a
> published task list.

Everything below either serves that number or serves the trust needed to believe it.

### 1.1 Why this is the right bet (thesis)

Frontier coding agents waste tokens for two reasons unrelated to intelligence:

- They **replay enormous, mostly-irrelevant context** every turn: a ~40k-token
  system prompt, 20–40 tool schemas that go unused that turn, and multi-kilobyte
  tool results scrolled past.
- Every turn's **"is this file relevant? / can I drop this log? / is this trivial?"**
  decision is a *classification*, paid for with frontier-model generation.

A System One / System Two split (the thing that makes Jev fast) offloads those
classifications to a tiny, local, sub-100ms decision model. subproto brings that
split to the **harness**, so it works with *any* vendor and *any* model — including
models that don't exist yet — without the user changing editors or providers.

### 1.2 The moat is the dataset, not the integration

"Laya plugged into a proxy" is cloneable in a weekend. The defensible asset is a
**large, human-labelled corpus of coding-agent routing decisions** (keep/drop per
file, per tool, per context chunk, effort tier) harvested from real traffic. That
corpus is the supervision signal for the LoRA fine-tune that closes Laya's
zero-shot accuracy gap — and nobody else has it. Every design choice below serves
*harvesting and trusting that dataset*.

---

## 2. Users & jobs-to-be-done

| User | Job they hire subproto for | What must be true |
|---|---|---|
| Terminal-first indie dev paying per-token | "make my bill smaller without dumbing my agent down" | savings visible, opt-in, reversible |
| OSS contributor / power user (the star driver) | "give me a number I can believe and screenshot" | reproducible benchmark, honest labels |
| Team lead / platform eng | "cut agent spend fleet-wide, keep vendor flexibility" | vendor-agnostic proxy, no SDK change |
| AI-infra tinkerer | "let me run/finetune a real System One model" | Laya adapter + export path |

**Primary persona to win:** the power user who currently reads Jev/Laya threads and
wants the terminal version. They judge the README rendering closely and only star
things whose claims survive a one-command check.

---

## 3. Non-goals (scope guardrails)

- **Not** a new model or a new agent harness. We are middleware.
- **Not** a man-in-the-middle on TLS. We are an explicit `base_url` the user opts into.
- **Not** an auto model-router-sells-billions clone (OpenRouter already owns that).
  Effort routing ships, but it is not the headline.
- **Not** cloud-hosted; **no** server, **no** telemetry upload, **no** account.
- **Not** willing to trade correctness for tokens silently.

---

## 4. Core invariants (never break these)

- **I1 — Observation-first.** Every slot records what it *would* do before it *does*
  it. Enforcement is opt-in (`SUBPROTO_APPLY`). Telemetry is honest from day zero.
- **I2 — Prompt-cache safety.** Never rewrite the cached prompt prefix mid-stream.
  Anthropic/OpenAI cache reads are ~10× cheaper than fresh; breaking a stable prefix
  costs more than we save. Context injection appends *after* the prefix; compaction
  fires only past a size threshold where a fresh cache already beats a replay.
- **I3 — Correctness floor.** The last N turns of a conversation are sacred.
  A slot may never drop recent user intent, open errors, or the current tool-result
  the agent is reacting to. Every drop is logged and individually reversible.
- **I4 — Local-first / private.** Decisions run on-device; only the actual prompt
  leaves, exactly as it already does today. Nothing is uploaded.
- **I5 — Pass-through correctness.** With no slots enforced, subproto is byte-for-byte
  transparent (headers + body + streaming). If we ever break this, we are off.
- **I6 — Measured, not claimed.** Any public number is reproducible by a reviewer with
  one command. Projections are labelled as projections until a held-pass-rate harness
  upgrades them to measurements.

---

## 5. Functional requirements + acceptance criteria

Requirement IDs map to the shipped modules so progress is auditable.

### FR-1 Transparent, multi-dialect proxy — ✅ shipped
`proxy.py`, `protocol.py`, `usage.py`
- **AC:** OpenAI (`/chat/completions`, `/responses`), Anthropic (`/messages`) and
  Gemini (`generateContent`/`streamGenerateContent`) all relay correctly, streamed
  (SSE) and non-streamed.
- **AC:** SSE usage reconstructed for cache-read / cache-write / reasoning tokens on
  every dialect.
- **AC:** Byte-for-byte passthrough in observation mode verified by e2e test.

### FR-2 Telemetry & waste report — ✅ shipped
`telemetry.py`, `pricing.py`, `report.py`
- **AC:** SQLite store of prompt-shape features, usage, latency, ttfb, cost, cache stats.
- **AC:** `subproto report` shows where input tokens came from (system/tool_result/
  assistant/user), per-model cache hit rate, failures, ttfb p50/p95, and **per-slot
  headroom** as a projection.
- **AC:** Privacy: metadata-only by default; bodies recorded only with `--store-bodies`.

### FR-3 The four decision slots — ✅ shipped (heuristics, observation + opt-in apply)
`heuristics.py`, `engine.py`
- **FR-3a tool_gate:** keep only relevant tool schemas this turn. **AC:** dropped
  tool count > 0 on irrelevant turns, core tools never dropped, still 200 over wire.
- **FR-3b compact:** drop stale context/tool-results within a token budget, tail-protected.
  **AC:** recent turns always kept; savings proportional to history depth.
- **FR-3c context (graph-scoped retrieval):** rank files a task touches via offline graph.
  **AC:** `subproto graph` indexes imports+symbols in seconds; `subproto where "<task>"`
  returns the right file with a `why` trace; over the wire it appends (never rewrites) context.
- **FR-3d effort:** route trivial turns to low tier / small model. **AC:** complex vs
  trivial requests classify correctly on the heuristic; observational only by default.

### FR-4 Laya backend adapter + local scoring server — ✅ interface + mock backend shipped; real checkpoint BLOCKED(external)
`laya.py`, `laya_server.py`, `bench/ablation.py`
- **AC today:** adapter asks per-candidate keep/drop; falls back to heuristics if the
  server is unreachable; backend recorded per decision (`laya` vs `heuristic`). A
  local `/health`+`/score` server (deterministic lexical scorer, MLX path behind an
  import guard) runs on an ephemeral port; `bench/ablation.py` reports laya-vs-
  heuristic precision on labelled ground truth (both lexical today → they agree, and
  the report says so).
- **AC target:** point `LAYA_URL` at a quantized MLX Laya checkpoint and show a real
  laya-vs-heuristic precision delta + CPU latency curve. **BLOCKED(external):** needs
  ~808MB HF weights + Apple MLX runtime.

### FR-5 Dataset harvest & label loop — ✅ shipped
`dataset.py`, `subproto export`, `subproto label`, `subproto split`, `train/finetune_mlx.py`
- **AC:** JSONL `subproto/1` records per-decision: features, candidates+scores, backend,
  applied flag, token/cost outcome, and any human label.
- **AC:** export is stable and append-only across releases (it's a training asset).
- **AC:** `subproto split` emits a deterministic, seeded, body_sha-de-duplicated
  train/val split in the `{state,question,options,answer}` supervision format; no
  train/val leakage (tested). `train/finetune_mlx.py` is a guarded LoRA script that
  exits honestly without MLX + weights.

### FR-6 Benchmark harness — ✅ shipped (projection + mock-measured; billed hero BLOCKED(external))
`bench/ab.py`, `bench/results.md`, `bench/live.py`, `bench/tasks.sample.jsonl`
- **AC:** `python bench/ab.py --mock` reproduces the per-slot savings **projection**
  table with no key.
- **AC:** `python bench/live.py --mock` runs the **pass-rate-held** A/B (observe vs
  enforce) against `fakeup` with a SWE-bench-shaped task set + mock grader, and emits
  the delivered input-token Δ with bootstrap 95% CI: −27.9% tokens, pass-rate +0.0 pp.
  The enforced body is echoed by the mock so the token number is *delivered*, not
  estimated. Latency there is a request-shape proxy, labelled as such.
- **AC target:** run the same harness over a **public** task suite with a real grader
  and real provider billing to publish the billed hero number. **BLOCKED(external):**
  non-zero API spend + a curated SWE-bench subset.

### FR-7 CLI, install, demo — ✅ shipped
`cli.py`, `install.sh`, `demo.py`, `fakeup/server.py`
- **AC:** `subproto demo --slots --audit` shows the whole idea in one command, no key.
- **AC:** `subproto up --for claude|codex|gemini|aider` prints exact env vars; `inject`
  and `--help` work on 3.9 and 3.11.

### FR-8 Gemini-native + OpenAI `responses` streaming edges — ✅ shipped
`usage.py`, `proxy.py`, `fakeup/server.py`, `demo.py`
- **AC:** Gemini `generateContent`/`streamGenerateContent` usage (`usageMetadata`:
  prompt/candidates/thoughts/cached tokens) parsed streamed + non-streamed and routed
  with `x-goog-api-key`; OpenAI `responses` usage (incl. cached-input + reasoning
  tokens) parsed. Both exercised end-to-end through the proxy against `fakeup`, which
  now emits those exact shapes.

### FR-9 TUI overlay — ✅ shipped
`live.py`, `subproto live`
- **AC:** live terminal meter: "saved 38% / 4,120 tok this session · $1.02 → $0.63",
  tailing telemetry; `subproto live --once` emits a deterministic snapshot for CI and
  screenshots. Pure `render(snapshot) -> str`, ANSI-tested.

---

## 6. Metrics & instrumentation (what we must always be able to report)

- **Input-token reduction %** vs unproxied baseline, **at held pass-rate** (the hero).
- **p50/p95 time-to-first-token** delta (the latency win the tiny model buys).
- **Cache-hit-rate delta** (prove invariant I2 isn't hurting us).
- **Slot precision:** for each slot, labelled-correct rate (laya vs heuristic ablation).
- **Correctness guard:** task pass-rate delta, must stay ≥ −1%.
- **Cost:** $ per task, before/after.

---

## 7. Success criteria for "the next big thing" (the star engine)

1. **One-command truth.** `subproto demo` + `bench/ab.py --mock` work with no key,
   no signup, on a fresh clone. (Done.)
2. **The hero image is a bill.** README's first visual is `$A → $B, same tasks,
   same pass rate`. **Mock-measured today** (−27.9% tokens, pass-rate held, CI);
   the *billed* bill drop needs FR-6-real + M3 (non-zero API spend) — kept labelled a
   projection until then, per I6.
3. **A killer demo gif.** Terminal side-by-side: agent reading 8 files vs `subproto
   where` jumping to the right one, with the token counter visibly lower. (Needs the
   external launch assets; `subproto live` supplies the meter.)
4. **Rides the Jev/Laya wave.** Framing is "System One for coding agents," positioned
   next to the models people are already excited about.
5. **Contributors can add value in 5 minutes** via `subproto label` — the OSS loop
   that compounds stars into a better model.
6. **Copy-paste install**, honest roadmap, and a `good first issue` that is a real
   label batch.

---

## 8. Milestones

- **M0 (done)** — P0 proxy + telemetry + report; P1 slots + graph + dataset + demo +
  offline benchmark. Green on 3.9 & 3.11.
- **M1 (done)** — Per-tool wiring docs (`WIRING.md`, path-served test), FR-8 hardened
  (OpenAI `responses` + Gemini `generateContent` shapes in `fakeup` + parsed + e2e),
  `bench/live.py` pass-rate-held harness against `fakeup` with a SWE-bench-shaped task
  adapter + mock grader, and per-decision latency plumbing. Report now carries
  cache-hit-rate, decision latency, and label-driven slot precision.
- **M2 (partial — BLOCKED external)** — Local Laya scoring server + adapter + ablation
  harness shipped and tested (lexical backend). The **real quantized MLX checkpoint**
  and published CPU latency curve need ~808MB HF weights + MLX runtime.
- **M3 (harness shipped — billed number BLOCKED external)** — `bench/live.py` runs the
  pass-rate-held A/B with bootstrap CI and reports a mock-delivered −27.9% token
  reduction. The **public task suite + real grader + billed provider savings** need API
  spend.
- **M4 (pipeline shipped — training BLOCKED external)** — export + label loop +
  deterministic train/val split + guarded LoRA script all tested. **Actual fine-tune +
  dataset publication** need weights/runtime and a real training run.
- **M5 (TUI shipped — release/post/GIF BLOCKED external)** — `subproto live` meter is
  screenshot-ready. The tagged PyPI release, demo GIF, and launch posts are human
  actions + spend.

---

## 9. Risks & mitigations

| Risk | Reality | Mitigation |
|---|---|---|
| Laya zero-shot accuracy is weak | It's "a fast base to specialise" | Ship heuristics first; harvest labels; fine-tune is M4 |
| CPU latency ≫ the 33ms GPU headline | Numbers are T4-GPU | Route decisions are sparse (5–20/turn); quantise (MLX); measure and publish honestly |
| Prompt-cache break makes us *cost more* | Real, severe | Invariant I2; append-only context; compaction only past threshold; report cache-hit delta |
| Dropping needed context breaks tasks | Worst failure mode | Invariant I3; tail protection; reversible; pass-rate guard ≥ −1% (M3) |
| "another proxy/router" apathy | Crowded | Reframe as **System One decision layer**; the dataset moat; the measured hero number |
| MITM / ToS concerns | Vendor traffic | Never intercept TLS; use the *supported* `base_url`/hooks path only |
| Overclaiming = repo credibility hit | This user reviews rendering closely | Label projections as projections until M3; reproducible in one command |

---

## 10. Definition of "done / fully built"

subproto is "perfect" when:
1. A new user clones, runs `subproto demo`, and *gets it* in <60s with no key. **(done)**
2. A real user wires it into ≥5 agents and sees a measured, verified bill drop with
   no task regressions. **(BLOCKED external: real sessions + API spend; mock-measured
   held-pass-rate harness is in place)**
3. The README hero number is **measured**, not projected, with a public reproducible
   harness and confidence intervals. **(mock-delivered −27.9% w/ CI is measured today;
   the billed/provider number is M3)**
4. Laya (on-device) beats the heuristic baseline on slot precision in an ablation.
   **(ablation harness + local scorer shipped; needs the quantized checkpoint — M2)**
5. There is an open, versioned routing-decision dataset people can contribute labels
   to. **(export + label loop + seeded split pipeline shipped; publishing the corpus
   is external)**
6. A live TUI savings meter exists that is screenshot-worthy. **(done — `subproto live`)**
7. Test suite stays green on 3.9 & 3.11 with the held-pass-rate harness as a gate, not
   just token math. **(mock harness gates in `run_tests.sh`; real-suite gate is M3)**

Until all seven, "perfect" = "the claim is always at least as strong as the evidence."

---

## 11. Locked decisions (2026-09-25)

1. **On-device model backend → Apple MLX** (M2/M3/M4 first). M2 builds the Laya
   server on MLX, quantized, measured on Apple silicon. ONNX/CPU stays a documented
   fallback, not the default path.
2. **Live harness task suite → SWE-bench subset** (M3). We stand up `bench/live.py`
   against a SWE-bench-style subset with a grader; this is the source of the hero
   number's pass-rate claim.
3. **API spend → none yet, mock-first.** M1/M3 are built and validated entirely
   against `fakeup` + projections first; real-key validation is a separate, gated
   step after the harness is trustworthy. Keeps invariant I6 (measured-not-claimed)
   honest and the project $0 to reproduce.
4. **Name → keep `subproto`.** System One that runs *before* the frontier protocol.

---

## 12. Current sprint — M1, mock-first

Because spend is capped at $0 and the harness targets SWE-bench later, this sprint
stays fully reproducible:

1. **Per-tool wiring docs + validation matrix** — exact `base_url`/env for Claude Code,
   Codex, Gemini CLI, Cline, aider, OpenCode, Antigravity; verified against `fakeup`.
2. **FR-8 streaming edges against the mock** — OpenAI `responses` reasoning + Gemini
   `generateContent` shapes added to `fakeup` and parsed, so the proxy is truly
   dialect-complete before any real key touches it.
3. **Scaffold `bench/live.py` against `fakeup`** — pass-rate-held harness skeleton with
   a mock grader and a SWE-bench-shaped task adapter stub, so M3 is wiring, not design.
4. **Latency measurement plumbing** — per-decision timing recorded so the M2 MLX number
   has somewhere to land.

Exit (met): every README claim is either (a) shipped + tested, or (b) explicitly
labelled a projection with a one-command repro. The wiring docs, FR-8 dialect hardening,
`bench/live.py` scaffold, and latency plumbing all shipped; no regressions on 3.9/3.11.


