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
input tokens, pass-rate held, bootstrap CI, no key). The real Laya checkpoint is
wired and measured — and it is a torch-CPU encoder, not an MLX model (§11.1), and it
did *not* beat the heuristic on the corpus this repo carries. The remaining gaps are
gated on external resources — routing labels whose origin differs from the baseline's
own rule, non-zero API spend, and a public task suite — and stay labelled
**BLOCKED(external)**, never falsely checked.

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
  scorer, plus the real checkpoint behind `--backend laya` on a Python ≥ 3.10 venv)
  runs on an ephemeral port;
  `bench/ablation.py` scores **any registry label** through the adapter interface and
  reports precision vs the heuristic, listing an adapter with no endpoint as skipped.
- **AC target (v2) — the checkpoint half is met, the runtime half was voided**
  (2026-09-27): `LAYA_URL` at the real `convaiinnovations/laya` checkpoint answers the
  shipped contract over HTTP and `bench/ablation.py` prints it as its own column, so
  the SPI is proven against the actual 421M-parameter checkpoint (fp32, torch CPU —
  there is no quantised MLX build of it to point at) rather than only a stand-in.
  What the measurement *found* is the part worth locking: on the synthetic corpus the
  checkpoint does not beat the heuristic (see `bench/ablation_results.md`'s
  corpus-ceiling section — the gold sets were authored around the heuristic's own
  rule, so the harness cannot express a large positive delta for anyone), and a
  `choice` answer is a **distribution over the options** (it sums to 1) whose
  confidence the runtime itself flags as uncalibrated,
  which the slot's `p ≥ 0.5` keep rule reads as "keep one tool". That is an open
  decision this spec now carries, not a bug to hide:
  **FR-4a — does a decision slot threshold a probability or rank one?** Thresholding
  is what ships and what guards correctness (I3); ranking is how the compiler spends
  a token budget, and it scores materially better on the same answers. Clearing it
  needs labelled data (V2-A/V2-D), not more instrumentation.
  The remaining gate is **labels with a different origin than the baseline's**, not
  weights or a runtime.

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
  exits honestly without MLX + weights. **Narrowed 2026-09-27:** it targets a causal
  LM through `mlx_lm`, so it cannot fine-tune Laya — an encoder with a classification
  head is trained with a sequence-classification LoRA (PEFT over torch), which is the
  V2-D path this repo now documents; the script stays for a generative System One and
  must not be pointed at the Laya base.

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
- **AC:** live terminal meter — `tokens saveable 38% 38,000 tok`, `spend before $1.02`,
  `spend after $0.63`, and the meter's own state line (`potential` while the slots only
  observe, `delivered` once bytes are actually cut); tailing telemetry,
  `subproto live --once` emits a deterministic snapshot for CI and screenshots. Pure
  `render(snapshot) -> str`, ANSI-tested.

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
  truncating: `subproto compile … --budget 1500` prints its own header
  *"subproto compile — over budget: nothing was cut"* and one reason line, *"! protected
  set needs 4959 tok, budget is 1500 — the tail is sacred (I3)"*, and exits 1.
- **AC (I6) — retention proof:** every drop names the joint decision that beat it
  (`value/token 0.002700 < kept floor 0.002988 (floor set by tool_result#9)`) and carries a
  reversible address `{request_id, body_sha, pointer, index}` into telemetry — six decimals
  and a real sha, because a proof that prints "0.0030 < 0.0030" or points at `body_sha: null`
  is not checkable. *Address, not reconstruction:* the body is never deleted, so recovery is
  `subproto show <request_id>` + the message index; `compiler` deliberately has no `restore()`.
  **Now walked (T38):** `show` resolves each pointer against the flattened message list the
  proof actually indexed and prints the item beside the reason it lost — pinned by a mutant
  that indexes the raw body instead (`bench/mutation_gate.py` T38-1), and a pointer whose
  kind disagrees with what its index holds prints no quote rather than a neighbour's text
  (T38-2). A home recorded without `--store-bodies` keeps the pointers and counts, which are
  measured, and says the body was never stored (I6).
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

### FR-11 The capture side of the flywheel — ✅ shipped (code-only; the training run stays **V2-D**)
`subproto/implicit.py`, `subproto learn`, `subproto/systemone/versions.py`, `subproto models`,
`subproto/retrain.py`, `report.py`, `dataset.py`
A fine-tune is only an experiment if three separate things are on record: what the traffic said
about the last decision, *which checkpoint* made it, and whether starting a new run was worth
it. All three are local, key-free, and gated on two interpreters.

- **AC (implicit labels, S29/S30):** `subproto learn` reads the `--store-bodies` spool and
  verdicts each drop from the wire alone — content cut and later re-read is a **regrettable
  drop**, and its token price is the number `learn` prints. Verdicts land in
  `implicit_labels.json`, a store *separate* from human `subproto label` output, so a human
  verdict always wins and is never overwritten; `--dry-run` prints and writes nothing.
  `build_training_split` consumes them (`reread` flips the answer to `keep`, `correction`
  excludes the candidate), so the flywheel tightens the next dataset without a labeller.
