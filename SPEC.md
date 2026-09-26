# subproto — Product & Engineering Spec

> **One line:** A local *System One* layer that sits in front of any terminal coding
> agent and makes it cheaper and faster by deciding — with a ~400M model, not the
> frontier one — what context, tools, and effort each turn actually needs.

Status: **P0 + P1 + M1-sprint shipped and tested.** A three/four-dialect transparent
proxy (OpenAI chat + `responses`, Anthropic `messages`, Gemini `generateContent`),
telemetry + waste/slot-precision report, 4 decision slots (observation + opt-in
enforce), code graph, dataset export + label loop + deterministic train/val split, a
guarded MLX LoRA script, a local Laya scoring server + ablation harness, a live
terminal savings meter, and a **mock-measured** pass-rate-held benchmark (−29.9%
input tokens, pass-rate held, bootstrap CI, no key). The remaining gaps are gated on
external resources — a real quantized checkpoint, non-zero API spend, and a public
task suite — and stay labelled **BLOCKED(external)**, never falsely checked.

*The forward plan lives in [`ROADMAP.md`](ROADMAP.md): a v2→v13 spec that keeps a
~8-month lead, and it deliberately makes the tiny decision model a **pluggable System
One backend** (Laya is adapter #1, not the bet).*

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
   [Laya](https://huggingface.co/convaiinnovations/laya) — installable
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

### 1.3 The landscape, and why this is not a clone (deconfliction)

The "make coding agents cheaper" idea is live right now (as of Sept 2026), so the
first thing a reviewer types into search is *"isn't this just Jev / RTK / a router?"*
The spec has to answer that on paper, honestly, before the README does it in code.

| Category | Representative(s) | Mechanism | What it does **not** do that subproto does |
|---|---|---|---|
| Closed **System One service** | [Jev (TypeSafe)](https://typesafe.ai/blog/introducing-system-one-models-and-jev) | Cloud API + console; proprietary; you call it to inject decision nodes into apps **you** write | Open weights; on-device; a transparent proxy in front of agents you **didn't** write; per-turn agent-context triage |
| Open **System One models** | [Laya](https://huggingface.co/convaiinnovations/laya), OpenJev / djev / SemIf (~300–420M) | Small "state in → typed decision out" classifiers | No wiring: not a multi-dialect proxy, no observation-first telemetry, no label→fine-tune loop, no dataset. subproto **is** the wiring + the moat |
| Deterministic **token killers** | [RTK "Rust Token Killer"](https://github.com/rtk-ai/rtk) (60–90% bash-output claim) | Hooks **bash stdout**, 4 static regex rules (strip noise, dedupe, aggregate) | Only terminal output; no learned/adaptive decisions; not a `base_url` proxy; no tool-schema / history / file-selection / model-routing; **no pass-rate metric, no dataset harvesting** |
| Model **routers / gateways** | [OmniRoute](https://github.com/diegosouzapw/OmniRoute) (MIT), LiteLLM, OpenRouter, RouteLLM | Pick **which model/provider** answers a whole request | Don't decide **what goes inside** the prompt each turn; not a local sub-100ms classifier |
| Manual **config hygiene** | `CLAUDE.md` / output styles, `/compact`, disabling MCP tools | Human-maintained, per-tool, static settings | Not automatic, not measured, not vendor-portable, doesn't compound into anything |
| General **prompt compressors** | LLMLingua-family | Token-squeeze arbitrary prose | Agent-unaware and prompt-cache-hostile (violates our I2) |

**The open gap subproto occupies:** a *local, transparent, multi-dialect proxy* that
treats the **whole turn** — tool schemas + replayed context + which files + how much
model — as tiny **learned** System One classifications, is **observation-first and
measures pass-rate held**, and turns every decision into a **labelled dataset** that
fine-tunes the very model making the calls. No listed project ships that set together.
RTK is the closest neighbour and **explicitly omits** success-rate measurement and
adaptive learning; the routers and manual techniques live at a different layer; the
open models are raw material with no harness.

### 1.4 How we win against this field (positioning, not volume)

- **We don't out-shout RTK's "90% of bash output."** That's a self-warned, partial
  number over one stream. We publish a **whole-request input-token reduction at a held
  pass-rate with a confidence interval** — the honest, harder number (I6), and the one
  the token-killer wave has to answer to.
- **We don't compete with Jev on the model.** We're the open, local, agent-facing
  *harness* Jev isn't, with Laya as a swappable base we specialise on our own data.
- **We don't compete with routers on provider choice.** We work one level *down*, on
  what the chosen model is forced to read every turn.

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
  costs more than we save. Context injection appends *after* the prefix — concretely,
  at the end of the last turn (or a new trailing turn after an assistant one), never
  into the top-level `system` string or the tools block, which *are* the prefix;
  compaction fires only past a size threshold where a fresh cache already beats a
  replay.
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
  every dialect. (Each field is read wherever that provider actually emits it; a
  dialect with no cache-write concept records 0 rather than an invented number.)
- **AC:** Byte-for-byte passthrough in observation mode verified by e2e test — including
  *end-to-end headers*: a `content-encoding: gzip` request body arrives at the upstream
  still compressed and still labelled, and a gzipped response keeps its header and its
  `content-length` on the way back (I5; `test_I5_gzip_*`).

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
- **FR-3d effort:** classify trivial vs complex turns and record the tier + a
  `small_model_ok` flag. **AC:** the heuristic classifies correctly on both shapes.
  Effort is **advisory-only**: it never rewrites a request (no model swap yet), so a
  decision reports `applied: false` and `subproto up` says so when the slot is named
  in `SUBPROTO_APPLY`. Enforcement over the wire is v3 work, not shipped.

### FR-4 Pluggable System One backend — ✅ SPI shipped; **not** Laya-fixed
`systemone/base.py` + `systemone/registry.py`, `laya.py` (thin adapter), `laya_server.py`, `bench/ablation.py`
- **Design principle (de-fixation):** the tiny decision model is a **swappable component,
  not the product bet.** `ModelAdapter` (`health`/`score`) + a registry resolves
  `SUBPROTO_MODEL` to an adapter — Laya, OpenJev, djev, SemIf, our own `mlx_lora`, an
  arbitrary `http://` endpoint (which is how GGUF servers and hosted classifiers are
  reached today), and the always-on `heuristic` fallback. Every decision records the
  **label** of the adapter that answered it. New small models become an *upgrade we adopt
  in a config line*, never a threat. See [`ROADMAP.md`](ROADMAP.md) §1 & §4.
- **AC today:** the adapter asks per-candidate keep/drop; falls back to heuristics if the
  server is unreachable **or unconfigured**; backend label recorded per decision;
  `subproto models` lists selectable backends, the active one and the per-slot
  assignment; `--model` selects it on `up`/`demo`; `SUBPROTO_MODEL_<SLOT>` /
  `model_by_slot` override per slot (only `tool_gate`/`compact` ask a model, so only
  those are overridable). A local `/health`+`/score` server (deterministic lexical
  scorer, MLX path behind an import guard) runs on an ephemeral port;
  `bench/ablation.py` scores **any registry label** through the adapter interface and
  reports precision vs the heuristic, listing an adapter with no endpoint as skipped
  (both real columns are the lexical stand-in today → they agree, and the report says so).
- **AC target (v2):** point the SPI at any quantized checkpoint (MLX/GGUF) and show a
  real per-slot precision/latency trade-off across **adapters**. **BLOCKED(external):**
  needs ~400–808MB HF weights + a runtime (MLX/llama.cpp).

### FR-5 Dataset harvest & label loop — ✅ shipped
`dataset.py`, `subproto export`, `subproto label`, `subproto split`, `train/finetune_mlx.py`
- **AC:** JSONL `subproto/1` records per-decision: features, candidates+scores, backend,
  applied flag, token/cost outcome, and any human label.
- **AC:** the export is a stable, schema-versioned (`subproto/1`) projection of the
  telemetry DB, re-derived in `request_id` order — so a later export keeps every
  earlier row byte-identical and only appends (tested). The DB, not the day-stamped
  JSONL, is the append-only training asset.
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
  the delivered input-token Δ with bootstrap 95% CI: −29.9% tokens, pass-rate +0.0 pp.
  (Was published as −30.2%; the number moved to **−29.9% `[28.2, 31.3]`** when v5 gave
  both enforced arms the same code graph, which changes what the per-slot `context` slot
  does. The current figure is what `bench/live_results.md` prints today.)
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

### FR-10 The Context Compiler — ✅ shipped (opt-in, `SUBPROTO_COMPILE=on`; accuracy measured on the mock)
`subproto/compiler.py`, `engine.decide`, `subproto compile`, `bench/live.py` (third arm)
The four slots stop spending independent budgets. `tool_gate` / `compact` / `context`
resolve from **one value-per-token knapsack over one budget**, over `Candidate`s of three
kinds — `{message, tool spec, file note}` — harvested from the *existing* heuristics so no
scoring logic forks.
- **AC (I1):** off by default; with `SUBPROTO_COMPILE=on` and empty `SUBPROTO_APPLY` the
  plan is recorded and no bytes are written (`test_observation_mode_compiles_but_writes_nothing`).
- **AC (I2):** the system + tools block are never candidates; the injected file note lands
  after the last turn (`test_compiled_context_note_lands_after_the_prefix`).
- **AC (I3):** the protected set — last `PROTECTED_TAIL=4` turns, `core` tools, and at most
  `EVIDENCE_MAX=3` graph-evidence reads under a `PROTECTED_MAX=60%` cap — is booked before
  the optimiser runs, and a budget that cannot fit it reports `over_budget` instead of
  truncating: `subproto compile … --budget 1500` → *"over budget: protected set needs 4983
  tok, budget is 1500 — the tail is sacred (I3), so nothing was cut"*.
- **AC (I6) — retention proof:** every drop names the joint decision that beat it
  (`value/token 0.002700 < kept floor 0.002988 (floor set by tool_result#9)`) and carries a
  reversible address `{request_id, body_sha, pointer, index}` into telemetry — six decimals
  and a real sha, because a proof that prints "0.0030 < 0.0030" or points at `body_sha: null`
  is not checkable. *Address, not reconstruction:* the body is never deleted, so recovery is
  `subproto show <request_id>` + the message index; `compiler` deliberately has no `restore()`.
- **AC (never bet the request on one component):** a raising plan degrades to the per-slot
  path and the reason surfaces as `model_status()["compile_error"]`.
- **AC — the accuracy gate (this is what makes it a contribution, not a savings pitch).**
  `bench/live.py` runs a third arm and reports token-weighted **recall of required files**
  and **precision of what survived**, content-measured via `@@dump <path>@@` markers —
  mention-testing would score 100% recall for a prompt whose evidence was evicted, because
  a `tool_use` still names the file after its `tool_result` is gone.
  `--require-better` exits 3 unless recall holds at 100% *and* precision improves; it is a
  gate in `run_tests.sh`, and it printed **CHEAPER-BUT-NOT-BETTER twice** before the
  mechanism was fixed.
  - `bench/tasks.sample.jsonl` (n=20), compiled vs per-slot: **−19.3% input tokens**
    `[18.0, 20.9]`, recall **100% → 100%**, precision **2.6% → 19.6%** (+17.0 pp
    `[+2.8, +35.8]`), p50 6.1 → 6.3 ms (**still slower**, reported rather than hidden),
    verdict **BETTER**.
  - `bench/tasks.hard.jsonl` (n=8; one needed 2.6k-token read as the *oldest* turn + 6
    stale decoys): per-slot **pass-rate 0.0% / recall 2.7% → I3 VIOLATED**, compiled
    **100% / 100%** at **18.2% fewer tokens** than per-slot `[16.2, 20.2]`, precision
    0.5% → 25.1%, verdict **BETTER**. Per-slot spends *more* and keeps *less*.
- **The compiler's local cost, measured (S32).** Request latency above includes the
  mock's own HTTP round trip, and two runs of the two arms drift 10-20 % on a laptop —
  more than the overhead being looked for — so the witness is `bench/s32_probe.py`:
  both arms in one process against the same payloads, alternating round by round, and
  the **minimum** per round (interference can only add time). Profiling put the gap on
  one line — a `PATH_RE` scan of every candidate message (1.20 ms/decision) — and that
  scan is now needle-anchored: each ranked basename is located by a C-level `find` and
  the regex only ever sees the path-shaped run around it. Paired best
  **+1.242 → +0.192 ms/decision** (3.27 per-slot vs 3.76 compiled at 9 rounds; the
  4-round count `run_tests.sh` uses prints +0.256 on 3.11 and +0.323 on 3.9, because
  the suite itself is competing for the CPU then). The
  gate was "compiled ≤ per-slot" and it is **NOT MET**: the residual is the
  graph-coupling scan itself (0.22 ms of needle `find`s), the very feature that buys
  the +17.0 pp precision. Closing it would mean deleting the reason to compile, so the
  row is reported rather than dropped.
- **Known limits (mock-measured, not billed):** both tables are the local mock with a
  materialised repo tree and a content-substring grader — no frontier model judged
  anything, so "accuracy" here means *the evidence the model needs is still in the prompt*.
  The billed, real-model version is **V2-B** and stays `BLOCKED(external)`. Latency is a
  request-shape proxy. The index covers code, `.md`/`.rst`/`.txt`/`.sql` (S27: headings
  and DDL objects are those files' symbols) **and** `.json`/`.csv`/`.tsv`/`.yaml`/`.yml`
  (S31: a nested key path or a header column is that file's symbol, and its *values* are
  deliberately not indexed). What still has no signal is a file type the indexer does not
  know at all — one needed read of an unknown extension is saved only when the human typed
  its path (`EVIDENCE_NAMED`).

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
   same pass rate`. **Mock-measured today** (−29.9% tokens, pass-rate held, CI);
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
  pass-rate-held A/B with bootstrap CI and reports a mock-delivered −29.9% token
  reduction. The **public task suite + real grader + billed provider savings** need API
  spend.
- **M4 (pipeline shipped — training BLOCKED external)** — export + label loop +
  deterministic train/val split + guarded LoRA script all tested. **Actual fine-tune +
  dataset publication** need weights/runtime and a real training run.
- **M5 (TUI shipped — release/post/GIF BLOCKED external)** — `subproto live` meter is
  screenshot-ready. The tagged PyPI release, demo GIF, and launch posts are human
  actions + spend.
- **M6 (done)** — **FR-10, the Context Compiler**: one joint budget over messages / tool
  specs / file notes behind `SUBPROTO_COMPILE=on`, a machine-readable retention proof on
  every turn, and a third `compiled` arm in `bench/live.py` whose accuracy gate
  (`--require-better`, recall + precision) runs in `run_tests.sh` under both interpreters.
  Mock-measured; the billed, real-model version stays **V2-B**.

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
   harness and confidence intervals. **(mock-delivered −29.9% w/ CI is measured today;
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

---

## 13. v2 — from mock-measured to the billed hero number (execution spec)

v1 (M1) is shipped and mock-verified. **Everything that remains is gated on a
resource §11.3 or the offline sandbox locked out** — non-zero API spend, a public task
suite, real HF weights, and human launch actions. This section pre-specifies that work
so each item is *wiring, not design*, the moment its gate opens. **Every item below
carries an explicit `gate:` — it may never be marked done until a human opens that
gate** (invariant I6). No v2 item is silently assumed complete.

### V2-A — Real-traffic validation · `gate: user runs their own agents` (no incremental $)
- **V2-A1** Wire subproto into ≥3 real sessions across ≥2 vendors with `--store-bodies`.
- **V2-A2** `subproto audit` + `report` on real corpora; reconcile projected vs
  delivered reduction; fix the provider-quirk usage-parse gaps only real traffic
  exposes (the mock cannot).
- **V2-A3** Confirm I2 against **real** Anthropic/OpenAI caching: wiring the proxy must
  not regress cache-hit rate. Acceptance: a **measured** (not mock) input-token
  reduction with no correctness regression, self-assessed by the user.

### V2-B — The billed hero number · `gate: approved API budget + a public task suite`
- **V2-B1** ~~Port `bench/tasks.sample.jsonl` to a curated 20-task SWE-bench-style set~~
  **done locally** — the mock corpus is now a 20-task curated set (varied stale-context
  shapes, `files_present` graders). Remaining: swap in a public subset with **real**
  graders when the spend gate opens.
- **V2-B2** `bench/live.py` runs observe-vs-enforce against the **real provider**, model
  held constant: emits input-token Δ, **billed $ Δ**, p50 TTFB Δ, pass-rate Δ, each with
  bootstrap 95% CI.
- **V2-B3** README hero image becomes that measured bill. North-Star (§1): **≥30% input
  tokens at ≤1% pass-rate delta**, reproducible. If the real number lands lower, the
  claim is narrowed to what was measured — never the reverse.

### V2-C — Real Laya on-device · `gate: quantized weight download + Apple MLX runtime` (no $)
- **V2-C1** `laya_server` MLX backend loads quantized `convaiinnovations/laya`; `/score`
  returns real probabilities instead of the lexical stand-in.
- **V2-C2** Re-run `bench/ablation.py` on the **real** scorer vs heuristic — this is what
  makes the currently-zero precision delta non-zero and meaningful (T15).
- **V2-C3** Publish the per-decision CPU/MLX latency curve (target ≤150 ms p50, ≤350 ms
  p95 on Apple silicon). Acceptance: FR-4 target + **T16** check with a real scorer.

### V2-D — Close the data flywheel · `gate: V2-A labels + a training run`
- **V2-D1** Run `train/finetune_mlx.py` LoRA on `subproto split` output (real labels).
- **V2-D2** Ship a v0 routing model; re-run V2-B with it; show **learned > heuristic**
  precision at equal-or-better pass-rate — the moat earning its keep.
- **V2-D3** Publish a versioned slice of the dataset; open a `good first issue` that is a
  real label batch (the contributor loop from §7.5).

### V2-E — The viral surface · `gate: human launch actions`
- **V2-E1** Tagged PyPI release (`pipx install subproto`).
- **V2-E2** Demo GIF (agent `grep`-ing its way around vs `subproto where` jumping to the
  file, token counter visibly lower) + a `subproto live` screenshot.
- **V2-E3** HN / Product-Hunt post anchored on the measured hero number, positioned
  against RTK / Jev / routers exactly as framed in §1.3. **T19** checks here.

### v2 Definition of Done
All seven of §10, with items (2)–(5) flipped from mock/external to **measured · billed ·
learned · published**, and every public number carrying a real confidence interval.
Until a V2 item's `gate` is opened by the user, it stays `[~] BLOCKED(gate)` — never a
false `[x]`.


