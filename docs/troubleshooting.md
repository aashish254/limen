# Troubleshooting

The failures a first run actually hits, each as **symptom → what it means → the fix**.
Nothing here is a workaround that hides a problem; where a fallback is by design, it says so.

## Where to start: `subproto doctor`

Before reading the symptoms below, run the one command that measures them:

```bash
subproto doctor            # or --json, to attach to a bug report
```

It checks the interpreter against the floor in `pyproject.toml`, writes and deletes a
witness file in the data dir, binds the port, reports whether each provider key is
present, and asks every configured backend that answers. It never prints a key value.
The rows it prints are the same four or five causes the rest of this file expands on.

## `SyntaxError` or an import failure on startup

**Symptom.** `subproto` will not start, or `pip install` rejects the package.

**Meaning.** subproto needs **Python 3.9 or newer** (`pyproject.toml`:
`requires-python = ">=3.9"`, zero runtime dependencies, stdlib only). An older
interpreter is the usual cause. Separately, the real Laya backend needs a **Python ≥ 3.10**
venv — that is the checkpoint's requirement, not subproto's.

**Fix.**

```bash
python3 --version                 # must be 3.9+
python3 -m subproto report        # run from the module if the entry point is not on PATH
```

For the Laya checkpoint specifically, run the server from a 3.10+ venv with
`pip install "laya[serve]"`. The proxy itself never needs that venv — it falls back to the
in-process heuristics.

## The proxy refuses to start: `Address already in use`

**Symptom.** `subproto up` exits with an `OSError` mentioning the port (default `8787`).

**Meaning.** Another process — often a previous `subproto up` you did not stop — already
holds `127.0.0.1:8787`. (`allow_reuse_address` lets subproto reclaim a socket in
`TIME_WAIT`; it does not let two live listeners share a port.)

**Fix.** Use a different port, or free the original:

```bash
subproto up --port 8790
# or: SUBPROTO_PORT=8790 subproto up
lsof -nP -iTCP:8787 -sTCP:LISTEN          # what is holding it
```

Remember to point your agent's `base_url` at the same port you chose
(`subproto inject <tool>` prints it).

## Nothing is recorded / the data dir cannot be written

**Symptom.** `subproto report` says `0 requests` after real traffic, or startup raises a
`PermissionError` / `OSError` from `ensure_dirs`.

**Meaning.** `~/.subproto` (or your `--home` / `SUBPROTO_HOME`) is not writable, so the
telemetry SQLite file cannot be created or opened. The default state dir is
`~/.subproto`.

**Fix.** Point subproto at a writable directory and use it consistently across commands:

```bash
export SUBPROTO_HOME="$HOME/.subproto"    # any dir you can write
subproto up --slots --store-bodies
```

All read commands (`report`, `live`, `show`, `learn`, …) must see the **same**
`SUBPROTO_HOME`, or they look in an empty dir and report nothing.

## "I turned on `tool_gate` and it isn't doing anything"

**Symptom.** A slot is in `SUBPROTO_APPLY`, `subproto report` shows headroom for it, but
the request bytes did not change.

**Meaning.** Two separate normal cases:

1. **Observation, not enforcement.** A slot is stamped `applied` only when it *rewrote the
   body it forwards*. If it found nothing to drop (no tools, a prompt under the `compact`
   size threshold), it is correctly not an intervention. `subproto live` also labels the
   number **potential** ("headroom; slots are observing, not enforcing") until enforcement
   is actually on.
2. **`effort` is advisory-only.** It is in `ADVISORY_SLOTS`; naming it in `SUBPROTO_APPLY`
   changes no bytes — it only records a tier. `subproto up` prints that it is advisory
   instead of letting the meter imply an edit.

**Fix.** Confirm you started with enforcement *and* slots computed:

```bash
SUBPROTO_APPLY=tool_gate,compact subproto up --slots
```

Then check `subproto show <id>`: each decision row says `delivered` (it rewrote the body)
or `potential` (it recorded only).

