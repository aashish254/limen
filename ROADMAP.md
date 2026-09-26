# subproto — Roadmap Spec · v2 → v13

> **One line:** A versioned engineering roadmap that turns subproto from a working
> System One proxy into the default decision layer for every coding agent — and
> compounds advantages (data, distribution, measurement, model-agnosticism) a weekend
> clone cannot replicate for ~8 months.

**Status of this document:** a *plan*, not a claims sheet. Everything here is
labelled **planned**; nothing moves to "shipped" until it passes the same
`run_tests.sh` gate and the same I6 "measured-not-claimed" bar as v1. The
already-shipped v1 surface lives in [`SPEC.md`](SPEC.md) / [`TODO.md`](TODO.md).

---

## 0. Why this roadmap exists (the moat thesis in one paragraph)

The decision model is **not** the moat — ~400M "state in → typed decision out"
classifiers are appearing monthly (Laya, OpenJev, djev, SemIf, and whatever ships
next). Anyone can copy a proxy that calls one model in a weekend. What is *not*
copyable is: (a) a **pluggable** System One backend that rides *every* new small
model the day it drops, (b) a growing **labelled routing-decision dataset** harvested
from real agent traffic that only the layer sitting in front of *all* agents can see,
(c) a **public, pass-rate-held measurement** people trust, and (d) **breadth of
integration** (every agent, every provider, every editor). v2→v13 sequences those four
so each version raises the cost of catching up.

---

## 1. The architectural unlock that must happen first (de-fixating from Laya)

**Shipped (TODO S01–S10).** `laya.py` is no longer a single-backend adapter: it is a
thin subclass of the generic `HTTPScoreAdapter`, and the System One Model SPI below is
a provider registry where Laya is one entry, not the load-bearing bet.

**`subproto/systemone/` (implemented):**

| Concept | Contract | Status |
|---|---|---|
| `ModelAdapter` | `health() -> dict`, `score(state, question, options) -> {option: prob}` | Done — the surface `laya.py` already spoke, promoted to an interface in `systemone/base.py`. |
| `HTTPScoreAdapter` | the same, over `/health` + POST `/score`, with any `label` | Done — one implementation serves every HTTP-served model, so a new one needs no code. |
| Adapters | `laya`, `openjev`, `djev`, `semif`, `mlx_lora` named; `http://…` for anything else; `heuristic` as the always-available fallback | Named entries done (`systemone/registry.py`). `gguf` / `jev_api` are URL entries today: point `SUBPROTO_MODEL=http://…` at them and they work unmodified. |
| `Registry.resolve(config, slot)` | config/env → `(adapter, label)`; the label is what every decision records | Done, with explicit precedence and clean degradation (unknown / disabled / no URL → heuristics). |
| Per-slot selection | `SUBPROTO_MODEL_<SLOT>` / `model_by_slot` let one slot use a different model, falling back to the global choice | Done for the slots that actually ask a model a question (`tool_gate`, `compact`). `context` is graph-answered and `effort` shape-answered, so `subproto models` reports them as model-free rather than pinning a label that decided nothing. |
| `subproto models` | list backends, which is active, and which slot uses which | Done. |
| `bench/ablation.py` | compare **adapters** through the `ModelAdapter` interface, not a hard-coded laya column | Done — every label with an endpoint gets its own precision/recall/f1/Δ row; a label without one is listed as skipped instead of borrowing the stand-in's number. |
| `Router` | *automatically* picks an adapter per slot per turn from measured precision + latency budget | **Shipped (opt-in, evidence-driven).** `subproto/systemone/router.py` ranks the *configured* adapters by measured slot precision (`bench/ablation.py`) under an optional `SUBPROTO_ROUTER_BUDGET_MS`, and routes each un-pinned slot to the winner. Honesty (I6): an adapter with no measured precision is never auto-picked, and precision ties route to `heuristic` — so today's synthetic evidence (laya == heuristic, Δ0) correctly stays on heuristics instead of fabricating an edge. Opt-in (`SUBPROTO_ROUTER=on`); explicit pins win. Ensembling two tiny models per slot is a **v5.1** extension (v5 shipped the joint budget; see §v5). |

**Config (implemented, backward-compatible):**
```
SUBPROTO_MODEL=laya|openjev|djev|semif|mlx_lora|heuristic|http://host:port
SUBPROTO_MODEL_URL=http://host:port    # where the named model is served
SUBPROTO_MODEL_COMPACT=djev            # per-slot override (also _TOOL_GATE)
LAYA_URL=…                     # still works; it is the laya entry's URL (and the
                               # default when SUBPROTO_MODEL is unset)
```

