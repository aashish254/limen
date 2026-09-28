# Commands

`subproto` has **17 subcommands**. This list was verified against `subproto/cli.py` by
grepping `add_parser(`, and each section's flags by reading the parser that registers it.

```
up  report  live  graph  where  compile  show  audit
export  split  learn  label  inject  demo  models  retrain  doctor
```

Run any of them as `subproto <cmd>` (installed entry point) or
`python3 -m subproto <cmd>`. `subproto` with no command prints the top-level help.

## Conventions that apply to every command

- **Version.** `subproto --version` prints one line — `subproto 0.1.0 (python 3.11.15,
  darwin arm64)` — with the build, the interpreter that ran it and the platform. It is
  the line to paste into a bug report; `doctor` is the page that diagnoses the machine.
- **State dir.** `--home DIR` (env `SUBPROTO_HOME`, default `~/.subproto`) holds
  `telemetry.db`, `bodies/`, `graphs/`, `models.json`, `implicit_labels.json`,
  `training_history.json`, and the `training/` split. Every command resolves it the same
  way.
- **Config file.** `--config PATH` (searched: `SUBPROTO_CONFIG`, `./.subproto.json`,
  `~/.subproto/config.json`).
- **Colour.** `--color` forces ANSI escapes on (for a screenshot from a pipe),
  `--no-color` forces them off (for a paste). Both outrank `isatty` and `NO_COLOR`.
- Pages print on one grid; colour is only ever on a state word, so stripping the escapes
  returns the same page character for character.

Below, flags are exactly those parsed in `cli.py`. The pages marked *captured* were run
against a throwaway `SUBPROTO_HOME=$(mktemp -d)`; nothing was left running.

---

## `up` — run the local proxy

Starts the transparent listener and, with `--slots`, computes a decision per request.

| Flag | Effect |
|---|---|
| `--port N` | listen port (env `SUBPROTO_PORT`, default 8787) |
| `--store-bodies` | record gzip request bodies so slots can be audited offline |
| `--slots` | compute routing decisions per request (default: pure passthrough) |
| `--graph PATH` | a `.subproto-graph.json` or repo dir, for the `context` slot |
| `--laya-url URL` | `http://host:port` of a Laya scoring server |
| `--model NAME` | backend: `laya`, `openjev`, `djev`, `semif`, `mlx_lora`, `heuristic`, or an `http://host:port` URL |
| `--router` | pick the best measured backend per un-pinned slot (same as `SUBPROTO_ROUTER=on`) |
| `--for TOOL…` | print the env vars to point a tool at the proxy (`claude`, `codex`, `gemini`, `openai`, `aider`, `generic`) |

```bash
subproto up --slots --store-bodies --for claude
```

The banner prints the data dir, whether bodies are recorded, whether slots are on
(otherwise "pure passthrough"), the enforcing state, the graph path, and the model
that answers: its label, version, per-slot backends, and a reason for anything that
was named but not used. Both of those last two clauses are load-bearing. The
enforcing state is asked of the engine rather than read out of `SUBPROTO_APPLY`, so
`SUBPROTO_ENFORCE=1` prints the two slots it turned on instead of "observation mode
— it records, it never rewrites". And a pin that resolved to nothing says so here —
`--model openjev` with nothing behind `OPENJEV_URL` prints
`openjev is not being served — set OPENJEV_URL, or name its URL in SUBPROTO_MODEL`,
because a model that quietly fell back to the heuristics used to print no model row
at all. It closes with the `base_url` lines for the requested tools and
`Ctrl-C to stop. Then run: subproto report`.

`up` is a long-running server. It was **not** run for this document.

---

## `report` — token, latency and waste report

| Flag | Effect |
|---|---|
| `--no-learn` | skip the eviction-regret harvest (no bodies read) |
| `--since DATE` / `--until DATE` | time window; a date that does not parse is refused with the form it wanted, exit 1 |
| `--json` | machine-readable output |

```bash
subproto report
```