## A model is configured but every decision says `heuristic`

**Symptom.** You set `SUBPROTO_MODEL` (or `LAYA_URL`) / registered a version, but
`report`, `show`, and the `up` banner all say `heuristic`, and `models` marks the endpoint
`unreachable`.

**Meaning.** **By design.** An unreachable or absent backend degrades to the in-process
heuristics rather than breaking the proxy (invariant I5). The version selector says it
plainly: `pinned <label> is unreachable (<reason>); every decision it would have answered
says heuristic`. A missing model is never a broken install.

**Fix.** Bring the endpoint up and let it answer `/health`:

```bash
python -m subproto.laya_server --backend laya      # needs a Python ≥ 3.10 venv
curl -s http://127.0.0.1:8890/health               # the health gate the manifest checks
subproto models                                    # `*` now marks the live backend
```

If it answers but slowly, raise `SUBPROTO_MODEL_TIMEOUT_MS` (default 350 ms) — a shape that
exceeds the budget is treated as unusable. A trained version with no serving endpoint will
also stay on heuristics until something serves it.

## My latency numbers do not match the published curve

**Symptom.** A decision or TTFT you measured differs from `bench/laya_latency.md`.

**Meaning.** The published curve is a **measurement on one specific host on one day**, and
it carries its conditions precisely so this comparison is possible: `cpu` device, torch
2.6.0, 4 threads on 10 cores, and a host **load average of 7.79→10.16** at start/end. Your
machine, thread count, question `--shape`, and current load all move it. The `choice` shape
costs ~123 ms p50; `keep_drop` costs ~800 ms p50 because it is one pass per option.

**Fix.** Re-measure rather than comparing across hosts, and record your conditions the same
way:

```bash
python bench/laya_latency.py            # prints its own load average in the header
```

Treat the committed file as evidence of *that run's* cost, not a universal constant
(invariant I6).

## `subproto live` shows nothing / looks frozen

**Symptom.** `subproto live` prints nothing useful, or repeats the same block.

**Meaning.** Two things. First, with no traffic the meter correctly prints
`no traffic yet`. Second, the periodic screen-clear is a **tty-only control code**: run in
a pipe, a CI log, or a non-interactive shell (or `TERM=dumb`), the refreshing loop cannot
clear and repaint, so it appends a monochrome snapshot every `--interval` seconds and reads
as frozen.

**Fix.**

```bash
subproto live --once          # one snapshot, plain — for CI, a paste, or a screenshot
subproto live --interval 0.5  # the animated meter, in a real terminal, once traffic exists
```

`--once` and a tty both render the same grid; the animated loop needs the tty.

## A turn saves nothing

**Symptom.** `subproto compile` reports `nothing to compile: the request has no droppable
candidates`, or `report` shows `0` headroom for a request you expected to shrink.

**Meaning.** The slots have nothing to act on: `tool_gate` needs tool specs in the request,
`compact` fires only past a size threshold (estimated input over ~4,000 tokens and more
than 8 messages), and `context` needs a loaded code graph. A short turn with few or no
tools genuinely has nothing to drop, and the honest answer is zero — not a missing
measurement.

**Fix.** There is nothing to force. Confirm against traffic that *does* carry candidates:

```bash
subproto demo --slots          # synthetic agent traffic with many tool specs
subproto report                # now shows non-zero slot headroom
subproto compile "fix retry.py" --graph <repo>
```

## `subproto show` cannot display the dropped text

**Symptom.** `show` lists real pointers and counts but says
`never stored — subproto up --store-bodies records them`.

**Meaning.** Body recording is off (the default is metadata-only). The pointers and counts
are measured, but the page cannot quote the item its pointer names without the stored body.

**Fix.** Record bodies, then re-run the traffic:

```bash
subproto up --slots --store-bodies
```

`learn` needs the same thing: with no bodies it says `Nothing to judge … start the proxy
with --store-bodies`.
