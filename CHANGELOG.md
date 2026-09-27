# Changelog

Every entry below is drawn from `git log` on this tree (32 commits, all on or after
2026-09-25). Commands, flags and numbers are quoted from the source or a committed
artifact, never restated from memory. Where a number is a *projection*, a *measured*
mock result, or a figure still *gated* on an external resource, the entry says which.

The shape of the work is not a clean `1.0`. It is a working proxy that measures
honestly, and several claims that have not yet been earned. The honest negatives are
left in place on purpose:

- The Context Compiler's per-decision cost is still not at parity with the per-slot
  path (see *Cheaper compiler decisions* below).
- The real Laya checkpoint measured **below** the in-process heuristic on the corpus
  this repo carries (see `bench/ablation_results.md`, `bench/laya_latency.md`).
- The billed hero number is **gated** on real API spend and a public task suite; only
  the mock, pass-rate-held measurement ships as `measured`.

---

## Unreleased — the release-readiness pass

Work to make this tree something a stranger can clone, install and run. Each item below
is in the tree and has a test behind it; the items that are *not* in the tree are in
**Still gated**, at the end, and nothing here checks one of them off.

### The machine gets a page of its own

- Added **`subproto doctor`** (`subproto/doctor.py`): six checks, each measured rather
  than assumed — the interpreter against the floor in `pyproject.toml`, a file actually
  written and deleted in the data dir, the port really bound, provider keys present
  (names only: a key's value, prefix and length never reach the page), and every
  configured backend probed over HTTP. `--json` prints the same checks as one document
  for a bug report. The `python` row says *where* the floor came from, because an
  installed wheel carries no `pyproject.toml` and "3.9" printed either way would be a
  claim about a file that may not exist.
- Added **`subproto --version`**: one line — build, interpreter, OS and machine — which
  is the line to paste into an issue.
- `subproto retrain` now distinguishes its three absences on the page: `trainer` missing
  prints the path it looked at, on its own line, so a `pip install` reader is told to
  clone and a clone reader is told their checkout is incomplete.

### Install and packaging

- `install.sh` puts the entry point on `PATH` and no longer needs a package index;
  `tools/check_install.sh` runs the same checks a reviewer would, from outside the clone.
- `pyproject.toml` is PEP 621 throughout, with PEP 639 `license = "Apache-2.0"`, the
  version taken from `subproto.__version__`, an explicit package list, and
  `testpaths = ["subproto/tests"]` so `pytest` from a read-only checkout collects the
  suite instead of trying to write a cache.
- Added `MANIFEST.in` and `.gitattributes`; `python -m build` then `pip install` of the
  wheel and the sdist is exercised in CI, in a clean venv, from outside the source tree.

### CI and community files

- `.github/workflows/ci.yml`: pytest across ubuntu/macos × Python 3.9/3.11 (windows-latest
  is informational, `continue-on-error`), a byte-compile pass under `-W error`, the
  install-from-artifact job above, and `bash run_tests.sh` — the benches and the mutation
  gate — as one job.
- Added `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, two issue forms and a PR
  template; `docs/index.md` maps the reference pages.

### Portability, from a cross-platform audit

The audit's premise: a release is installed on machines whose author does not control.
Every item below has a witness at the seam the clause names, and each rule is now broken
on purpose by a mutant in `bench/mutation_gate.py` (142 → **156 mutants**).

- The CLI reconfigures its own streams to UTF-8: a console that cannot encode an em dash
  or a non-Latin path still gets the page, verified by running two real child processes
  under `PYTHONIOENCODING=cp1252`.
- Every text file this project reads is opened with an explicit encoding, and the graph's
  language lookup lowercases the extension, so `0008_GATEWAY.SQL` is indexed as SQL.
- `bench/laya_latency.py` handles a host with no `getloadavg` (Windows) *and* a host whose
  kernel refuses the call; the conditions block then says the figure is absent rather than
  printing a row of `None`s as if it were a measurement.
- `README.md` and the wiring docs use `http://127.0.0.1:` rather than `localhost`, which on
  a dual-stack host can resolve to `::1` while the proxy binds IPv4.
- `conftest.py` scrubs every `SUBPROTO_*`/`LAYA_*` variable from the host, so a developer's
  own live backend cannot silently change which adapter the suite exercised.
- `subproto demo` — a stranger's first command — no longer collides on a busy
  `port + 1`, no longer shares one SQLite file between runs, prints the sandbox it wrote,
  and waits for its own telemetry rows before printing counts. The same fixed-sleep
  pattern in four e2e tests became polling against the database.

### The release surface, audited

A read-only sweep of everything a first-time reader touches — links, command citations,
anchors, env vars in both directions, file modes — run separately from the test suite,
because none of these failures is in a code path.

- **`LICENSE` was an excerpt, not the license.** It carried the 17-line Apache
  boilerplate notice instead of the license text, so GitHub's detector would have read the
  repo as unlicensed whatever the badge and `pyproject` metadata said. Now the canonical
  Apache-2.0 text (202 lines, verified byte-identical against apache.org) with the
  copyright line after it.
- **`install.sh` was not executable** while the README's install block types `./install.sh`
  — a permission-denied first command for anyone who cloned and obeyed. Mode `100755`.
- `docs/configuration.md` said the real checkpoint "measures 110–141 ms"; its own published
  artifact says **123 ms p50, spread 106–139 ms over 30 decisions**. The row now quotes the
  artifact and names the file.
- The README's compiler-cost line keeps `+0.19 ms` as the quiet-host best but now also prints
  what the gate measured on a loaded host (**+0.32 best / +0.37 median**), because the gap
  moves by more than its own effect size and a single number invites a re-run that "fails".
- Python and dependency badges linked to `(#)`, a dead self-link; they point at
  `pyproject.toml` now.

### The front page shows a real run

- `docs/assets/first-30-seconds.gif`: thirty seconds of the installed wheel doing the
  three things first — `doctor` reads the machine, `demo` replays 17 synthetic agent turns
  through the proxy against the local mock, `live` prices the remaining headroom. Recorded
  from the *wheel* rather than the checkout, with no machine-specific path in the frame,
  and `docs/assets/first-30-seconds.sh` beside it so the page can be re-made rather than
  re-claimed. The `.cast` is committed too, so the recording is text anyone can replay.
- `docs/configuration.md` documents the two variables that belong to the live Laya
  certification (`SUBPROTO_LAYA_PYTHON`, `SUBPROTO_LAYA_BUDGET`) and says plainly that
  neither is read by the proxy — the budget check is opt-in because a wall-clock assertion
  measured while another process holds the machine is a false red.

### Still gated — not in this tree

- **Version tag and PyPI.** `pyproject.toml` is at `0.1.0` and the entry point is wired,
  but no release tag or upload has been performed. `README.md` says so and points
  installers at the source path.
- **The compiler cost gate.** The v5 S32 gate is `compiled decision cost <= per-slot`. It
  is **NOT MET**; the residual row stays in `bench/s32_probe.py`, and a retrain that does
  not clear it must keep printing `not ready`.
- **Real-provider flip.** The pass-rate-held harness runs against `fakeup` (local mock,
  $0, no key). Flipping it to a billed run against a published task list is a config
  change plus a spend gate, not opened.
- **Routing labels with independent origin.** The gold sets in `bench/` were written around
  the heuristic's own rule, so a precision delta on that set favours the baseline by
  construction. Real `subproto label` verdicts, or a set labelled without the rule in
  view, are what make the two ablation columns meaningful. That corpus is still gated.
- **MLX.** `--backend mlx` refuses; the trainer ships with the source clone, and the
  measured path is CPU + the Laya checkpoint. No MLX number is claimed anywhere.

---

## `subproto show` — the pointer the compiler cites now walks (commit `d07e2c9`)

- Added `subproto show <request_id>`: one telemetry row, its `body_sha` resolved to the
  spooled gzip body, and every decision with its state word, both kept/cut counts, its
  tokens, its latency, and the backend that answered. Under each cut: the reason it lost
  and a clipped quote of the item itself. Three places had already told the reader to run
  it while the command did not exist.
- Drop pointers now resolve against the *flattened* message list (one entry per content
  block) that the compiler scored, not `body["messages"]`, and a pointer whose kind
  disagrees with what its index holds is refused rather than printed as a neighbouring
  turn.
- `compact` no longer prints its `dropped_count` under the word "kept"; both counts come
  from whichever shape the arm actually wrote, and `effort` prints neither (it edits no
  pool).
- A request not in the DB is a reason line and exit 1, never a traceback.

## One design system for every page (commit `d730c4f`)

- Every human-facing page prints through `subproto/style.py` on a single grid: a label
  column and a number column, no box drawing, colour only on a state word (red reserved
  for actual breakage), a long line only for a path or command the reader copies.
  Stripping the escapes returns the plain page character for character, so hue is
  additive and never load-bearing.
- `--color` / `--no-color` outrank the terminal heuristics (`isatty`, `NO_COLOR`) for
  screenshots and pastes.
- Fixing the pages to that grid surfaced five product bugs, not style nits: `report`
  printed no colour at all, ANSI escapes were counted as column width, `where` had no
  header, `demo` reshuffled the compiler's kept-tool proof between identical runs, and
  `live` cleared a terminal it was not drawing on.
- `subproto up --graph <repo>` (and `where --graph <repo>`) no longer raise
  `IsADirectoryError`: a directory resolves to that repo's cached index, and a graph
  that cannot be read is said as a state and exits 1.

## The traffic charged the compiler 4088 tokens (commit `535fac1`)

- Corroborating the capture loop on running traffic, as a fresh user, printed a regret
  count of 10 rather than 0: with `SUBPROTO_APPLY` on, the drops a shadow run reported
  for free cost **4,088 tok** of re-reads against **75,999** saved. That is a measured
  observation on live traffic, reported with the bill in it.
- `SPEC.md` gained FR-11 (labels, versions, cadence) and two report-answerable metrics;
  `README.md` gained `learn`/`retrain` in the quick start plus the cadence section.

## Cheaper compiler decisions — parity still not met (commit `3e3abfa`)

- One line, not "the per-candidate passes", was the cost: `PATH_RE.findall` over every
  candidate message read whole 4 KB tool results to find paths almost never there
  (1.20 ms/decision). `protocol.paths_named()` now locates each ranked basename with a
  C-level `str.find` and hands the regex only the path-shaped run around it.
- Paired best decision overhead: **+1.242 ms → +0.192 ms/decision** (measured on the
  mock harness by `bench/s32_probe.py`, which runs both arms in one process and reports
  the minimum, because the request p50 drifts 10–20% on a laptop and cannot see a effect
  this small).
- The gate `compiled <= per-slot` is **NOT MET**, and the residual (the graph coupling
  that earns the +17.0 pp precision) stays printed rather than being hidden.
- Corrected a stale claim: `bench/live_results.md` and the docs still carried the
  pre-S31 `11,136 tok / [17.9, 20.9]` figures; every quote now matches a fresh print
  (`11,133 tok`, CI `[18.0, 20.9]`).

## The Context Compiler — one joint budget (commit `dba3959`, v5 S20–S28)

- `tool_gate`, `compact` and `context` previously spent three independent budgets and
  could not see each other. Behind `SUBPROTO_COMPILE=on` they now resolve from one
  value-per-token knapsack over `{message, tool spec, file note}`, with a retention
  proof naming the decision that beat every drop.
- Accuracy is the claim, measured on the mock (`bench/live.py`, pass-rate held):
  - sample set: **−19.3%** input tokens vs per-slot, recall 100%→100%, precision
    2.6%→19.6% (+17.0 pp).
  - hard set: the per-slot arm drives pass-rate to **0.0%** (recall 2.7%, I3 VIOLATED);
    the compiled arm holds 100% / 100% at 18.2% fewer tokens.
- `where` gained `.md`/`.rst`/`.txt`/`.sql` indexing (a heading or DDL object is that
  file's symbol), so a prose read the task never names still gets graph evidence.

## Traffic that labels its own drops (commit `fc5a1dc`, v4 S29–S31)

- `subproto/implicit.py` harvests three signals from local telemetry plus the recorded
  bodies — re-read, correction, re-run — into `implicit_labels.json`, a store kept
  separate from the human `subproto label` verdicts so a guess never overwrites one.
  Only an **enforced** cut is allowed to claim a token cost; a shadow re-read is reported
  as `would_have_been_live`, never double-billed.
- The harvested labels reach the training split (a contradicted drop is taught as `keep`;
  a complaint only removes supervision) and print as `subproto report`'s eviction-regret
  section through a shared renderer, so `learn` and `report` cannot drift.
- `subproto learn` and `subproto report` now show what the recorded traffic said about
  the drops.
- S31 closed a hole in the graph: `PATH_RE`'s unbounded extension alternation matched
  `js` inside `.json`, so every data path had been truncated on read since v1 and could
  never couple to the graph.

## Checkpoint versions and the cadence policy (commit `2784d62`, v4 S33–S34)

- `models.json` holds versions behind `active`/`previous` with a `/health` gate: a dead
  endpoint degrades to the heuristic **with the reason printed** (`pinned <label> is
  unreachable…; every decision it would have answered says heuristic`), and every
  decision carries the `model_version` of whatever actually produced it.
- `subproto retrain` decides whether a fine-tune is worth starting from local state only
  (content, balance, time, provenance), refuses out loud, and keeps an append-only
  `training_history.json` that a refused row can never age. It prints and runs nothing
  unless `--run` says so *and* the cadence cleared.
- Two defects only running it found: `--run` named a split nothing had written, and
  `--json` was unparseable because the trainer's progress line inherited stdout. Both
  fixed and tested.

## The v2 audit: two SPEC check-offs were false (commit `0d9a340`, S15–S19)

Re-verifying every FR against the code and running each documented command as a fresh
user turned up gaps the green suite was not seeing:

- **I5 was not byte-for-byte.** `content-encoding` sat in `HOP_BY_HOP`, so a gzipped
  request reached the upstream as unlabelled gzip (a 400 it cannot explain) and a gzipped
  response reached the SDK as undecodable bytes; response `content-length` was dropped
  too. Both directions now relay end-to-end headers unchanged.
- **I2 was not held by the `context` slot**, which injected the graph note into the
  top-level `system` string — for Anthropic that string *is* the cached prefix, so
  enforcing it would have cost more than it saved. The note now lands after the last turn.
- **`applied`** came from membership of `SUBPROTO_APPLY`, so a slot that changed nothing
  still counted as an intervention. It is now derived from the slots that actually
  rewrote the body, and `effort` is declared advisory-only (`ADVISORY_SLOTS`).
- `subproto graph <typo>` died with a raw traceback; `--out` into a fresh directory did
  the same.

## Per-slot model choice and the evidence-driven router (commits `d10566a`, `3e4341d`)

- `subproto/systemone/router.py`: an opt-in (`SUBPROTO_ROUTER=on`) Router picks each
  un-pinned, model-backed slot from measured `bench/ablation.py` precision under an
  optional `SUBPROTO_ROUTER_BUDGET_MS`, instead of a hand-named model. An adapter with no
  measured precision is never auto-picked, and precision ties fall to `heuristic` — so on
  the shipped evidence (where Laya lands *below* the heuristic) the Router correctly stays
  on heuristics rather than faking an edge. Exposed as `subproto models --router`.
- Per-slot selection: `SUBPROTO_MODEL_COMPACT=djev` (or `model_by_slot` in the config
  file) lets one slot run a different model than the global choice. Only `tool_gate` and
  `compact` are overridable — `context` is answered by the code graph and `effort` by
  request shape, so pinning a model to them would record a label that decided nothing.
- The ablation now drives every column through a real `POST /score` via
  `HTTPScoreAdapter`, the same path the engine uses; a label with no endpoint is reported
  as skipped, never as a borrowed result. Committed `bench/` files carry no URLs or ports.

## The System One backend became a config choice, not a Laya bet (commits `ded4ad0`, `ecb7455`)

- `subproto/systemone/` extracted a `ModelAdapter` contract, one generic `HTTPScoreAdapter`
  any `/health` + `/score` classifier can hide behind, and a registry resolving
  `SUBPROTO_MODEL` (or `--model`, or the legacy `LAYA_URL`) to an adapter plus the label
  each decision records. Selection is total about degradation: an unknown name, `heuristic`,
  or a configured model whose server is down all fall back to the in-process heuristics
  rather than breaking the proxy. `subproto models` makes the whole surface inspectable.
- `ROADMAP.md` (v2→v13) reframed the tiny decision model as a swappable component.

## CLI first-run fixes (commits `d2e8464`, `eccd99d`)

- `subproto export` with no `--slots` no longer crashes (`TypeError: argument of type
  'NoneType'`); `None` now means "every slot".
- `subproto live --once` reported `save 454%` on cache-heavy traffic; it now divides by the
  same full estimated-input basis as `report`, and shows the honest share.
- `subproto demo` honours `SUBPROTO_HOME`, so `subproto demo --slots` then `subproto report`
  share one data dir instead of the demo landing traffic in a different directory.

## Benchmark artifacts and corpus (commits `174194a`, `c909e24`, `0861971`, and earlier)

- The mock task corpus expanded 8 → 20 SWE-bench-shaped tasks; re-measured, the per-slot
  arm shows **−29.9%** input tokens (n=20, `[28.2, 31.3]` CI) with pass-rate held at 100%
  in both arms (`bench/live_results.md`; this is measured against the mock, **not** billed).
- `bench/results.md` keeps the *projected* all-slots-enforce figure (−32.3%) clearly
  labelled as a projection from measured prompt shapes, not delivered savings.
- The Laya latency curve (`bench/laya_latency.md`) records that `convaiinnovations/laya`
  is a torch-CPU ModernBERT encoder, not an MLX model, that there is **no MLX path to it**,
  and that on the shipped `choice` shape it costs 123 ms p50 (fits the 350 ms budget) yet
  measures f1 0.200 against the heuristic's 0.979 on this corpus.
- `bench/ablation_results.md` records the Laya precision delta against the heuristic as
  **−0.083** (threshold rule) / **−0.099** (ranked), and states plainly that the gold sets
  were written around the heuristic's own rule.

## Initial build (commits `ad3e978` … `9ebc562`)

- The System One decision layer, the multi-dialect transparent proxy (OpenAI chat +
  `/responses`, Anthropic `messages`, Gemini `generateContent`), telemetry and the
  waste/slot-precision report, the four decision slots in observation mode with opt-in
  enforcement, the code graph, dataset export + the `label` loop + a deterministic
  train/val split, the guarded MLX LoRA script, the local Laya scoring server, and the
  live terminal savings meter.
- `README.md` leads install with the working source path and flags PyPI as pending.