**captured** (`demo --slots` traffic) — header `subproto report — 17 requests`, then
billed input (179,280 tok), cached input (38,817 tok, 17.8 % — read from the mock's
prompt cache, which prices a shared prefix at its read rate), output, spend ($0.59 at
the mock's tariff, so nothing is billed), failures, slot decision latency (p50/p95),
cache hit rate, a "where the input tokens came from" breakdown by message category,
`by model`, `by client`, `slot headroom`, and a `by model version` line naming which
checkpoint answered. Empty DB instead prints `0 requests` and the point-an-agent hint.

---

## `live` — live terminal savings meter

| Flag | Effect |
|---|---|
| `--interval SECONDS` | refresh rate (default 1.0) |
| `--once` | print one snapshot and exit (CI / screenshots) |

```bash
subproto live --once
```

**captured.** `--once` prints a single non-ANSI snapshot. With no traffic it reads
`no traffic yet / point an agent at the proxy, then come back`. It renders a shared meter;
the running form needs a tty.

---

## `graph` — index a repo (imports + symbols)

| Flag / arg | Effect |
|---|---|
| `PATH` (positional, default `.`) | repo to index |
| `--out PATH` | where to write the index (default: `<PATH>/.subproto-graph.json`) |
| `--install` | also copy it into `$SUBPROTO_HOME/graphs/<name>.json` |

```bash
subproto graph fakeup --out /tmp/sp-graph.json
```

**captured.**

```
subproto graph — fakeup
         files  2
  import edges  5
    indexed in  0.0s
       written  /tmp/sp-graph.json
```

A nonexistent directory is a plain `no such directory: <abs path>` line and exit 1.

---

## `where` — which files does a task touch?

| Flag / arg | Effect |
|---|---|
| `QUERY` (positional, rest of line) | the task text; also read from stdin when piped |
| `--graph PATH` | graph json **or** repo directory to index |
| `--file PATH` | prepend a file's text to the query |
| `--path DIR` | repo to resolve `.subproto-graph.json` from (default `.`) |
| `--top N` | number of hits (default 10) |
| `--json` | list of `{file, score, why}` |

```bash
subproto where --graph /tmp/sp-graph.json server
```

**captured.**

```
subproto where — server
  2.0    server.py                    path:server
         215 lines  A mock upstream that speaks just enough of both dialects to test the p
```

With no index at the resolved path it says `no index at … — built one in memory
(cache it: subproto graph …)` and continues; nothing matching prints
`no file in the index matched that.`

---

## `compile` — compile one turn under one budget

The v5 witness: spends one token budget over messages, tools and files, and prints the
retention proof. This does not touch your real traffic — it builds a synthetic request and
applies your task as the live turn.

| Flag / arg | Effect |
|---|---|
| `TASK` (positional, rest of line) | the turn to compile |
| `--graph PATH` | graph json or repo directory to index |
| `--path DIR` | repo to index when `--graph` is a directory (default `.`) |
| `--budget N` | token budget (default: 45% of the estimate) |
| `--turns N` | simulated prior turns (default 6) |
| `--seed N` | demo body seed (default 3) |
| `--json` | full proof + decisions + compiled body |

```bash
subproto compile "fix the relay server" --graph fakeup
```

**captured.** Header, `task`, `budget` (e.g. `6,192 tok   pool was 11,289 tok`), `spent`
(`−47.6%`), `protected` (tokens booked before the optimiser ran), a per-kind kept/dropped
table (`message`, `tool`, `file`), the never-dropped sets (`tail kept`, `core tools`,
`files added`), a "what was cut, and what beat it" table with each drop's value/token
ratio against the kept floor, and a note that every drop carries a reversible pointer
resolvable via `subproto show <request_id>`.

Two exits are deliberate: an empty droppable pool prints
`nothing to compile: the request has no droppable candidates` (exit 1); `over budget`
prints `the tail is sacred (I3)` (exit 1) and cuts nothing.

---

## `show` — one recorded request and what each slot cut

| Flag / arg | Effect |
|---|---|
| `REQUEST_ID` (positional, int) | the telemetry row to open |
| `--json` | full payload incl. body path and every drop with its excerpt; for an id that is not in the DB, the payload carries `"error"` — an empty `decisions` list alone cannot tell those two apart |

```bash
subproto show 1
```

**captured.** Header `subproto show — request 1, anthropic /v1/messages, <model>`, then
client, recorded time, stream, status, ttfb, latency, billed input (+cached), output,
spend, and the stored body path (`present …` or `never stored — subproto up --store-bodies
records them`). A `decisions` block per slot with both kept/cut counts, tokens, decision
ms, the answering backend, and under each cut the pointer, its tokens, its reason and a
clipped quote of the item. A request not in the DB is a reason line and exit 1.

---

## `audit` — re-run the slots over recorded traffic

| Flag | Effect |
|---|---|
| `--limit N` | how many recorded requests to re-run (default 200) |
| `--verbose` | more detail |
| `--graph PATH` | index for the `context` slot |

```bash
subproto audit --limit 50
```

Prints a JSON summary of what the slots *would* drop over the recorded bodies. Not run for
this document (it is not read-only against the DB write path).

---

## `export` — write the fine-tuning dataset as JSONL

| Flag | Effect |
|---|---|
| `--out PATH` | output file (default `$SUBPROTO_HOME/dataset-YYYYMMDD.jsonl`) |
| `--slots a,b,c` | restrict to named slots (default: every slot) |
| `--slim` | omit per-candidate scores |

```bash
subproto export --out /tmp/sp-export.jsonl
```

**captured** (JSON): `{path, rows, labelled, labelled_by_human, labelled_by_traffic,
sources, slots}` — e.g. `rows: 51` with per-slot counts `tool_gate 17 / compact 17 /
context 0 / effort 17`.

---

## `split` — deterministic train/val Laya supervision split

| Flag | Effect |
|---|---|
| `--val-frac FLOAT` | validation fraction (default 0.2) |
| `--seed INT` | shuffle seed (default 1337) |

```bash
subproto split
```

**captured** (JSON): `n_examples`, `n_train`, `n_val`, the supervised/traffic/heuristic
counts, `val_frac`, `seed`, `dedup: true`, and the written `train_path` / `val_path`
(under `$SUBPROTO_HOME/training/`). Deterministic and de-duplicated by construction.

---

## `learn` — label the drops the traffic contradicted

Reads local telemetry + recorded bodies; no key, no upload (I4).

| Flag | Effect |
|---|---|
| `--limit N` | requests to scan (default 200) |
| `--json` | `{summary, labels}` |
| `--dry-run` | print the harvest without writing `implicit_labels.json` |

```bash
subproto learn --dry-run
```

**captured.** Header counts requests with decisions and bodies readable. An
`eviction regret` block (evicted reads seen, regrettable drops split enforced vs shadow,
tokens paid back, the window, verdicts by signal). Then a `written` row — `nothing
(--dry-run)`, or `<N> requests` with the `implicit_labels.json` path and a note that human
`label` verdicts live in a separate store and were untouched. If no bodies are available
it prints `Nothing to judge … start the proxy with --store-bodies`.

---

## `label` — supervise one decision by hand

| Arg / flag | Effect |
|---|---|
| `REQUEST_ID` (int) | the request |
| `SLOT` | one of `tool_gate`, `compact`, `context`, `effort`, or `all` |
| `VALUE` | one of `good`, `bad`, `uncertain` |
| `--reason TEXT` | free text stored with the label |

```bash
subproto label 1 tool_gate bad --reason "dropped the retry tool"
```

Writes the verdict to the human label store (never the implicit one) and prints
`labelled → <id> (<slot>, <value>)` with the store path. Slot values come from the
engine's `ALL_SLOTS`.

---

## `inject` — print env vars for a given tool

| Arg | Effect |
|---|---|
| `TOOL` | one of `claude`, `codex`, `gemini`, `openai`, `aider`, `generic` |

```bash
subproto inject claude
```

**captured.**

```
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export CLAUDE_CODE_ENABLE_PROXY=1  # not required; base URL is enough
```

It reads the configured port but does not start the proxy. Per-tool detail lives in
`WIRING.md`.

---

## `demo` — replay synthetic traffic against a local mock

Runs end-to-end against `fakeup` (a local mock upstream): no API key, no spend. It
temporarily opens a listener, replays, prints the report, then shuts itself down.

| Flag | Effect |
|---|---|
| `--port N` | proxy port (default 8799; the mock upstream takes N+1, or any free port if something already holds it) |
| `--slots` | run the decision slots over the replayed traffic |
| `--audit` | also print a JSON slot audit |
| `--graph PATH` | index for the `context` slot |
| `--model NAME` | backend to run the slots with |

```bash
subproto demo --slots
```

**captured.** Prints the full `report` page over the replayed requests, then
`replayed 17 synthetic agent requests through the proxy` and `recorded bodies: 17`.
Honours `SUBPROTO_HOME` / `--home`. With neither, the run gets a fresh
`subproto-demo-*` directory under the system temp dir and prints it as
`sandbox: <path>` — a fixed sandbox made two runs share one SQLite file, so the
second one reported the first one's traffic as its own result. Pass `--home` (or set
`SUBPROTO_HOME`) when you want the traffic to stay somewhere `subproto report`,
`subproto audit` and `subproto export` can read back.

