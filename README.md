<div align="center">

# subproto

**A System One layer for coding agents — decide before you pay.**

A local, OpenAI/Anthropic-compatible proxy that sits in front of Claude Code,
Codex CLI, Gemini CLI, Cline, OpenCode, aider and anything else that honours a
`base_url`. It measures exactly where your agent's tokens go, then — one routing
decision at a time — removes them using a tiny [Laya](https://huggingface.co/convai/laya)-style
decision model instead of your frontier model.

*Faster first token, fewer replayed tokens, and a fine-tuning dataset you build
just by using it.*

[Install](#install) · [30-second demo](#quick-start) · [How it works](#how-it-works) · [Benchmark](#benchmark) · [Roadmap](#roadmap)

The full product & engineering spec — goal, requirements, success criteria — lives in
[`SPEC.md`](SPEC.md).

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

subproto offloads those classifications to a 300–400M decision model (Laya, the
Apache-2.0 open alternative to [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev))
that answers in tens of milliseconds, and only keeps the reasoning that actually
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

# 5. Build the fine-tuning set, then enforce when you trust it.
subproto export            # writes ~/.subproto/dataset-YYYYMMDD.jsonl
SUBPROTO_APPLY=tool_gate subproto up --slots
```

`subproto inject claude|codex|gemini|aider` prints the exact env vars for each tool.

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
with the number. `bench/ab.py` replays a fixed workload in observe vs enforce mode
and reports the projected reduction per slot:

```bash
python bench/ab.py --mock        # reproducible, no API key
python bench/ab.py --home ~/.subproto   # your real traffic
```

The hero metric we optimise and publish:

> **same 20 tasks, same model, held pass-rate: −X% input tokens, −Y ms p50 to first
> token, $A → $B.**  (Filled in per release from `bench/results.md`.)

Until we have a stable pass-rate harness we label these as *projections*, not
claims — you can verify any of them locally in one command.

## Bring your own Laya

subproto ships with heuristics so it is useful on day zero. When you have a
[Laya](https://huggingface.co/convai/laya) scoring server up:

```bash
LAYA_URL=http://localhost:8000 subproto up --slots
```

The `subproto/laya.py` adapter asks the model per-candidate keep/drop questions and
falls back to heuristics if it's unreachable — the exported dataset is exactly the
supervision signal for the fine-tune that closes the zero-shot accuracy gap.

## Design tenets

1. **Measure before you change anything.** Telemetry first, enforcement behind a flag.
2. **Never trade correctness for tokens silently.** The tail of a conversation is
   sacred; every drop is logged and reversible.
3. **Local-first.** Decisions happen on your machine; only the actual prompt goes to
   your provider, exactly as it does today.
4. **The dataset is the moat.** Usage compounds into a routing-decision corpus that
   nobody else has — the thing that makes the tiny model beat the zero-shot baseline.

## Roadmap

- [x] Transparent OpenAI + Anthropic proxy with SSE-aware usage capture
- [x] Prompt-shape telemetry + SQLite + token/cost/waste report
- [x] Four decision slots (tool_gate, compact, context, effort) in observation mode
- [x] graphify-lite code graph (`subproto graph` / `subproto where`)
- [x] Dataset export + human-label loop (`subproto export` / `subproto label`)
- [ ] Live Laya inference backend (beyond the adapter) and the LoRA fine-tune
- [ ] Pass-rate–held A/B harness wired to SWE-bench-style tasks
- [ ] TUI overlay: live "you just saved 38% this session" meter
- [ ] Providers: Gemini native + OpenAI `responses` streaming edge cases

## Contributing

The highest-leverage contribution is **routing-decision labels**: run real agent
traffic, `subproto export`, `subproto label` the good/bad calls, and open a PR with
the JSONL. That is how a fast base model becomes an accurate one.

## License

Apache-2.0 — see [LICENSE](LICENSE).