- **AC — measured on running traffic (S35, fresh home, mock, n=17 requests, applied
  `tool_gate,compact`):** `evicted reads seen 14`, `regrettable drops 10 (enforced 10,
  shadow 0)`, `tokens paid back 4,088 (re-reads of the 10 drops that reached the wire)`, and
  the verdict it came from priced on its own row: `implicit:re-read 4,088 (10 labels)`.
  Against the same run's `75,999 tok` saved by its 34 compiled decisions the payback is
  **5.4 %** of the saving — and the *same 10 drops* are
  what the identical traffic prints in shadow mode (`enforced 0`, `tokens paid back 0`), so
  the detector is mode-stable and `SUBPROTO_APPLY` only changes who pays. The regret count is
  reported as 10, not as a 0: applying a compiler costs re-reads, and saying otherwise would
  be the pitch this project exists to avoid.
- **AC (versioning + health gate, S33):** `$SUBPROTO_HOME/models.json` holds
  `versions: [{label, url, tier, added, note, version}]` plus `active`/`previous`, written
  atomically (temp + `os.replace`). Precedence stays explicitly documented and printed when
  it bites: `SUBPROTO_MODEL[_<SLOT>]` / config `model` **>** manifest `active` **>**
  `heuristic` (I1). An `active` whose `/health` fails is not used — resolution falls to
  `previous`, then `heuristic`, **with the reason surfaced**, and `model_version` on the
  decision follows the answer, never the wish. `subproto models --use/--rollback` are the
  write surface; `--rollback` with no `previous` says so instead of quietly going heuristic.