---

## `models` — list System One backends and the version manifest

| Flag | Effect |
|---|---|
| `--model NAME` | show the selection as if this backend were chosen |
| `--router` | show the evidence-driven routing plan as if `SUBPROTO_ROUTER=on` |
| `--add LABEL` | register a version (needs `--url`) |
| `--url URL` | endpoint for `--add`: any `/health` + `/score` server |
| `--tier T` | `foundation` or `personal` |
| `--note TEXT` | free text for `--add` |
| `--use LABEL` | make a registered version active (old one → previous) |
| `--rollback` | put the previous version back, or drop to heuristics |
| `--json` | structured status |

```bash
subproto models
```

**captured** (clean home). Header `subproto models — 6 backends, 0 versions`; a `backend`
state row (`heuristic` when nothing is configured); a `backends` list — `heuristic`
(in-process, always available) plus `laya`, `openjev`, `djev`, `semif`, `mlx_lora`, each
showing its `*_URL` env var or `not configured`; a `per slot` line
(`compact=heuristic tool_gate=heuristic`) with the note that `context`/`effort` are
answered by the graph and request shape, not a model; a `versions` block pointing at
`$SUBPROTO_HOME/models.json`; a `router` row (`off`, or the per-slot plan under
`--router`/`SUBPROTO_ROUTER=on`); and a `select one` block of example invocations. A
registered version whose endpoint fails `/health` degrades to `heuristic` with the reason
printed, and a `SUBPROTO_MODEL`/`LAYA_URL` pin outranks the manifest.

