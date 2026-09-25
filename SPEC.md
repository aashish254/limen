# subproto — Product & Engineering Spec

> **One line:** A local *System One* layer that sits in front of any terminal coding
> agent and makes it cheaper and faster by deciding — with a ~400M model, not the
> frontier one — what context, tools, and effort each turn actually needs.

Status: **P0 + P1 shipped and tested** (transparent proxy, telemetry, 4 decision
slots in observation mode, code graph, dataset export, offline benchmark, no-key
demo). This spec defines the goal and the requirements that turn a working core
into the thing people can't not star.

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

### FR-1 Transparent, dual-dialect proxy — ✅ shipped
`proxy.py`, `protocol.py`, `usage.py`
- **AC:** OpenAI (`/chat/completions`, `/responses`) and Anthropic (`/messages`) both
  relay correctly, streamed (SSE) and non-streamed.
- **AC:** SSE usage reconstructed for cache-read / cache-write / reasoning tokens.
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

### FR-4 Laya backend adapter — ⚠️ interface shipped, live inference pending
`laya.py`
- **AC today:** adapter asks per-candidate keep/drop; falls back to heuristics if the
  server is unreachable; backend recorded per decision (`laya` vs `heuristic`).
- **AC target:** `LAYA_URL=… subproto up` uses a real local Laya scoring server and the
  report shows **laya vs heuristic precision delta** on the same corpus.

### FR-5 Dataset harvest & label loop — ✅ shipped
`dataset.py`, `subproto export`, `subproto label`
- **AC:** JSONL `subproto/1` records per-decision: features, candidates+scores, backend,
  applied flag, token/cost outcome, and any human label.
- **AC:** export is stable and append-only across releases (it's a training asset).

### FR-6 Offline benchmark harness — ✅ shipped (projection only)
`bench/ab.py`, `bench/results.md`
- **AC:** `python bench/ab.py --mock` reproduces the per-slot savings table with no key.
- **AC target:** `bench/live.py` runs the **pass-rate-held** A/B against a real task
  suite + grader and emits the hero number with confidence intervals.

### FR-7 CLI, install, demo — ✅ shipped
`cli.py`, `install.sh`, `demo.py`, `fakeup/server.py`
- **AC:** `subproto demo --slots --audit` shows the whole idea in one command, no key.
- **AC:** `subproto up --for claude|codex|gemini|aider` prints exact env vars; `inject`
  and `--help` work on 3.9 and 3.11.

### FR-8 Gemini-native + OpenAI `responses` streaming edges — ❌ not started
- **AC:** vertex/gemini `generateContent` streaming usage captured; `responses` SSE
  reasoning tokens captured. (Current OpenAI path covers chat completions + best-effort
  responses.)

### FR-9 TUI overlay — ❌ not started (the shareable moment)
- **AC:** live terminal meter: "saved 38% / 4,120 tok this session · $1.02 → $0.63".
  This is the screenshot that spreads.

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
   same pass rate` — measured, not projected. (Needs FR-6 live + M3.)
3. **A killer demo gif.** Terminal side-by-side: agent reading 8 files vs `subproto
   where` jumping to the right one, with the token counter visibly lower. (Needs FR-9.)
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
- **M1 — Real traffic, real numbers (projection → measurement).** Document per-tool
  wiring (Claude/Codex/Gemini/Cline/aider/OpenCode/Antigravity), validate the waste
  report against ≥3 real sessions, harden OpenAI `responses` + Gemini streaming (FR-8).
- **M2 — Laya on-device.** Stand up a local Laya scoring server (MLX/ONNX quantized),
  wire FR-4 end-to-end, prove laya-beats-heuristic precision on the ablation, publish
  the CPU latency curve (target ≤150ms/decision on Apple silicon, ≤350ms worst case).
- **M3 — The held-pass-rate harness.** `bench/live.py` over a public task suite
  (SWE-bench-style / a curated 20-task set) with a grader; publish `results.md` with
  confidence intervals. This is what upgrades every claim.
- **M4 — LoRA fine-tune + release the dataset.** Train on harvested labels, ship a
  v0 routing model, publish the dataset. The moat goes live.
- **M5 — The viral layer.** TUI live-savings overlay (FR-9), demo gif, launch copy,
  first tagged PyPI release, Product-Hunt / HN post anchored on the hero number.

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
   no task regressions. **(M1 + M3)**
3. The README hero number is **measured**, not projected, with a public reproducible
   harness and confidence intervals. **(M3)**
4. Laya (on-device) beats the heuristic baseline on slot precision in an ablation. **(M2 + M4)**
5. There is an open, versioned routing-decision dataset people can contribute labels to. **(M4)**
6. A live TUI savings meter exists that is screenshot-worthy. **(M5)**
7. Test suite stays green on 3.9 & 3.11 with the held-pass-rate harness as a gate, not
   just token math. **(ongoing)**

Until all seven, "perfect" = "the claim is always at least as strong as the evidence."

---

## 11. Decisions I need from you before the next build sprint

1. **Target hardware for the on-device model** — Apple-silicon Mac (MLX) first, or
   pure-Python CPU (portable but slower)? Sets M2 backend.
2. **Task suite for the live harness** — SWE-bench subset, or a small curated
   terminal-agent suite we control end-to-end? Sets M3.
3. **How much real-API spend are you OK authorizing** for M1/M3 (a few dollars), or do
   we build the entire held-pass-rate harness against the mock first and validate with
   keys later?
4. **Name/brand lock-in** — keep `subproto`? (I chose it as a fast System One that sits
   *before* the frontier "protocol". Alternatives if you want a more brandable/viral handle.)
