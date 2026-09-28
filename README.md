<div align="center">

![Limen — a threshold mark: a bar mostly unlit, with the crossing line in red](https://github.com/aashish254/limen/raw/main/docs/assets/logo.png)

# Limen

*the threshold a stimulus has to cross before it is noticed* — here, the decision an
agent makes before it spends anything

[![CI](https://github.com/aashish254/limen/actions/workflows/ci.yml/badge.svg)](https://github.com/aashish254/limen/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/subproto)](https://pypi.org/project/subproto/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/aashish254/limen/blob/main/LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-informational)](https://github.com/aashish254/limen/blob/main/pyproject.toml)
[![Runtime dependencies: 0](https://img.shields.io/badge/dependencies-none-informational)](https://github.com/aashish254/limen/blob/main/pyproject.toml)

**A System One layer for coding agents — decide before you pay.**

A local, OpenAI- / Anthropic- / Gemini-compatible proxy (chat, `messages`,
`generateContent`, and `responses`) that sits in front of Claude Code,
Codex CLI, Gemini CLI, Cline, OpenCode, aider and anything else that honours a
`base_url`. It measures exactly where your agent's tokens go, then — one routing
decision at a time — removes them using a tiny, **swappable System One decision
model** ([Laya](https://huggingface.co/convaiinnovations/laya) today — the real
checkpoint, on torch CPU; OpenJev, a GGUF server, any `http://` classifier), or the
heuristics it ships with, instead of your frontier model.

*Faster first token, fewer replayed tokens, and a fine-tuning dataset you build
just by using it.*

[Install](#install) · [30-second demo](#quick-start) · [How it works](#how-it-works) · [Wiring your agent](https://github.com/aashish254/limen/blob/main/WIRING.md) · [Reference docs](https://github.com/aashish254/limen/blob/main/docs/index.md) · [Live measurement page](https://aashish254.github.io/limen/) · [Benchmark](#benchmark) · [Roadmap](#roadmap)

![The first thirty seconds: doctor reads the machine, demo replays agent traffic, live prices the headroom](https://github.com/aashish254/limen/raw/main/docs/assets/first-30-seconds.gif)

*The first thirty seconds, on an installed wheel: `subproto --version`, then `doctor`
reads the machine, `demo` replays 17 synthetic agent requests through the proxy against a
local mock, and `live` prices the headroom those requests still carry — 32% here, while
the slots are **observing**, not enforcing. No key, and nothing billed: the `$0.59` on
screen is what the mock's pricing says the traffic *would* have cost, and `$0.40` is what
the same 17 requests would cost with the headroom removed.
[How this was captured](https://github.com/aashish254/limen/blob/main/docs/assets/first-30-seconds.sh).*

What this project promises is enforced by code, not by a plan document: the six invariants
are named tests in [`subproto/tests/test_invariants.py`](https://github.com/aashish254/limen/blob/main/subproto/tests/test_invariants.py),
every command is documented in [`docs/`](https://github.com/aashish254/limen/tree/main/docs),
and every number is labelled measured, projected or gated on the
[live measurement page](https://aashish254.github.io/limen/#unmeasured).

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

This space is live and crowded, so the honest comparison:

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

## Names

**Limen** is the project: this repository, the docs and the landing page. **`subproto`**
is the package and the command — `pip install subproto`, `subproto demo` — and it is not
going anywhere, because every wiring guide, config example and muscle memory in the wild
says `subproto`. One brand for people to point at, one name for a shell to type.

## Install

Python 3.9 or newer, **zero runtime dependencies**, stdlib only, no key, no network
beyond the provider you point it at. Verified on macOS and Linux; Windows runs behind an
explicit "not tested by us" (see [`CONTRIBUTING.md`](https://github.com/aashish254/limen/blob/main/CONTRIBUTING.md)).

```bash
pip install subproto                 # or: pipx install subproto
# or from the clone:
git clone https://github.com/aashish254/limen && cd limen
./install.sh                 # puts a `subproto` shim on your PATH ($HOME/.local/bin)
# or, without installing anything:  python3 -m subproto …
# or, from the clone as a package:  pip install .   (pipx-ready: it declares one console script)
```

Then ask it what it can see:

```bash
subproto doctor              # python, data dir, a free port, which backends answer
```

**`subproto` is on PyPI** (`pypi.org/project/subproto`), so `pip install subproto`
and `pipx install subproto` are the shortest route — same zero runtime dependencies, same
one console script. *No version is named here on purpose: this paragraph travels inside the
release it would date, and the badge above reads what the index actually serves.*
`bash run_tests.sh` from the clone is the verification path for the
source you are reading; it needs no key, spends nothing, and takes about two hours on a
32 GB laptop because the mutation gate alone runs 157 mutants once per interpreter present.

## Quick start

Two commands is the whole pitch, and neither spends anything:

```bash
subproto demo --slots --audit --home /tmp/subproto-try
subproto report --home /tmp/subproto-try
```

`--home` is the part you would otherwise miss: without it `demo` writes to a fresh
directory it picks for itself, and the bare `subproto report` after it reads your
default `~/.subproto` instead — 0 requests, or worse, someone else's traffic. The
`demo` page prints the exact `--home` line to run; these two have it already.

`demo` runs against `fakeup`, a local mock upstream, so you get the real report pages —
tool counts, per-slot decisions, tokens kept vs cut, p50 decision latency — with no API
key and no bill. Everything below is what you do after those two.

Nothing on this list writes outside its own data directory: `SUBPROTO_HOME` moves it
(any absolute path, created on first use), and `subproto doctor` prints what it found —
the Python it is running under, where the data lives, whether the port is free, which
backends answer. Run that first when a command surprises you.

```bash
# 1. See the whole idea with zero setup — replays synthetic agent traffic
#    through the proxy against a local mock upstream. No key, no spend.
subproto demo --slots --audit --home /tmp/subproto-try

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

# 7. Choose (or swap) the small model making those decisions.
subproto models               # every System One backend + which one is active
subproto up --slots --model openjev

# 8. Close the loop: let the recorded traffic judge the drops it saw, then ask
#    whether a fine-tune is worth starting. Both print; neither spends anything.
subproto learn                # eviction regret: which cuts the wire disagreed with
subproto retrain              # the cadence verdict + the exact trainer command, runs nothing
```

`subproto inject claude|codex|gemini|aider` prints the exact env vars for each tool.
For the precise `base_url`/endpoint every supported agent uses, see
[`WIRING.md`](https://github.com/aashish254/limen/blob/main/WIRING.md).

## What it prints

Three pages, photographed from the installed wheel rather than typed out by hand. The
first two are the same 17 synthetic requests the demo replays against the in-repo mock, so
every price on screen is what the mock's tariff says that traffic *would* have cost: `$0`
of API spend, no key.

**`subproto demo --slots`, then `subproto report`** — where the input tokens came from,
what each model and client cost, the headroom the slots price without touching, and the
decision latency the routing model actually answered at.

![report after the demo: 179,280 tok billed input, p50 1.2ms slot decision latency, 56.1% of input in tool schemas](https://github.com/aashish254/limen/raw/main/docs/assets/terminal/demo-slots.png)

**`subproto show 3`** — one request and every decision it carried, with the reason beside
each cut. `delivered` is a verdict the model made and the compiler paid for; `potential` is
advice only. The `body present ~/…` line is the stored request, so a pointer can be opened
and checked rather than trusted.

![show 3: tool_gate delivered 12 kept 12 cut 4,145 tok, compact delivered 10 kept 3 cut 1,200 tok, effort potential](https://github.com/aashish254/limen/raw/main/docs/assets/terminal/show-compiled.png)

**`subproto doctor --port 0`** — the machine readout a stranger gets when something is
wrong: six checks, each one measured rather than assumed, two state words that need
fixing, and no key material on the page.

![doctor: 6 checks, 2 to fix — python ok, data dir ok, port ok, provider keys warn, laya runtime not ready, backend not configured](https://github.com/aashish254/limen/raw/main/docs/assets/terminal/doctor.png)

`live`'s meter and the `report` page over traffic with no decisions are in
[`docs/assets/terminal/`](https://github.com/aashish254/limen/tree/main/docs/assets/terminal), and
[`docs/assets/terminal-stills.sh`](https://github.com/aashish254/limen/blob/main/docs/assets/terminal-stills.sh) re-runs every command
above and re-draws every picture — including the row count, which each page measures for
itself so nothing scrolls out of the frame.

## The code graph

```bash
subproto graph ~/src/myrepo          # index imports + symbols once, ~seconds
subproto where "fix the payment retry refund"
#  7.0  app/payments/retry.py        path:payment,path:retry,sym:refund
#  5.0  app/services/gateway_client  path:gateway,sym:retry
```

The agent stops paying a frontier model to `grep` its way around the repo. Docs count
too: a markdown/rst heading or a SQL `CREATE TABLE` name is that file's symbol, so a
"where does the refund ledger get written" question can rank the migration, not just the
module. **So do data files** — `fixtures/refunds.json` is indexed by its nested key paths
(`refunds[].amount_cents`) and a `.csv`/`.tsv` by its header columns, which is what a task
actually names ("the refund fixture", `amount_cents`). A file's *values* stay out of the
index on purpose: they are what the agent opens the file to find, and indexing them would
rank every fixture for every word it happens to contain. `--graph` takes your repo *or* a
saved index, for both `where` and `compile`.

## One budget, not four — the Context Compiler

The four slots above spend *independent* budgets and cannot see each other, so
`compact` can evict a 2.6k-token tool result that the graph ranks #1 for the current
task while `context` pays again to name that same file in prose. `SUBPROTO_COMPILE=on`
replaces the three editing slots with **one value-per-token knapsack over one budget**,
over `{message, tool spec, file note}` harvested from the same heuristics:

```bash
subproto compile "refactor the retry path in app/payments/retry.py" \
  --graph subproto/tests/fixtures/sample_repo --budget 6000
```
```
subproto compile — compiled turn, one budget over messages, tools and files

indexing subproto/tests/fixtures/sample_repo …
  3 files, 0 import edges, 0.0s
                       task  refactor the retry path in app/payments/retry.py
                     budget      6,000 tok   pool was 11,313 tok
                      spent      5,945 tok   −47.4%
                  protected      4,959 tok
                             booked before the optimiser ran

  message                     11 kept       3 dropped
  tool                        12 kept      12 dropped
  file                         3 kept       0 dropped

  what was cut, and what beat it
    message   tool_result#5                389 tok value/token 0.002987 < kept floor 0.002988
    message   tool_result#7                440 tok value/token 0.002700 < kept floor 0.002988
    tool      exitplanmode                 339 tok value/token 0.000000 < kept floor 0.002988
    …    (3 message rows, 12 tool rows, 0 file rows; `--json` carries all 15)
```

(`--graph` takes your repo *or* a saved index; the path above is this repo's own test
fixture, so the command is copy-pasteable from a fresh clone.)

Every drop carries a reversible pointer (`body_sha` + message index) back into
telemetry, and the last 4 turns / core tools are booked before the optimiser runs — if
the protected set alone doesn't fit, it says so rather than truncating your tail:

```
subproto compile — over budget: nothing was cut

  ! protected set needs 4,959 tok, budget is 1,500 — the tail is sacred (I3)
```

That address is walkable. `subproto show <request_id>` reads one recorded request back
out of telemetry, resolves every drop's pointer into the body the client actually sent,
and prints the item that lost beside the reason it lost:

```bash
SUBPROTO_COMPILE=on subproto demo --slots --home /tmp/subproto-readme  # 17 mock turns
subproto show 3 --home /tmp/subproto-readme
```
```
subproto show — request 3, anthropic /v1/messages, claude-sonnet-4-5

                     client   claude-code
                   recorded  2026-09-27 16:11:51
                     stream  yes
                     status           200
                       ttfb          0 ms
                    latency          5 ms
               billed input        11,935  (2,464 cached)
                     output           320
                      spend       $0.0413
                       body  present  /tmp/subproto-readme/bodies/anthropic_96a260a6d0ba.json.gz

  decisions  3, and it could save 5,345 tok
                  tool_gate  potential  12 kept  12 cut  4,145 tok  2.4 ms  compiled
    tool             exitplanmode                 339 tok value/token 0.000000 < kept floor 0.003003
                             Present the current plan for user approval. parameter 0 accepts…
    tool             killshell                    334 tok value/token 0.000000 < kept floor 0.003003
                             Terminate a running background shell session. parameter 0…
    …
    tool             mcp__db__schema              343 tok value/token 0.000000 < kept floor 0.003003
                             Print the schema of a database table. parameter 0 accepts a JSON…
      4 more in --json
                    compact  potential  10 kept  3 cut  1,200 tok  0.0 ms  compiled
    message          tool_result#5                381 tok error, tool_result, names-task-file
                             raise RetryExhausted(last_error) from err INFO connecting to…
    message          tool_result#7                410 tok error, tool_result, names-task-file
                             DEBUG serialising payload {'amount': 4210, 'currency': 'usd',…
    message          tool_result#3                409 tok error, tool_result, names-task-file
                             raise RetryExhausted(last_error) from err DEBUG serialising…
                     effort  potential  0 tok  0.0 ms  heuristic
                             tier medium, advisory-only: multi-file
```

Every block on that page adds up on its own: 12 rows of `tool_gate` sum to the 4,145 tok
beside its name and 3 rows of `compact` to its 1,200, and no cut is listed twice — the
joint proof the compiler solves once is attached to one record, so each slot's block is
projected onto the pool it paid for (S46/B6).

Three things on that page are deliberate. It reads `potential` rather than `delivered`
because the demo proxy observes without editing the request — the saving is not sold as
banked (I6). The quoted text is a *clip*, cut at the grid's own margin; `--json` carries
each drop's text whole. And a home recorded without `--store-bodies` still prints the
pointers and the counts, because those are measured, and says the body was never stored
rather than inventing a quote.

What moves between replays of that command, measured: the `recorded` stamp, the
per-decision milliseconds, and the body's sha — the mock's system prefix carries
`started=… turn=… minute=…`, so crossing a minute changes the bytes sent, and the sha is
over exactly those. Every count and every token figure held across two back-to-back
runs. The two kinds are named separately because only one of them measures the mechanism.

Why bother, measured (see [Benchmark](#benchmark) for the repro command): **+17.0 pp
precision of surviving evidence at 19.3% fewer input tokens**, and on the hard set
(`bench/tasks.hard.jsonl`) per-slot loses the file the task is about — **pass-rate 0%,
recall 2.7%** — while the compiler holds **100% / 100%** at *lower* spend.

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
| input tokens / task | 19,746 | 13,822 | **−29.9%** `[28.2, 31.3]` |
| task pass-rate | 100% | 100% | **+0.0 pp** (held) |

With the compiler on, a third arm is measured against the per-slot arm on the *same*
tasks and the *same* graph (`--tasks bench/tasks.hard.jsonl` for the hard set):

| metric | per-slot | compiled | delta |
|---|--:|--:|--:|
| input tokens / task | 13,822 | 11,133 | **−19.3%** `[18.0, 20.9]` |
| p50 request latency (ms) | 6.1 | 6.3 | **+0.2** (slower; drifts +0.2 to +0.4 run to run) |
| recall of required files | 100% | 100% | held |
| precision of what survived | 2.6% | 19.6% | **+17.0 pp** `[+2.8, +35.8]` |
| hard set: pass-rate / recall | **0% / 2.7%** | **100% / 100%** | per-slot VIOLATES I3 |

![The eight plates from the run: what an average request carries, the cut per task family
and per request size, the ablation corpus, pass-rate by family, recall against precision,
the eight hard tasks the per-slot arm loses, and the small model's cost per
decision](https://github.com/aashish254/limen/raw/main/docs/assets/results-grid.png)

*The two tables above, plotted — one plate per question: what a request carries before
anything is decided, what comes off, whether the task still passes, and where the naive
version breaks. Same corpus and same arm means as those tables (19,746 → 13,822 → 11,133
input tokens per task, 2.6% → 19.6% precision), from
`bench/launch_stats.json`: `python3 bench/launch_stats.py --iters 5`, 20 tasks × 5 repeats,
Darwin 27.0.0, commit `0f7a546`. Every plate keeps that stamp. The interactive version and
the rest of the page: [the live measurement page](https://aashish254.github.io/limen/#figures).
The image is a crop of that page, regenerated by
[`docs/assets/make-readme-art.py`](https://github.com/aashish254/limen/blob/main/docs/assets/make-readme-art.py)
after `python3 tools/make_site.py`.*

**Projected on heuristic prompt shapes** (`bench/results.md`): −32.4% if every slot
enforces. These are prompt-shape projections, not billed savings, and latency is a
request-shape proxy against the mock rather than a real time-to-first-token. The
compiler's p50 on the mock is *still slower* (6.1 → 6.3 ms in the run behind
`bench/live_results.md`; the pair drifts by ±0.4 ms between runs), which we print rather
than hide; the token and accuracy numbers are the claim. The overhead is profiled and
partly paid back: a full-message regex scan of every candidate was 1.20 ms/decision and
is now needle-anchored, cutting the paired decision gap from **+1.24 ms to +0.19 ms**
(its best on a quiet host; **+0.32 ms** best / +0.37 median when `run_tests.sh` runs it at
4 rounds with the suite competing for the CPU — measured 2026-09-27, and the gap is
host-load-sensitive by more than its own effect size)
(`python3.11 bench/s32_probe.py`, both arms in one process, minimum over rounds because
a laptop's request latency drifts more than the effect). Parity was the goal and is not
reached — what remains is the graph-coupling scan, which is the thing buying the
+17.0 pp precision above.

The hero metric we publish once against a real SWE-bench-style suite + grader and
real provider billing:

> **same 20 tasks, same model, held pass-rate: −X% input tokens, −Y ms p50 to first
> token, $A → $B** (billed). Until that exists, the mock-measured −29.9% and the
> projected −32.4% above are exactly what they're labelled — you can reproduce both
> locally in one command, no key.

## Bring your own System One model (not just Laya)

subproto ships with heuristics so it is useful on day zero, and the decision model is a
**pluggable backend** — Laya is just adapter #1, so when a better small model lands you
swap it, you don't fork the project. It also ships a
local scoring server that speaks the adapter's `/health` + `/score` contract. It has two
backends: a deterministic lexical scorer (no weights, no torch, the same signal the
heuristics use, and it says it is a stand-in), and **the real Laya checkpoint** —
`pip install "laya[serve]"` in a Python ≥ 3.10 venv (the checkpoint requires it, which is
what the HTTP boundary is for) and `--backend laya` answers each decision with one
`choice` forward pass on torch CPU. Its probabilities are **not** calibrated confidence:
the runtime itself reports the checkpoint's temperatures are invalid, and
`bench/laya_latency.md` prints that warning rather than smoothing it over.

```bash
subproto models                                   # what can I point it at, and what is active?
python3 -m subproto.laya_server --port 8000       # the stand-in: no weights, no torch

# The real checkpoint is the one part of this that is not stdlib: it needs torch and
# Python >= 3.10, so it gets its own venv. Everything below runs from the clone.
python3.11 -m venv .venv-laya && .venv-laya/bin/pip install "laya[serve]"
.venv-laya/bin/python -m subproto.laya_server --backend laya --port 8000

subproto up --slots --model laya --laya-url http://127.0.0.1:8000
.venv-laya/bin/python -m bench.laya_latency --write   # the cost curve it earns
```

The registry in `subproto/systemone/` is the only thing that knows about model
families: `--model laya|openjev|djev|semif|mlx_lora` picks a named adapter, and
`--model http://host:port` (or `SUBPROTO_MODEL=…`) picks **anything** that speaks
the `/health` + `/score` contract without us shipping an entry for it. Every
decision records the label of whatever answered it (`"openjev"`, `"model"`, …),
so the dataset says which model produced each supervision signal, and an
unreachable or absent backend degrades to the heuristics instead of breaking the
proxy. `LAYA_URL` still works — it is now the legacy default for the `laya`
entry.

A slot can also pick its own model, so the cheap classifier answers tool gating
while a stronger one decides what to compact:

```bash
SUBPROTO_MODEL=laya SUBPROTO_MODEL_COMPACT=djev subproto up --slots
# or in .subproto.json: {"model": "laya", "model_by_slot": {"compact": "djev"}}
```

Only `tool_gate` and `compact` ask a model anything — `context` is answered by the
code graph and `effort` by request shape, so `subproto models` refuses to list a
model for them rather than recording a label that decided nothing.

Instead of naming one model and hoping it is good, the **Router** picks per slot from
what has actually been measured: `bench/ablation.py` scores each configured adapter's
slot precision, and the Router routes each un-pinned slot to the best-scoring one under
an optional latency budget (`SUBPROTO_ROUTER=on`, `SUBPROTO_ROUTER_BUDGET_MS=40`). It is
deliberately honest — an adapter with no measured precision is never auto-picked, and
ties fall back to the in-process heuristics rather than spending a network hop, so today
(it only ships with a lexical stand-in that ties the baseline) the Router stays on
heuristics. Explicit `SUBPROTO_MODEL_<slot>` pins always win. `subproto models --router`
shows the plan and the evidence behind it.

```bash
SUBPROTO_ROUTER=on subproto up --slots       # best measured backend per un-pinned slot
subproto models --router                     # what it would pick, and why
```

### Which checkpoint answered? Versions, rollback, and a health gate

Naming a *family* is not enough to evaluate a fine-tune: `laya` today and
`mlx-lora-v2` next month are different answers to the same question, and the telemetry
has to tell them apart. `$SUBPROTO_HOME/models.json` is where a **version** lives:

```bash
subproto models --add mlx-lora-v1 --url http://127.0.0.1:8010 --tier personal \
                --note "LoRA on 1.2k decisions, epoch 3"
subproto models --use mlx-lora-v1      # whatever was active becomes `previous`
subproto models --rollback             # and one command puts it back
subproto report                        # decisions, tokens, p50 ms, approval *per version*
```

Every decision then carries `model_version` beside its `backend` label — in both arms,
and on the decisions the heuristics answered too (`"heuristic"`) — so `subproto report`
can group the corpus by version and `subproto export` carries the column into the
training set. That is the difference between "we ran a fine-tune" and "the fine-tune was
better on `compact`, worse on `tool_gate`, here is the split". A version is also just
another label in the Router's pool, so `foundation` and `personal` compete on measured
precision with no new surface.

Two rules keep the column worth reading:

- **An active version whose `/health` refuses is not used.** Resolution falls to
  `previous`, then to the heuristics, and the reason is printed when the proxy starts
  (`! dead-v1: /health failed (...)`) instead of being left for each decision to imply
  by quietly recording `heuristic`. A version stamped on a decision is the one that
  produced it: after a fallback the stamp moves with the answer, never with the wish.
- **A pin still wins.** `SUBPROTO_MODEL[_<slot>]` outranks the manifest (invariant I1),
  and `models --use` prints that warning rather than letting you believe you switched.

`bench/ablation.py` scores **any registry label** through that same adapter
interface against the heuristic baseline on labelled ground truth. The committed run
scores **the real checkpoint** — `LAYA_URL` pointed at `laya_server --backend laya` —
and it prints a negative result: precision **0.889 vs the heuristic's 0.972**, agreeing
with the heuristic's answer on 27 % of options. An adapter with no configured endpoint
is reported as skipped instead of borrowing the stand-in's number. Read the number
with `corpus_ceiling()` beside it: **7 of this corpus's 10 cases have a gold set equal
to the heuristic's own answer**, so 83 of its 86 decisions cannot move either way, and
the delta that is left is a property of the set rather than a measurement of skill.
That is why the accuracy gate is now about *labels* and not about models. The full loop
from usage to a specialised model is:

```bash
subproto learn    # the traffic judges yesterday's drops -> implicit_labels.json
subproto export   # telemetry -> JSONL of every decision (+ its label + model_version)
subproto split    # -> train.jsonl / val.jsonl (seeded, de-duplicated by body_sha)
subproto retrain  # is a fine-tune worth starting? prints the verdict and runs nothing
subproto retrain --run           # only if the cadence cleared: starts the trainer
subproto models --use lora-v3    # promote a run that actually finished
```

### When is a fine-tune worth starting? The cadence, and what the traffic charged back

Two questions decide it, and neither one is answerable from memory.

**Did the last decisions work?** `subproto learn` reads the recorded bodies and prices each
drop the wire disagreed with — content that was cut and later re-read. On a fresh demo run
with `SUBPROTO_COMPILE=on SUBPROTO_APPLY=tool_gate,compact` it printed:

```
         evicted reads seen            14
          regrettable drops            10  (enforced 10, shadow 0)
           tokens paid back         4,088  (re-reads of the 10 drops that reached the wire)
```

That is the accuracy side of a cut, measured rather than argued: the same 34 compiled
decisions saved 75,999 tok, so the re-reads cost 5.4 per cent of the saving. The 10 drops are
written to `implicit_labels.json` — a store separate from human `subproto label` verdicts,
which always win — and `build_training_split` flips each one to `keep`, so the next dataset
teaches the mistake away. (In shadow mode the identical traffic prints the same 10 drops with
`enforced 0` and nothing paid: `SUBPROTO_APPLY` changes who pays, not what the detector sees.)

**Is there enough new signal to train on?** `subproto retrain` answers from local state and
refuses out loud when the answer is no — a split whose hash has not changed since the last
finished run, a slot under 40 rows, an answer under 8 examples, fewer than 7 days on the
clock:

```
  per slot  (floor 40 rows, 8 in both answers)
    compact      ok        209 rows   177 keep    32 drop
    tool_gate    ok        390 rows   206 keep   184 drop

        decision  ready
```

It runs nothing unless `--run` says so, `training_history.json` records every attempt —
including the refusals, with their reasons — and only a run that *finished* becomes a version
`subproto models --use lora-vN` can activate. Today the trainer it starts exits **3** with
`mlx / mlx-lm not installed`, and that refusal is one honest step short of the whole truth:
`train/finetune_mlx.py` is a `mlx_lm` LoRA script, and the checkpoint we actually serve
is an encoder with a classification head, which that script cannot fine-tune at all (the
architecture was only read after the decision was locked).
Installing MLX would not open the fine-tune; a sequence-classification LoRA over torch/PEFT
is the build that would, and it needs labels before it can be judged. A command that cannot
honour the run says so instead of pretending to train.

## Design tenets

1. **Measure before you change anything.** Telemetry first, enforcement behind a flag.
2. **Never trade correctness for tokens silently.** The tail of a conversation is
   sacred; every drop is logged and reversible.
3. **Local-first.** Decisions happen on your machine; only the actual prompt goes to
   your provider, exactly as it does today.
4. **The dataset is the moat.** Usage compounds into a routing-decision corpus that
   nobody else has — the thing that makes the tiny model beat the zero-shot baseline.
5. **A page has to survive a paste.** Every readout is printed through
   `subproto/style.py` on one grid: a label column and a number column, no box drawing
   and no rules (a frame sized for 80 columns is a frame that breaks in an issue
   thread), a long line only for a path or a command you are meant to copy, and colour
   only ever on a state word — `ok`, `present`, `short`, `missing`. Red means broken,
   never "this number is interesting". Strip the escapes from the coloured page and you
   get the plain page back character for character, so a paste into a README loses the
   hue and keeps every meaning; `--color`/`--no-color` outrank the terminal heuristics
   when you want a screenshot or a clean paste. `subproto/tests/test_style.py` walks the
   pages and fails on a hue that is not a state, a box, a sentence wider than the page,
   or a readout that reshuffles its own rows between two identical runs. `audit`,
   `export` and `split` sit outside it deliberately: their stdout is JSON for `jq`, and
   a design system does not own a pipe.

## Roadmap

What is shipped and what is outstanding, item by item. The later versions are planned
but not published — the checklist below is the part of this project's future that is a
promise rather than a position.

- [x] Transparent OpenAI + Anthropic proxy with SSE-aware usage capture
- [x] Gemini native + OpenAI `responses` dialects (usage, routing, mock, tests)
- [x] Prompt-shape telemetry + SQLite + token/cost/waste report
- [x] Four decision slots (tool_gate, compact, context, effort) in observation mode
- [x] graphify-lite code graph (`subproto graph` / `subproto where`)
- [x] Dataset export + human-label loop (`subproto export` / `subproto label`)
- [x] Deterministic, de-duplicated train/val split (`subproto split`)
- [x] Guarded MLX LoRA fine-tune script (`train/finetune_mlx.py`)
- [x] Local Laya scoring server behind the adapter (`python -m subproto.laya_server`)
- [x] Pluggable System One SPI: swap Laya for any `/health`+`/score` model by config,
      globally or per slot (`subproto/systemone/`, `subproto models`, `--model`)
- [x] Pass-rate–held A/B harness on SWE-bench-shaped tasks (mock; `bench/live.py`)
- [x] TUI overlay: live "you just saved 38% this session" meter (`subproto live`)
- [x] The Context Compiler: one joint budget over messages/tools/files + a retention
      proof (`subproto compile`, opt-in `SUBPROTO_COMPILE=on`; mock-measured
      +17.0 pp precision at −19.3% tokens vs per-slot)
- [x] Real Laya checkpoint on-device (torch CPU — there is no MLX build of it) behind
      `--backend laya`, plus the published per-decision curve (`bench/laya_latency.py`)
- [ ] A fine-tune that fits the architecture: a sequence-classification LoRA over the
      exported split (`train/finetune_mlx.py` is a causal-LM script and cannot train this
      encoder). Needs labels first, and then a trainer that matches the architecture
- [ ] Hero metric on a real SWE-bench-style suite + billed provider savings
      (needs non-zero API spend — external; mock-measured number ships meanwhile)
- [ ] HN/PH launch post (external: a human action, not a command). The release half of this
      line is done — the tag is cut and the index serves it, and the demo GIF
      at the top of this page was recorded from the installed wheel
      ([`docs/assets/first-30-seconds.sh`](https://github.com/aashish254/limen/blob/main/docs/assets/first-30-seconds.sh) re-makes it)

## If it does not work

[`docs/troubleshooting.md`](https://github.com/aashish254/limen/blob/main/docs/troubleshooting.md) takes the failures a first run
actually hits — symptom, meaning, fix — and [`docs/commands.md`](https://github.com/aashish254/limen/blob/main/docs/commands.md) /
[`docs/configuration.md`](https://github.com/aashish254/limen/blob/main/docs/configuration.md) are the reference for every subcommand
and every env var. For a diagnosis of *your* machine rather than a list of possibilities,
run `subproto doctor`: it binds the port, writes and deletes a witness in the data dir,
and probes each configured backend's `/health` with the shipped timeout.

## Contributing

The highest-leverage contribution is **routing-decision labels**: run real agent
traffic, `subproto export`, `subproto label` the good/bad calls, and open a PR with
the JSONL. That is how a fast base model becomes an accurate one.

## License

Apache-2.0 — see [LICENSE](https://github.com/aashish254/limen/blob/main/LICENSE).
