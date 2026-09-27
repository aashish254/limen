# Configuration

subproto reads three sources, resolved by `subproto/config.py` plus a handful of direct
`os.environ.get` calls. Nothing is required: with no config file and no env vars the
defaults below give a working, observation-only proxy on `127.0.0.1:8787`.

## Precedence

1. A command-line flag (`--home`, `--port`, `--model`, `--laya-url`, `--router`, …) is an
   *override* and always wins.
2. Then, for the three boolean switches that are read through a truthy-env helper
   (`store_bodies`, `record_features`, `router`), the **env var beats the config file**.
3. For everything else (`data_dir`, `port`, `laya_url`, `model`, `model_url`,
   `model_timeout_ms`, `router_budget_ms`, `router_evidence`, the three upstreams) the
   **config file beats the env var**.
4. Then the built-in default.

"Truthy" means one of `1`, `true`, `yes`, `on` (case-insensitive) — but only for the three
booleans in step 2. Numeric budgets are read as numbers, never through the truthy path, so
`SUBPROTO_MODEL_TIMEOUT_MS=1500` is `1500` and not `False`.

The config file is searched in this order: `SUBPROTO_CONFIG`, then `./.subproto.json`,
then `~/.subproto/config.json`. First one found wins; a file that does not parse is
ignored (treated as `{`). See `.subproto.example.json` for the shape.

## Slots and enforcement — the ones that change behaviour

subproto has four decision slots: `tool_gate`, `compact`, `context`, `effort`. They run in
observation mode unless you say otherwise.

### `SUBPROTO_APPLY`

| | |
|---|---|
| Value | comma-separated slot names, e.g. `tool_gate,compact,context` |
| Default | unset — **observation mode**: every decision is recorded, none are enforced |
| Changes | which slots actually rewrite the forwarded request |
| Can break | **I1** (observation-first) if you enforce a slot you have not watched; **I3** (correctness floor) if you enforce `compact` on traffic where it cuts needed context |

Names outside the four slots are dropped. `effort` is **advisory-only** (it is in
`ADVISORY_SLOTS`): naming it changes no bytes — it only records a tier — and `subproto up`
says so out loud instead of letting the meter imply an intervention.

If `SUBPROTO_APPLY` is *unset*, presence of **`SUBPROTO_ENFORCE`** (any non-empty value)
turns on the default set `DEFAULT_APPLY = tool_gate,compact`. Without either, `applied` is
empty for every slot.

A slot is stamped `applied` only when it rewrote the body it forwards. One named in
`SUBPROTO_APPLY` that found nothing to drop is *not* an intervention, and the report and
`live` meter do not claim it was (I6).

### `SUBPROTO_COMPILE`

| | |
|---|---|
| Value | `on` / `1` / `true` / `yes` |
| Default | off — the per-slot path runs |
| Changes | routes `tool_gate`/`compact`/`context` through the v5 joint budget optimiser (`compiler.compile_turn`) instead of three independent budgets |
| Can break | none by default — protected items (the tail, core tools, named-path files) are booked before the optimiser, so I3 and I2 hold by construction; a planning error degrades back to the per-slot path and records the failure (`engine.compile_error`) rather than dropping the request |

## Model backend selection — `SUBPROTO_MODEL`, `LAYA_URL`, `SUBPROTO_ROUTER`

These decide *what answers a slot question*. The whole surface is inspectable with
`subproto models`.

### `SUBPROTO_MODEL`

| | |
|---|---|
| Value | a registry name (`laya`, `openjev`, `djev`, `semif`, `mlx_lora`), `heuristic`/`none`/`off`/`passthrough`, or any `http(s)://host:port` URL |
| Default | unset — the in-process heuristics answer |
| Changes | the System One backend for the `tool_gate` and `compact` slots |
| Can break | **nothing if the endpoint is down**: an unreachable or absent backend falls back to heuristics rather than breaking the proxy (I5). It is a *config choice*, not a bet |

A `://` in the value means "use this endpoint directly, no registry entry needed". A name
is resolved to its own `*_URL` env var (below), then `SUBPROTO_MODEL_URL`, then the legacy
`LAYA_URL`.

### `LAYA_URL`

| | |
|---|---|
| Value | `http://host:port` of a Laya scoring server |
| Default | unset |
| Changes | binds the `laya` label; if no `SUBPROTO_MODEL` is set at all, a present `LAYA_URL` still selects the laya adapter (legacy behaviour) |
| Can break | none — degradation is by design |

The registry also reads `OPENJEV_URL`, `DJEV_URL`, `SEMIF_URL` and `SUBPROTO_MLX_URL` to
give a named backend its endpoint.

### Per-slot model choice

| | |
|---|---|
| Value | `SUBPROTO_MODEL_TOOL_GATE`, `SUBPROTO_MODEL_COMPACT` (the name is `SUBPROTO_MODEL_` + slot upper-cased), or a `model_by_slot` map in the config file |
| Default | unset — falls through to the global selection |
| Changes | lets one slot run a different model than the global choice |
| Can break | none. Only `tool_gate` and `compact` are overridable; pinning a model to `context` (graph-answered) or `effort` (shape-answered) would record a label that decided nothing, so `subproto models` refuses the pin |

### `SUBPROTO_MODEL_URL`

Endpoint for a named backend that has no `*_URL` of its own. Default unset.

### `SUBPROTO_MODEL_TIMEOUT_MS`