- **AC (every decision attributable):** `model_version` is stamped in both arms and carried
  through `export`, so `subproto report` answers *foundation vs personal vs compiled vs
  heuristic* as one table — decisions, requests, tokens saved, p50 decision ms, approval rate
  where labels exist. S35's print is the witness: `compiled 34 decisions … saved 75999 tok
  p50 0.7 ms` beside `heuristic 17 decisions … p50 0.0 ms`.
- **AC (cadence, S34):** `subproto retrain` decides *whether to train* from local state —
  **content** (sha256 over the split, with the train/val boundary committed so a re-shuffle
  counts as a new run), **balance** (≥ 40 rows/slot *and* ≥ 8 in each answer; 10 keeps and 1
  drop is a ratio, not a lesson), **time** (≥ 7 days since the last *completed* run),
  **provenance** (`git_sha` + toolchain presence beside every row). It prints every reason it
  refuses, writes nothing unless asked, and the command it prints is the absolute,
  shell-quoted one that actually runs. `training_history.json` is append-only: an unreadable
  file is left alone rather than replaced, a `refused`/`planned` row neither starts the
  cooldown clock nor becomes a selectable version, and only a run that *finished* can be
  activated with `models --use lora-vN`.
- **AC (V2-D stays a gate, not a claim):** the trainer is really invoked by `--run`, and on
  a machine without MLX + weights it exits 3 (`mlx / mlx-lm not installed`) with that exit
  recorded verbatim. `run_tests.sh` asserts the non-zero exit **only while
  `toolchain.mlx` is false**, so the leg documents today's limit without becoming a wall an
  operator has to delete once the weights land.
- **Known limits:** every number here is the local mock over 17 synthetic requests — the
  regret bill at real traffic and real providers is **V2-A/V2-B**. `models.json` records
  *what answered*, which is not the same as whether it was good; that judgement stays with
  the label store above. No training run has completed anywhere in this repo yet.

---

## 6. Metrics & instrumentation (what we must always be able to report)

- **Input-token reduction %** vs unproxied baseline, **at held pass-rate** (the hero).
- **p50/p95 time-to-first-token** delta (the latency win the tiny model buys).
- **Cache-hit-rate delta** (prove invariant I2 isn't hurting us).
- **Slot precision:** for each slot, labelled-correct rate (laya vs heuristic ablation).
- **Correctness guard:** task pass-rate delta, must stay ≥ −1%.
- **Cost:** $ per task, before/after.
- **Eviction regret:** drops made, regrettable drops (enforced vs shadow), and **tokens paid
  back** — the accuracy side of a cut, priced by the traffic rather than by an opinion (FR-11).
- **Per-model-version attribution:** decisions, tokens saved and p50 decision ms grouped by
  the `model_version` that produced them, so a fine-tune is compared to what it replaced in
  one query (FR-11).

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
- **M2 (partial — the model half is met 2026-09-27)** — Local Laya scoring server +
  adapter + ablation harness shipped and tested; `--backend laya` now serves the real
  `convaiinnovations/laya` checkpoint over the same `/health`+`/score` contract and
  `bench/ablation.py` scores it as its own column, with `bench/laya_latency.py`
  publishing the per-decision CPU curve. **What was learned, not just shipped:** there
  is no MLX runtime for this checkpoint (encoder, not causal LM — §11.1 corrected), the
  checkpoint does not beat the heuristic on the synthetic corpus the harness carries,
  and the corpus cannot express a large delta for anyone. The open gate is therefore
  **labels whose origin is not the baseline's own rule** (V2-A/V2-D), not weights.
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
| Laya zero-shot accuracy is weak | It's "a fast base to specialise" — **and measured weak on our corpus** (2026-09-27: `bench/ablation_results.md`, Δprecision −0.083 under the shipped rule) | Ship heuristics first; harvest labels; fine-tune is M4 |
| CPU latency ≫ the 33ms GPU headline | Numbers are T4-GPU | Route decisions are sparse (5–20/turn); the curve is measured and published on torch CPU (`bench/laya_latency.md`) — one pass answers near the budget, the tail is host-dependent, and MLX is not available for this architecture to fix it with |
| A `choice` answer is a distribution, not a keep probability | Real, and it changes the metric | `p ≥ 0.5` keeps one tool of nine; the slot's threshold-vs-rank rule is an open decision (FR-4a) and the report prints both readings instead of picking the flattering one. The runtime also flags these probabilities as uncalibrated, and the published curve carries that warning |
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
   **(measured 2026-09-27 against the real checkpoint: it does not — Δprecision −0.083
   under the shipped rule, and `corpus_ceiling()` shows the corpus cannot express a
   large positive delta for anyone. This item is now gated on V2-A labels, not on a
   checkpoint or a runtime — `bench/ablation_results.md` is the print)**
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
   **Corrected 2026-09-27 — this decision is void for Laya, and the checkpoint is
   what voided it.** `convaiinnovations/laya` is a ModernBERT-large *encoder* with a
   two-layer classification head (`pipeline_tag: text-classification`,
   `usage.output_tokens: 0` on every answer): a non-autoregressive classifier that
   never generates text. `mlx_lm` loads causal language models, so it has no path to
   this architecture, and the `laya` package requires torch. The replacement is not a
   downgrade: torch **CPU** meets this spec's own per-decision budget and the curve is
   published (`bench/laya_latency.py` → `bench/laya_latency.md`), so MLX is
   unnecessary here rather than merely awkward. MPS is not the default either —
   `Router(device=None)` selects it and the process dies on a
   MetalPerformanceGraph assertion (SIGABRT, exit 134) before the first answer.
   `--backend mlx` now refuses with this explanation instead of serving a stand-in
   and calling it a checkpoint. MLX stays the right target for a *generative* System
   One (our own LoRA over a causal model), and `train/finetune_mlx.py` is honest that
   it cannot fine-tune this checkpoint.
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
4. **Latency measurement plumbing** — per-decision timing recorded so a real
   per-decision number has somewhere to land. (What it was written for — "the M2 MLX
   number" — was later voided: see the §11.1 correction.)

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

### V2-C — Real Laya on-device · `gate: human-labelled routing decisions (V2-A) — not weights, not a runtime`
- **V2-C1** ✅ **measured 2026-09-27.** `laya_server --backend laya` loads
  `convaiinnovations/laya` on **torch CPU** (there is no MLX path to it — §11.1) and
  answers the shipped `/health` + `/score` contract with real probabilities, which the
  production `HTTPScoreAdapter` consumes. The witness is the opt-in live leg of
  `subproto/tests/test_laya_real.py`: `SUBPROTO_LAYA_PYTHON=<a ≥3.10 interpreter with
  `pip install "laya[serve]"`>` starts the real server and asserts the option set comes
  back answered. Without that env var the leg skips, so the gate stays cheap and the
  claim stays honest.
- **V2-C2** ✅ **measured, and the answer is negative.** The real checkpoint is *below*
  the heuristic on this corpus: precision **0.889 vs 0.972** (Δ −0.083) under the shipped
  `p ≥ 0.5` keep rule, **0.873** (Δ −0.099) read as a ranking, and it agrees with the
  heuristic's answer on **27 %** of options. `corpus_ceiling()` explains why the number
  cannot be read as skill: **7 of 10 cases have a gold set equal to the heuristic's own
  answer**, so 83 of 86 decisions cannot move either way and the whole harness can only
  express disagreement across 3 cases (2 false keeps, 1 false cut). What this gate needed
  was never a better scorer on this set — it is labels that were not written around the
  baseline's rule (V2-A).
- **V2-C3** ✅ **published.** `bench/laya_latency.py` → `bench/laya_latency.md` prints the
  per-decision CPU curve with its host conditions (device, threads, cores, load average
  before and after, the runtime's own calibration warning): the shipped `choice` shape
  costs **123 ms p50 / 133 p95 / 139 max** and fits the shipped 350 ms budget, while
  `keep_drop` (**800/1250/1383**) and `noul` (**1097/1581/2386**) do not. The §11 target
  of ≤150 ms p50 is met on `choice`; ≤350 ms p95 is met on `choice` only. HTTP-side
  corroboration: 131/155/160 ms over the real `/score` endpoint.

### V2-D — Close the data flywheel · `gate: V2-A labels + a training run`
- **V2-D1** Fine-tune a scorer on `subproto split` output (real labels) — with a
  **sequence-classification** LoRA over torch/PEFT. `train/finetune_mlx.py` cannot do
  this job: it drives `mlx_lm`, which trains causal LMs, and Laya is an encoder (§11.1).
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