---

## `retrain` — decide whether a fine-tune is worth starting

Dry-run by default: it prints a page and runs nothing unless `--run` says so *and* the
cadence cleared. Local state only (I4).

| Flag | Effect (default) |
|---|---|
| `--run` | start the trainer only if the cadence cleared; otherwise refuse + record |
| `--record` | append the decision to `training_history.json` without running |
| `--min-per-slot N` | rows per slot (default 40) |
| `--min-answers N` | rows per slot in *both* answers (default 8) |
| `--min-days N` | wait after a completed run (default 7) |
| `--val-frac F` / `--seed N` | forwarded to the split builder |
| `--epochs N` | default 3 |
| `--rank N` | default 8 |
| `--json` | the readiness state flattened (`ready`, `reasons`, per-slot counts, `toolchain`, …) plus `{command, recorded, split_written, exit}` |

```bash
subproto retrain
```

**captured** (`--run` was not used). Header `subproto retrain — cadence policy, <date>`,
the split fingerprint, `examples N train / N val` with the git sha, `last run`
(`none recorded` when there is no completed run on record), a `per slot` table against the
`40 rows / 8 in both answers` floor with OK/SHORT verdicts, a `toolchain` block (`trainer`,
`mlx + mlx-lm`, `base`), a `decision` (`not ready — N reasons` with each reason, or
`ready`), the exact command it *would* run, and `nothing was run. --run starts the trainer
once ready.` A refused `--run` exits 1.

When `trainer` reads `missing` the page says where it looked, on its own line, because the
path is what tells a `pip install` checkout from a source clone.

---

## `doctor` — diagnose this machine

Six checks, each measured rather than assumed: it runs the interpreter it was started
from, writes a file into the data dir and deletes it, binds the port for real, and probes
each configured backend over HTTP. Local only — nothing leaves the machine (I4), and no
key value is ever printed (it names the variables that are set).

| Flag | Effect |
|---|---|
| `--port N` | the port to test; `0` asks the kernel for any free one |
| `--json` | print the checks as one document, for a bug report |

```bash
subproto doctor --port 0
```

**captured** (`--port 0`):

```
subproto doctor — 6 checks, 2 to fix

  environment
          python  ok  3.11.15
                  >= 3.9, read from pyproject.toml — this interpreter:
                  /opt/homebrew/opt/python@3.11/bin/python3.11
        data dir  ok  /Users/you/.subproto
                  a file was written here and removed — telemetry.db lands
                  in this directory
            port  ok  63438  127.0.0.1
                  you asked for any free port, and that is the one it took
   provider keys  warn  none in this environment
                  export your provider's key (ANTHROPIC_API_KEY,
                  OPENAI_API_KEY or GEMINI_API_KEY) before subproto up — the
                  proxy forwards it and never stores it

  model backends
    laya runtime  not ready  not installed in this interpreter (python 3.11)
                  pip install "laya[serve]" here for the real checkpoint —
                  subproto's lexical server answers without it and needs
                  nothing
         backend  not configured — every slot answers with the in-process heuristics
```

The header counts what needs fixing, so a paste into an issue answers "did it work?"
before anyone reads a row. The `python` row says *where* the floor came from:
`read from pyproject.toml` in a checkout, and `the shipped default` in an installed
wheel, where that file is not beside the package.
