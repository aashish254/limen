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
| `Router` | *automatically* picks an adapter per slot per turn from measured precision + latency budget; can ensemble two tiny models | **Not done — this is the v3+ work.** Selection is manual per slot today; the per-decision `backend` + `decision_ms` the router needs to stop being manual are already recorded. |

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

### v4 — The learning loop goes continuous (personal + foundation routing models)
- **Ships:** automatic label capture (implicit: was the turn re-run / corrected / did the
  user toggle a drop back?), a scheduled LoRA **retraining cadence** via
  `train/finetune_mlx.py`, model versioning with rollback, and two model tiers: a shared
  **foundation** routing model + an opt-in **per-user personal** LoRA that specialises to
  their repos/tools. A/B: personal vs foundation, measured.
- **Unfair:** every user's usage trains *their* model and (opt-in) the foundation one.
  The dataset now compounds faster than any single team can hand-label. This is the flywheel
  spinning — the thing the spec calls "the moat" (SPEC §1.2).

### v5 — The Context Compiler (joint multi-slot optimisation)
- **Ships:** stop deciding tool/compact/context/effort independently; a joint optimiser
  maximises expected task success subject to a token budget, using graph + learned scores
  together. A "compile this turn" pass that emits a minimal, cache-safe prompt + a
  machine-readable **retention proof** (what was dropped, why, reversible pointer).
- **Unfair:** transforms subproto from four heuristics in a trenchcoat into a single
  optimiser whose objective is *measured success per dollar* — much harder to clone than
  four rules, and it strictly dominates per-slot tools.

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
| M1–2 | **v2, v3** | Model-agnostic core + a trusted, billed hero number. (Turns today's mock −30.2% into a real, published one.) |
| M3–4 | **v4, v5** | Flywheel running (continuous fine-tunes) + the Context Compiler. |
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
