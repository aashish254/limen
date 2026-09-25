<div align="center">

# subproto

**A System One layer for coding agents — decide before you pay.**

A local, OpenAI- / Anthropic- / Gemini-compatible proxy (chat, `messages`,
`generateContent`, and `responses`) that sits in front of Claude Code,
Codex CLI, Gemini CLI, Cline, OpenCode, aider and anything else that honours a
`base_url`. It measures exactly where your agent's tokens go, then — one routing
decision at a time — removes them using a tiny, **swappable System One decision
model** ([Laya](https://huggingface.co/convaiinnovations/laya) today; OpenJev, MLX
LoRAs, GGUF or any `http://` classifier tomorrow) instead of your frontier model.

*Faster first token, fewer replayed tokens, and a fine-tuning dataset you build
just by using it.*

[Install](#install) · [30-second demo](#quick-start) · [How it works](#how-it-works) · [Wiring your agent](WIRING.md) · [Benchmark](#benchmark) · [Roadmap v2→v13](ROADMAP.md)

The full product & engineering spec — goal, requirements, success criteria — lives in
[`SPEC.md`](SPEC.md), and the forward plan (why there's no room to catch us for ~8
months) lives in [`ROADMAP.md`](ROADMAP.md).

</div>

---

Frontier coding agents are expensive for two reasons that have nothing to do with
intelligence:

1. **They replay enormous, mostly-irrelevant context on every turn** — a 40k-token
   system prompt, 20+ tool schemas you never call this turn, and multi-kilobyte
   tool results scrolled past ages ago.
2. **Every one of those turns is decided by the big model**, when the decision
   "is this file relevant?" or "can I drop this old log?" is a *classification*,
   not a generation task.

subproto offloads those classifications to a tiny **System One** decision model — a
~300–400M "state in → typed decision out" classifier, **swappable** (Laya, the Apache-2.0
open alternative to [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
is the first one we wire up; any newer or better small model drops in via the adapter
SPI) — that answers in tens of milliseconds, and only keeps the reasoning that actually
needs the frontier model.

## Why this exists

A System One / System Two split is the thing making Jev fast. subproto brings the
same idea to the *harness* around any coding agent, without requiring you to change
models, vendors, or your editor:

| | Plain agent | subproto |
|---|---|---|
| "which of 24 tools matters this turn?" | paid in full every request | tiny triage decision |
| "do I need all 40k tokens of history?" | replayed regardless | old tool results compacted |
| "should I read these 8 files?" | agent greps → many big-model calls | offline code graph answers it |
| "simple rename or gnarly migration?" | always frontier | routed by measured complexity |

### Not another router, not a regex token-killer

This space is live and crowded, so the honest comparison (full table in
[`SPEC.md` §1.3](SPEC.md)):

- **vs [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)** — Jev is
  a *closed cloud service* you build apps **with**. subproto is the open, local
  **harness that sits in front of agents you didn't write**, with a swappable open base.
- **vs [RTK](https://github.com/rtk-ai/rtk) and other "token killers"** — they compress
  *bash stdout* with fixed rules and don't report task success. subproto triages the
  **whole turn** (tools, history, files, model tier), learns from your labels, and
  publishes a **pass-rate–held** number, not a partial one.
- **vs LiteLLM / OpenRouter / OmniRoute / RouteLLM** — routers pick *which model*
  answers; subproto decides *what that model is forced to read*, one layer lower.
- **vs [Laya](https://huggingface.co/convaiinnovations/laya)** — Laya is the ~400M base
  *model*; subproto is the proxy + telemetry + label→fine-tune loop that turns it into a
  routing model trained on **your** traffic. That dataset is the moat.

## How it works

`subproto up` starts a transparent proxy on `http://127.0.0.1:8787`. Point any
agent at it and **it passes traffic byte-for-byte until you tell it otherwise**.
Each request flows through four *decision slots*:

```
request ──▶ [ analyze prompt shape ] ──▶ tool_gate ──▶ compact ──▶ context ──▶ effort ──▶ upstream
                                             │            │           │           │
                                        drop unused   drop old    code-graph   route to
                                        tool schemas  context     picks files  small/big
```

- **tool_gate** — keeps only the tool schemas relevant to this turn.
- **compact** — drops stale tool results / assistant chatter, always protecting the
  last few turns (correctness first).
- **context** — an offline [graphify](#)-style import/symbol graph so the agent can
  navigate straight to the right file instead of reading the whole repo.
- **effort** — routes trivial turns to a smaller/faster model.

Every slot runs in **observation mode by default**: it records what *would* be
dropped, so your telemetry corpus is honest before any behaviour change is trusted.
Enforce a slot explicitly with `SUBPROTO_APPLY=tool_gate,compact`.

> **Prompt-cache safety.** subproto never rewrites the prompt prefix mid-stream —
> busting Anthropic/OpenAI prompt caching costs ~10× more than it saves. Context
> injection is appended after the cached prefix; compaction only fires past a size
> threshold where a fresh cache is already cheaper than a replay.

## Install

Works from source today; a PyPI/pipx package is pending the first tagged release.

```bash
git clone https://github.com/<you>/subproto && cd subproto
./install.sh                 # symlinks a `subproto` shim onto your PATH
# or, without installing:  python3 -m subproto  (stdlib only, Python 3.9+)
```

<details>
<summary>Once published</summary>

```bash
pipx install subproto        # or: pip install subproto
```
</details>

## Quick start

```bash
# 1. See the whole idea with zero setup — replays synthetic agent traffic
#    through the proxy against a local mock upstream. No key, no spend.
subproto demo --slots --audit

# 2. Run for real. Point Claude Code at it (your key stays with Anthropic):
subproto up --store-bodies --slots &
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
claude           # ...do your normal work

# 3. Where did the tokens go?
subproto report

# 4. What would the slots have removed? (offline, over your recorded traffic)
subproto audit

# 5. Watch it work — a live terminal meter of tokens/$ saved this session.
subproto live                 # ANSI meter, tails telemetry; --once for a snapshot

# 6. Build the fine-tuning set (de-duplicated, seeded train/val split),
#    then enforce when you trust it.
subproto export               # writes ~/.subproto/dataset-YYYYMMDD.jsonl
subproto split                # -> train.jsonl / val.jsonl in the Laya supervision format
SUBPROTO_APPLY=tool_gate subproto up --slots
```

`subproto inject claude|codex|gemini|aider` prints the exact env vars for each tool.
For the precise `base_url`/endpoint every supported agent uses, see
[`WIRING.md`](WIRING.md).

## The code graph

```bash
subproto graph ~/src/myrepo          # index imports + symbols once, ~seconds
subproto where "fix the payment retry refund"
#  7.0  app/payments/retry.py        path:payment,path:retry,sym:refund
#  5.0  app/services/gateway_client  path:gateway,sym:retry
```

The agent stops paying a frontier model to `grep` its way around the repo.

## Benchmark

Because the whole pitch is "fewer tokens, same correctness," every release ships
with the number — and we separate *measured* from *projected* (design tenet: never
overclaim).

```bash
python bench/live.py --mock    # measured: replay-vs-enforce against the local mock
python bench/ab.py   --mock    # projected: per-slot headroom over recorded prompt shapes
```

**Measured on the mock harness** (`bench/live_results.md`, n=20 SWE-bench-shaped
tasks, bootstrap 95% CI, $0 / no key — the enforced body is echoed back so the
token delta is *delivered*, not estimated):

| metric | observe | enforce | delta |
|---|--:|--:|--:|
| input tokens / task | 19,704 | 13,728 | **−30.2%** `[−28.5, −31.7]` |
| task pass-rate | 100% | 100% | **+0.0 pp** (held) |

**Projected on heuristic prompt shapes** (`bench/results.md`): −32.3% if every slot
enforces. These are prompt-shape projections, not billed savings, and latency is a
request-shape proxy against the mock rather than a real time-to-first-token.

The hero metric we publish once against a real SWE-bench-style suite + grader and
real provider billing:

> **same 20 tasks, same model, held pass-rate: −X% input tokens, −Y ms p50 to first
> token, $A → $B** (billed). Until that exists, the mock-measured −30.2% and the
> projected −32.3% above are exactly what they're labelled — you can reproduce both
> locally in one command, no key.

## Bring your own System One model (not just Laya)

subproto ships with heuristics so it is useful on day zero, and the decision model is a
**pluggable backend** — Laya is just adapter #1, so when a better small model lands you
swap it, you don't fork the project (see [`ROADMAP.md`](ROADMAP.md) §1). It also ships a
local scoring server that speaks the adapter's `/health` + `/score` contract (deterministic
lexical scorer, with an MLX path guarded behind an import check):

```bash
python -m subproto.laya_server --port 8000        # the mock-verifiable backend
LAYA_URL=http://localhost:8000 subproto up --slots   # any /health+/score server, Laya or not
```

The `subproto/laya.py` adapter asks the model per-candidate keep/drop questions and
falls back to heuristics if it's unreachable. `bench/ablation.py` reports laya-vs-
heuristic agreement on labelled ground truth — today both are lexical, so they agree
and we say so; the delta becomes meaningful when a quantized checkpoint replaces the
scorer. The full loop from usage to a specialised model is:

```bash
subproto export   # telemetry -> JSONL of every decision
subproto split    # -> train.jsonl / val.jsonl (seeded, de-duplicated by body_sha)
train/finetune_mlx.py   # LoRA fine-tune (guarded: exits honestly without MLX + weights)
```

## Design tenets

1. **Measure before you change anything.** Telemetry first, enforcement behind a flag.
2. **Never trade correctness for tokens silently.** The tail of a conversation is
   sacred; every drop is logged and reversible.
3. **Local-first.** Decisions happen on your machine; only the actual prompt goes to
   your provider, exactly as it does today.
4. **The dataset is the moat.** Usage compounds into a routing-decision corpus that
   nobody else has — the thing that makes the tiny model beat the zero-shot baseline.

## Roadmap

The full forward plan — **v2→v13**, engineered so the pluggable model core, the
labelled-decision dataset, the pass-rate-held measurement and the breadth of
integrations compound into an ~8-month lead — is its own spec:
**[`ROADMAP.md`](ROADMAP.md)**. The short version, v1:

- [x] Transparent OpenAI + Anthropic proxy with SSE-aware usage capture
- [x] Gemini native + OpenAI `responses` dialects (usage, routing, mock, tests)
- [x] Prompt-shape telemetry + SQLite + token/cost/waste report
- [x] Four decision slots (tool_gate, compact, context, effort) in observation mode
- [x] graphify-lite code graph (`subproto graph` / `subproto where`)
- [x] Dataset export + human-label loop (`subproto export` / `subproto label`)
- [x] Deterministic, de-duplicated train/val split (`subproto split`)
- [x] Guarded MLX LoRA fine-tune script (`train/finetune_mlx.py`)
- [x] Local Laya scoring server behind the adapter (`python -m subproto.laya_server`)
- [x] Pass-rate–held A/B harness on SWE-bench-shaped tasks (mock; `bench/live.py`)
- [x] TUI overlay: live "you just saved 38% this session" meter (`subproto live`)
- [ ] Real quantized MLX Laya checkpoint on-device + published latency curve
      (needs ~808MB HF weights + Apple MLX runtime — external)
- [ ] Hero metric on a real SWE-bench-style suite + billed provider savings
      (needs non-zero API spend — external; mock-measured number ships meanwhile)
- [ ] Tagged PyPI release + launch post + demo GIF (external)

## Contributing

The highest-leverage contribution is **routing-decision labels**: run real agent
traffic, `subproto export`, `subproto label` the good/bad calls, and open a PR with
the JSONL. That is how a fast base model becomes an accurate one.

## License

Apache-2.0 — see [LICENSE](LICENSE).