| | |
|---|---|
| Value | integer milliseconds |
| Default | `350` — a working budget for one decision on the real checkpoint, which measures **123 ms p50** (spread 106–139 ms, 30 decisions, 9-tool option sets, torch CPU; `bench/laya_latency.md`) |
| Changes | how long one System One answer may take before the slot falls back to heuristics |
| Can break | set it too low and a live model silently answers nothing (everything says `heuristic`); raise it to trade latency for the model |

### `SUBPROTO_ROUTER`

| | |
|---|---|
| Value | `on` / `1` / `true` / `yes` (or `--router`) |
| Default | off — manual selection stays in force |
| Changes | routes each **un-pinned** model-backed slot to the best *measured* backend, ranked by precision in the evidence file, within an optional latency budget |
| Can break | none by design — an adapter with no measured precision is never auto-picked, ties fall to `heuristic`, and explicit `SUBPROTO_MODEL[_<slot>]` pins always win |

### `SUBPROTO_ROUTER_BUDGET_MS` and `SUBPROTO_ROUTER_EVIDENCE`

| Var | Default | Meaning |
|---|---|---|
| `SUBPROTO_ROUTER_BUDGET_MS` | unset (no budget) | p50 decision-ms ceiling an adapter must fit to be routed to |
| `SUBPROTO_ROUTER_EVIDENCE` | unset → committed `bench/ablation.json` | the ablation results file the Router ranks against; point it at a fresh `bench/ablation.py --write` output to re-route without a code change |

## Proxy and telemetry

| Var | Config key | Default | Changes |
|---|---|---|---|
| `SUBPROTO_HOME` | `data_dir` | `~/.subproto` | state dir (`telemetry.db`, `bodies/`, `graphs/`, `models.json`, `implicit_labels.json`, `training_history.json`, `training/`). An unwritable dir means nothing is recorded |
| `SUBPROTO_PORT` | `port` | `8787` | the listener port |
| `SUBPROTO_STORE_BODIES` | `store_bodies` | off | gzip-record request bodies so `show`/`learn`/`audit` can resolve drop pointers. Off = metadata only (can weaken **I6** claims, not break them) |
| `SUBPROTO_RECORD_FEATURES` | `record_features` | on | store per-request prompt-shape features |
| `SUBPROTO_CONFIG` | — | — | path to the config file |
| `SUBPROTO_VERBOSE` | — | off | log proxied requests to stderr |
| `SUBPROTO_GRAPH` | — | — | default graph index for the `context` slot (overridden by `--graph`) |
| `NO_COLOR` | — | — | strip colour escapes (see `subproto/style.py`); `TERM=dumb` does the same |

## Upstream overrides

Point a dialect at a different host (handy for the test mock). The config key beats these
env vars, which are read at import time in `config.py`.

| Var | Default |
|---|---|
| `SUBPROTO_OPENAI_UPSTREAM` | `https://api.openai.com` |
| `SUBPROTO_ANTHROPIC_UPSTREAM` | `https://api.anthropic.com` |
| `SUBPROTO_GEMINI_UPSTREAM` | `https://generativelanguage.googleapis.com` |

Your real API key stays in the environment and is forwarded untouched (I4: nothing else
leaves the machine). The proxy injects a key header only if the request carried none:
`OPENAI_API_KEY` (OpenAI and `responses`), `ANTHROPIC_API_KEY` (Anthropic, as
`x-api-key`), `GEMINI_API_KEY` (Gemini, as `x-goog-api-key`). `SSL_CERT_FILE` is the first
CA-bundle candidate tried when building the TLS context.

## The Laya scoring server (`python -m subproto.laya_server`)

Separate from the proxy; it serves `/health` + `/score` for a `laya`/`SUBPROTO_MODEL`
endpoint.

| Var / flag | Default | Changes |
|---|---|---|
| `LAYA_PORT` / `--port` | `8890` | server port |
| `LAYA_BACKEND` / `--backend` | `auto` | `laya` (the real checkpoint, needs a Python ≥ 3.10 venv with `laya[serve]`), `lexical`, `mlx` (refuses — there is no MLX path to Laya), or `auto` (laya when importable, else lexical) |
| `LAYA_DEVICE` / `--device` | `cpu` | torch device for the checkpoint |
| `LAYA_SHAPE` / `--shape` | `choice` | how the question is asked of the encoder: one distribution over options (`choice`, affordable, one pass) or one probability per option (`keep_drop`, N passes) |

Two more vars exist, and neither is read by the proxy or this server: they belong to the
opt-in live leg of `subproto/tests/test_laya_real.py`.

| Var | Changes |
|---|---|
| `SUBPROTO_LAYA_PYTHON` | path to an interpreter that can `import laya` (≥ 3.10, `pip install "laya[serve]"`). The live leg boots the real checkpoint as a subprocess with it; unset, the leg skips and no claim is made about real weights |
| `SUBPROTO_LAYA_BUDGET` | set to `1` on a quiet host to certify that one live decision fits `SUBPROTO_MODEL_TIMEOUT_MS`. Deliberately opt-in: a wall-clock assertion measured while something else holds the machine is a false red, so the operator says "measure now" |

## Reference: config-file keys

`.subproto.json` accepts, in addition to the table above, `model`, `model_url`,
`model_timeout_ms`, `model_by_slot`, `router`, `router_budget_ms`, `router_evidence`, and
`laya_url`. An unknown key is ignored.
