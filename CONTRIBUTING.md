# Contributing

subproto is a local proxy that decides, before your agent pays for a turn, which tools
and which context that turn actually needs. Two things decide whether a change lands:
it must keep the invariants below, and it must come with a test that would have failed
without the change.

## Five minutes to a working branch

```bash
git clone https://github.com/aashish254/limen && cd limen
python3 -m venv .venv && source .venv/bin/activate
pip install "pytest>=7"              # the package itself has no runtime dependencies
bash run_tests.sh                    # the whole gate: ~2 h, prints ALL GATES PASS
python3 -m subproto demo --port 8000 --slots
python3 -m subproto report
```

`run_tests.sh` is the contract CI enforces. Running only `pytest` is not enough: the
gate also runs every bench, the demo, the label flywheel over the demo's own traffic,
the model-version ladder, the cadence log and the mutation gate.

**Budget your time around one line of that script.** The 411-test suite takes ~40 s and
every bench takes seconds; the mutation gate takes ~65 min *per interpreter*, because 156
mutants each cost one ~25 s run of an 18-file test subset (measured 2026-09-27 on a 32 GB
laptop at load ~4; a clean CI runner is faster, and CI runs that leg on one interpreter).
If you only changed a doc or a test, run `python -m pytest -q` and the bench you touched —
CI will run the gate. When you do run it locally, treat it as owning the tree: it rewrites
real source files with mutants and restores them byte-identically at the end, so do not
edit a patched file, and do not kill it mid-run.

## The invariants are the review criteria

- **I2 — never rewrite the cached prefix.** Compaction appends or cuts the middle; it
  does not touch the bytes a provider has already cached. Busting prompt caching costs
  roughly ten times what the cut saves.
- **I3 — the tail is sacred.** Recent turns and open errors are dropped last, the
  behaviour is reversible, and correctness is the floor: no change may take the
  pass-rate down by more than 1%.
- **I4 — local only.** Nothing leaves the machine except the request you sent. A
  contribution that phones home, even for analytics, will not be merged.
- **I5 — byte-transparent passthrough.** When subproto decides nothing, the response
  your agent gets is byte-identical to the provider's.
- **I6 — measured, not claimed.** A number in a doc, a README or a print must come
  from a command someone can re-run. Label it *measured*, *projected* or *gated*.

## Adding a model backend

`subproto/systemone/registry.py` is the only file that knows model families. Two ways
in, and neither requires touching the engine:

1. **An endpoint.** Any HTTP server that answers `GET /health` and `POST /score`
   (`{"state", "question", "options"}` in, `{option: probability}` out) can be used as
   `SUBPROTO_MODEL=http://host:port` with no code here at all.
2. **A registry entry.** Add a label, the env var that configures it and one line of
   `about` (keep the printed page under 100 columns), and the router, the report and
   `bench/ablation.py` pick it up. `subproto/systemone/base.py` already speaks the
   contract.

A backend that cannot answer must be *skipped with a reason*, never scored as "kept
nothing" — see `bench/ablation.py`'s `no_answer` accounting.

## Platforms

CI is the honest map of what is supported. **ubuntu-latest and macos-latest on Python
3.9 and 3.11 are release gates** — a red run there blocks a merge. **windows-latest runs
and reports, but `continue-on-error`**: the code paths a Windows machine takes first
(`os.getloadavg` in the latency bench, console encodings, path separators) are covered by
tests and by mutants, and no Windows machine is in this project's loop yet, so the row is
informational rather than a promise. A Windows contributor who turns that row green with
a real finding is very welcome; the PR that flips `continue-on-error` off is a release
change, and it should say which machine checked.

Anything that reads or writes a file in this package names its encoding, and anything
that prints names its stream — `subproto/tests/test_style.py` runs two real child
processes under `PYTHONIOENCODING=cp1252` to keep that true rather than intended.

## Tests, and the gate that checks the checks

`subproto/tests/` is the suite. Two project-specific conventions worth knowing before
you write one:

- **Design is a rule.** Anything that prints for a human goes through
  `subproto/style.py` on the shared grid: colour only on a state word, red only for
  breakage, no box-drawing, no fixed-width frames, `MAX_W = 100` per line.
  `subproto/tests/test_style.py` renders every page and fails the ones that drift.
- **A passing test is not proof.** `bench/mutation_gate.py` breaks one rule at a time in
  the real source and fails the build if the suite does not notice. If you add a rule,
  add its mutant; the anchor must appear exactly once in the file, and the entry is
  `(id, path, exact-old, exact-new)`.

For terminal/ANSI work, `subproto live --once` and every `report` section render
deterministically from their input, so assert on the string, not on a screen.

## Cutting a release

The `__version__` in `subproto/__init__.py` *is* the release:
`.github/workflows/publish.yml` builds the
tag, fails if the wheel it produced does not carry the version the tag was cut from, installs
that wheel into a fresh offline venv, runs it from a directory that is not the clone, and only
then uploads. So the sequence is: land the change, wait for `verify` to go green on it, bump
the version, tag, and push the tag — `git tag -a v0.1.1 -m "…" && git push origin v0.1.1`.
Then, once the index actually carries it, rebuild the page (`python3 tools/make_site.py`,
commit `_site/`) and update the prose that quotes the number.

No upload credential lives in this repo or on anyone's laptop. The identity is a *trusted
publisher* registered on pypi.org for the project: owner `aashish254`, repository `limen`,
workflow `publish.yml`, environment `pypi`. If that registration is missing or wrong the run
goes red and nothing is spent — a version number is only consumed when the upload succeeds.

The bump is one line, but the number is also quoted in prose: `README.md`, `docs/commands.md`,
`tools/site.professional.tmpl` and `tools/make_site.py`. Those surfaces say what the index
*has*, so they are updated in the commit after the upload goes green — never in the commit the
tag is cut from, which would claim a release that does not exist yet. `docs/commands.md` quotes
the literal output of `subproto --version`, so re-capture that line from the installed wheel
instead of editing its digits.

`0.1.0` is the exception, and it is worth knowing before you diff it against the rule: it went
out by hand with a short-lived token on 2026-09-28, before any publisher existed, and its tag
points one commit before the classifier fix — so building `v0.1.0` and uploading it would be
rejected for a bad Trove classifier. The version is consumed either way, so nothing follows
from that; the next tag is the first one CI publishes.

## Pull requests

- One rule per PR, and its test in the same commit.
- Paste the command you ran and its real output into the PR description. If a number
  moved, say what it was before.
- If your change weakens a claim in `README.md`, `SPEC.md` or `ROADMAP.md`, update the
  claim in the same PR. Docs that outrun the code get reverted, not starred.

Issues: bugs need the command, the Python version, the OS and `subproto doctor`.