`SUBPROTO_MODEL_DIR` (quantised checkpoints loaded in-process rather than over
HTTP) is still planned: it needs a runtime that can load them, which is the T16 /
V2-C gate, and pretending otherwise would be a fake adapter.

**Why this is the highest-leverage change:** a competitor that hard-codes Laya is
obsolete the week a better 300M model lands. A pluggable SPI makes every new model an
*upgrade to us*, not a *threat to us*. This converts the biggest technical risk
("Laya zero-shot is weak") into an advantage (we can hot-swap to whatever is best and
fine-tune it on our own data). **This is v2 and gates most of v3+.**

---

## 2. Version roadmap

Each entry: **theme → what ships → why a competitor can't match it (for ≥ a few
months) → the gate** (external resource required, if any). `gate:` items stay
`[~]`-blocked until a human opens them (per SPEC §13 / locked decision #3).

### v2 — Pluggable System One backend + real on-device inference
- **Ships:** ✅ the SPI above; ✅ a model registry; ✅ per-slot model selection;
  ✅ `bench/ablation.py` upgraded to compare **adapters**, not just laya-vs-heuristic;
  ⏳ three *real* adapters (`heuristic` ships, `http`/`mlx_lora` need an endpoint —
  the interface is done, the weights are the gate); ⏳ a published CPU/MLX latency curve.
- **Unfair:** a clone wired to one model must rewrite its core to add a second; we ship
  five. Riding OpenJev/djev/SemIf the day they trend *is* the marketing.
- `gate:` quantised weight download + MLX/llama.cpp runtime for the ML path (no $).

### v3 — The trusted number: real-traffic + billed hero harness
- **Ships:** V2-A/V2-B from SPEC §13 — real sessions across ≥3 agents; `bench/live.py`
  run against **real providers** (model held constant) emitting input-token Δ, billed
  $ Δ, p50 TTFB Δ, pass-rate Δ, each with bootstrap CI; a `results.md` **public
  leaderboard** ("measured % saved at held pass-rate").
- **Unfair:** RTK-style tools report % of *bash output* and no success rate. A
  whole-request, pass-rate-held, CI'd, reproducible number is a credibility moat and
  exactly the artifact that spreads on HN.
- `gate:` a small approved API budget + a public task suite.

### v4 — The learning loop goes continuous (personal + foundation routing models) — **code-only half shipped; the training run stays V2-D**
- **Ships:** automatic label capture (implicit: was the turn re-run / corrected / did the
  user toggle a drop back?), a scheduled LoRA **retraining cadence** via
  `train/finetune_mlx.py`, model versioning with rollback, and two model tiers: a shared
  **foundation** routing model + an opt-in **per-user personal** LoRA that specialises to
  their repos/tools. A/B: personal vs foundation, measured.
- **Unfair:** every user's usage trains *their* model and (opt-in) the foundation one.
  The dataset now compounds faster than any single team can hand-label. This is the flywheel
  spinning — the thing the spec calls "the moat" (SPEC §1.2).
- **Shipped today (`subproto/systemone/versions.py`, S33) — the machinery, not the compute:**
  `$SUBPROTO_HOME/models.json` holds `versions: [{label, url, tier, added, note, version}]`
  with `active`/`previous`; `subproto models --add/--use/--rollback` drive it; an active
  version whose `/health` refuses is *not* used (it falls to `previous`, then the
  heuristics, with the reason printed at startup); and every decision — both arms, the
  heuristic answers included — carries `model_version`, which `subproto report` groups by
  and `subproto export` carries into the training set. `tier` is exactly
  `foundation` / `personal`, so "A/B: personal vs foundation, measured" now has a key to
  group on and a rollback if the fine-tune is worse. What is *not* here is the training run
  and the weights — that stays **V2-D**.
- **Shipped today (`subproto/retrain.py`, S34) — the cadence, still not the compute:**
  `subproto retrain` answers *is a fine-tune worth starting* from local state alone and
  prints every reason it refuses: **content** (sha256 over the split, train/val boundary
  committed, so a re-shuffle is a new run), **balance** (≥ 40 rows/slot *and* ≥ 8 in each
  answer), **time** (≥ 7 days since the last *completed* run), **provenance** (git sha +
  toolchain presence in every row). It runs nothing and writes nothing unless `--run`/`--record`
  says so; `training_history.json` is append-only, a refused row neither starts the cooldown
  nor becomes selectable, and only a finished run can be activated with
  `subproto models --use lora-vN`. Over the demo's own traffic it prints
  `479 train / 120 val` and `decision  ready` under a per-slot table whose verdict leads each
  row (`compact  ok  209 rows  177 keep  32 drop`), and the trainer it invokes exits **3** with
  `mlx / mlx-lm not installed` — the honest V2-D state, gated that way on 3.9 and 3.11.
- **Measured on running traffic (S35, fresh home, mock, n=17, `SUBPROTO_APPLY=tool_gate,compact`):**
  the traffic judged the compiler's drops and the verdict is not flattering — `evicted reads
  seen 14`, `regrettable drops 10 (enforced 10, shadow 0)`, and `tokens paid back 4,088`
  priced as re-reads of the 10 drops that reached the wire. Against the `75,999 tok` the same
  run's 34 compiled decisions saved, the payback
  is 5.4 per cent of the saving; in shadow mode the identical traffic reports the *same* 10
  drops with `enforced 0` and nothing paid, so applying only changes who pays. Those 10
  became `keep` supervision rows the same minute (`implicit verdicts: keep 10`), which is the
  loop the row exists to close. `learn` (S29/S30) is the capture half and SPEC **FR-11** is
  where all three — labels, versions, cadence — are specified.
- **The surface is part of the deliverable (S37).** Every readout prints through
  `subproto/style.py` on one grid — a label column and a number column, colour only ever on a
  state word, red only for breakage, no box drawing, long lines only for a path or a command
  the reader copies — because a GIF, an issue thread and a README all take the same page at
  different widths. `subproto/tests/test_style.py` enforces it as a property of every page,
  and it earned its keep: the pass found a flagship `report` that printed no colour at all,
  ANSI escapes counted as column width, a headerless `where`, a `demo` request that reshuffled
  the compiler's own kept-tool proof between identical runs, a live meter that cleared a pipe,
  and a report row (`human/traffic disagree`) whose mutant had no witness left to kill it.
- **A pointer you cannot walk is an overclaim (S38).** The compiler's retention proof has
  always ended in `subproto show <request_id>`, quoted from the compile page, from a comment
  in `engine.py` and from SPEC FR-10 — and the command did not exist. It does now: the page
  resolves each drop's address through the *same* flattened message list the proof scored,
  and refuses a pointer whose kind disagrees with what its index holds rather than printing a
  neighbouring turn as the item that was cut. Building it surfaced two more: message drops
  silently quoted nothing (raw-list index vs flattened index), and `compact` printed its
  `dropped_count` under the word `kept`. Five mutants (T38-1…5) pin all of it, and README
  carries the real page with the fields that move between replays named.

### v5 — The Context Compiler (joint multi-slot optimisation) — **shipped, opt-in, mock-measured**
- **Ships:** stop deciding tool/compact/context/effort independently; a joint optimiser
  maximises measured value per token subject to one token budget, using graph + heuristic
  scores together. A "compile this turn" pass that emits a minimal, cache-safe prompt + a
  machine-readable **retention proof** (what was dropped, why, reversible pointer).
- **Shipped today (`subproto/compiler.py`, `SUBPROTO_COMPILE=on`, SPEC FR-10 / M6):** the
  three *editing* slots resolve from one knapsack over `{message, tool spec, file note}`;
  `effort` still decides on its own (it changes the route, not the prompt). The objective
  is **value per token as the heuristics + graph score it**, not learned expected task
  success — that needs the V2-A labels, and is listed as the open extension below.
- **Measured (mock, `bench/live.py`, reproducible with one command):** compiled vs
  per-slot = **−19.3% input tokens** `[18.0, 20.9]` with **recall of required files held
  at 100%** and **precision +17.0 pp** `[+2.8, +35.8]`; on `bench/tasks.hard.jsonl` the
  per-slot arm loses the file the task is about (**pass-rate 0%, recall 2.7%** → I3
  violated) while the compiler keeps **100%/100% at 18.2% fewer tokens**. The gate is
  `--require-better`, wired into `run_tests.sh`, and it printed CHEAPER-BUT-NOT-BETTER
  twice before the mechanism earned BETTER.
- **Still true of the mock, not of a bill:** no frontier model judged these answers —
  "accuracy" here means the evidence the model needs survived in the prompt. Billed +
  real-model version = **V2-B**. And the compiler's own p50 is *still slower* on the mock
  (6.1 → 6.3 ms). The reason is no longer a mystery: S32 profiled it to one line — a
  `PATH_RE` scan of every candidate message, 1.20 ms/decision — and needle-anchored it,
  cutting the **paired** decision gap from +1.24 ms to +0.19 ms
  (`bench/s32_probe.py`, both arms in one process, minimum over rounds). Parity was not
  reached, and what remains is the graph coupling that buys the +17.0 pp: the saving is
  tokens and correctness, not local compute.
- **Unfair:** transforms subproto from four heuristics in a trenchcoat into a single
  optimiser whose objective is *measured success per dollar* — much harder to clone than
  four rules. On the two mock sets it strictly dominates per-slot (equal-or-better recall
  at lower spend); the claim generalises only when a real grader says so.
- **Open extension (v5.1):** swap the heuristic value function for the learned one (the
  LoRA from v4's dataset predicts per-candidate keep/success) and ensemble two tiny models
  per slot. (Indexing non-code files — **S27** — shipped with this round: `.md`/`.rst`/
  `.txt`/`.sql` now carry symbols, so a migration or design-doc read earns graph evidence.)

### v6 — Team & fleet control plane (still local-first)
- **Ships:** policy-as-code (`subproto.policy`) — budgets, enforcement flags, per-team
  model choice, spend caps; RBAC + audit log; **opt-in** shared dataset pooling with
  consent + de-dup (the org's decisions compound into one better model); org analytics;
  self-hosted control plane with **no mandatory upload** (I4 preserved — sync is explicit).
- **Unfair:** the unit of adoption flips from "one dev" to "a platform team"; switching
  cost and pooled data make it sticky enterprise-side before a clone has an enterprise story.

### v7 — Predictive context + cache-aware scheduling
- **Ships:** a small predictor that guesses the next files/tools a turn needs from graph
  + session trajectory and **pre-warms prompt cache** / prefetches content; prompt-cache-
  aware ordering so we *raise* cache-hit rate instead of risking it; speculative tool-
  schema pruning with instant unfreeze on mispredict.
- **Unfair:** this is where I2 (cache safety) becomes a *positive* cost win, not just a
  non-regression. Requires the telemetry corpus + graph both — a rules-only proxy can't.

### v8 — Multi-agent & subagent awareness
- **Ships:** coordinate context decisions across parallel/sub-agents (Claude Code
  subagents, Codex fan-out, OpenCode): shared context ledger, cross-agent dedup, a
  "System One bus" so N agents don't each pay to re-discover the same files.
- **Unfair:** agentic swarms are the 2026 direction; being the *only* layer that sits
  in front of all of them and dedups their combined context is a structural advantage.

### v9 — Zero-config everywhere (form factors)
- **Ships:** one-line install (the packaging in `pyproject.toml` → `pipx install
  subproto` GA), IDE plugins (VS Code, JetBrains), an **MCP server** form factor so any
  MCP client gets subproto decisions without a base_url edit, OTel/Prometheus exporters,
  `subproto doctor` wiring-linter for the seven agents in WIRING.md.
- **Unfair:** distribution is a moat. Being installable *and* embeddable (MCP, IDE, CLI,
  proxy) means the agent ecosystem's own growth installs us.

### v10 — Verifiable correctness & safety guarantees
- **Ships:** a formal-ish **context-retention contract** (recent turns, open errors, and
  the active tool result are provably never lost), a signed **decision audit trail** +
  one-command **replay** of any session, red-team eval suites that fail CI if task success
  can regress, and a per-turn **correctness budget** the optimiser (v5) must respect.
- **Unfair:** the top fear about token-cutting is "it'll break my agent." Making
  *non-regression provable + replayable* converts our biggest objection into our
  biggest selling point — and it's research-grade work a copycat won't fund.

### v11 — The dataset & models become a product
- **Ships:** an **open, licensed routing-decision benchmark + dataset release** with a
  public leaderboard; a routing-**model marketplace** (anyone can publish a `ModelAdapter`
  / LoRA for the registry); a short paper/tech report (method + measured numbers).
- **Unfair:** we *become the reference* for "coding-agent routing decisions." Whoever owns
  the benchmark and the labelled corpus owns the category — the terminal form of the
  flywheel. Every competitor's "better model" is scored on *our* leaderboard.

### v12 — Autopilot cost automation
- **Ships:** closed-loop policy — "target 35% input-token savings, ≤0.5% pass-rate
  delta" — the system enforces, self-A/Bs, and tunes its own thresholds against live
  measured precision; drift detection + auto-rollback; per-repo cost dashboards.
- **Unfair:** moves us from a tool you operate to an autonomous layer that manages spend
  for you — the outcome people actually want, backed by v3+v4+v10 they can't get elsewhere.

### v13 — The standard (System One for agents)
- **Ships:** an open protocol/spec contribution ("System One decision layer for coding
  agents") the ecosystem can implement against; SDK; partner integrations with agent
  vendors; the SPI (v2) as the de-facto interface for on-device decision models.
- **Unfair:** when your architecture becomes the standard, you're not competing — the
  market is routing *through* you.

---

## 3. Cadence & the 8-month horizon

Aggressive but honest sequencing (~2 versions/month, minor releases between):

| Months | Versions | Net effect |
|---|---|---|
| M1–2 | **v2, v3** | Model-agnostic core + a trusted, billed hero number. (Turns today's mock −29.9% into a real, published one.) |
| M3–4 | **v4, v5** | Flywheel running (continuous fine-tunes) + the Context Compiler. (**v5 shipped early** — mock-measured, opt-in; v4 still to come.) |
| M5–6 | **v6, v7** | Team/fleet adoption + cache-*positive* cost wins. |
| M7–8 | **v8, v9, v10** | Multi-agent + everywhere-installed + provably-safe. |
| M8+ (stretch) | **v11, v12, v13** | Dataset-as-product, autopilot, and the standard. |

**The lead logic:** by the ~month-2 mark we have the two things a copycat cannot
bootstrap quickly — a real pass-rate-held measurement (v3) and a pluggable model core
(v2). By month 4 the **dataset** is compounding on live traffic (v4) that only a layer in
front of *all* agents can collect. By month 8 we're provably safe (v10), installed
everywhere (v9), and multi-agent (v8) simultaneously. A regex token-killer or a
single-model proxy stays ~4 versions behind the entire time because they're missing the
data + measurement + pluggability base on which every later version is built.

**Gates that are human/external (never auto-run):** weight downloads (v2), any real API
spend (v3, v12), the open dataset licence + paper (v11), and every public launch/upload.
These are surfaced in [`TODO.md`](TODO.md) and stay `[~]` until approved.

---

## 4. Why Laya is not (and must not be) the bet — the de-fixation rules

1. **The SPI is the spine (v2).** No version may add a hard dependency on one model.
   Laya is `ModelAdapter` #1, the default reference, not a load-bearing name.
2. **Naming follows:** product copy says "a tiny, swappable **System One** decision model
   (Laya today)," never "the Laya proxy." `SPEC.md` FR-4 and README are updated to match.
3. **Ride, don't marry:** when OpenJev/djev/SemIf/next-gen land, adding them is one adapter
   module; the ablation (v2) then *selects* the best per slot from live evidence.
4. **Our fine-tunes are first-class:** the LoRA from the dataset (v4) is often the best
   scorer — and it's ours, trained on data nobody else has. That, not a base model, is
   the defensible asset.

---

## 5. Risks across the long roadmap (and mitigations)

| Risk | Reality | Mitigation |
|---|---|---|
| Model commoditises / better tiny model appears | Likely, fast | That's the *point* of v2 SPI — every new model is an upgrade we adopt in a day. |
| Cache/prompt-provider economics shift | Anthropic/OpenAI pricing moves | I2 + v7 makes us cache-*positive*; report cache-hit delta always. |
| "It'll break my agent" adoption fear | Real | I3 + v10 provable retention + replay + correctness budget. |
| Team/fleet = scope creep + security surface | Enterprise pulls | v6 is opt-in, local-first, audited; no mandatory upload (I4). |
| Data moat needs real usage to compound | Chicken-and-egg | v3's trusted number + v9's zero-config install drive installs; v4 starts the flywheel. |
| Overclaiming on an 8-month roadmap | I6 threat | This doc is labelled *planned*; nothing says "shipped" until `run_tests.sh` green on it. |

---

## 6. Definition of "we are uncatchable for 8 months"

Reached when, cumulatively: (1) v2 pluggable core + ≥3 live adapters; (2) a published
billed pass-rate-held number with CI (v3); (3) the label→fine-tune flywheel running on
real traffic (v4); (4) the joint Context Compiler beating per-slot baselines (v5); (5)
provably-safe + replayable (v10); and (6) installable across CLI/proxy/MCP/IDE (v9). Each
is gated on a `run_tests.sh`-green, I6-honest artifact — never on a claim.
