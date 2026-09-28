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

## 0.1.1 — the first release published by CI

`subproto 0.1.1` is on the index, and CI put it there. No credential was used by anyone.

- Run `36438640051` (tag `v0.1.1` → commit `934e8c1`), 1m45s, every step green: the
  classifier preflight against PyPI's own page, `python -m build`, the artifact-carrying-the
  tag's-version check, the offline install-and-run smoke, then
  `pypa/gh-action-pypi-publish@release/v1` over OIDC — `Found and verified trusted root`, and
  two publish attestations. `pypi.org/pypi/subproto/json` now answers
  `releases: ['0.1.0', '0.1.1']`, `latest: 0.1.1`.
- **The first attempt did not publish, and that is why the number is reusable.** The same tag
  name pointed at `e1717e6` an hour earlier; its run `36428218899` died at
  *Install the built wheel offline and run it* with
  `.smoke/bin/subproto: No such file or directory`, exit 127. The step `cd`s into a fresh
  temp dir precisely so nothing can reach the source tree, then addressed its own venv by a
  *relative* path — so the path resolved against the temp dir, not the clone. The upload step
  was `skipped`, the index still served only `0.1.0`, and a red run consumes no version, so
  `v0.1.1` was re-pointed at the fixed commit instead of skipping to `0.1.2`.
- `subproto doctor --port 0` exits 0 from that scrubbed position (`env -i`, no provider keys,
  no local model server, no `laya`), so the smoke leg passes on a machine that has nothing —
  which is what a runner is.
- Verified from the index like a stranger: fresh 3.11 venv, `pip install subproto==0.1.1` with
  no local artifacts, `pip check` → `No broken requirements found.`, then
  `subproto 0.1.1 (python 3.11.15, darwin arm64)` and `tools/check_install.sh` → exit 0 from a
  directory that is not the clone. The files the index serves hash-match the ones that run
  built: wheel `40805f2153f663ae…`, sdist `2fb9a6a41ce0c565…`.
- The pruned tree reached the artifact, checked on the *published* sdist rather than a local
  rebuild: 141 members, and `SPEC.md` / `TODO.md` / `ROADMAP.md` return 0 matches. `0.1.0`'s
  sdist is immutable and still carries all three; that is history, not something a later
  release can undo.
- Gated on run `36429332435` (`completed success`, 1h10m11s on `934e8c1`): the four
  pytest legs and four offline pip-install legs on macOS and Linux at 3.9 and 3.11, plus the
  one-command leg — benches, demo, mutation gate (`bench/mutation_gate.py` carries 157
  mutants against a 443-test suite). Windows stays non-gating and its leg failed again with
  `4 failed, 436 passed, 3 skipped`: four posix-only assumptions in the *tests* — a `/`-only
  copyable-path class, two `bodies/` searches, and an `os.getloadavg` premise — one of which
  shows a Windows temp path overflowing the 100-column terminal measure. Fixing those is test
  work, not a portability claim this release can make.

---

## Unreleased — the release-readiness pass

Work to make this tree something a stranger can clone, install and run. Each item below
is in the tree and has a test behind it; the items that are *not* in the tree are in
**Still gated**, at the end, and nothing here checks one of them off.

### The Windows leg's four red rows

`windows-latest` reported `4 failed, 436 passed, 3 skipped` on every run since S41. All
four were the *tests* reading a POSIX machine that was not there, and the CI log named
each one exactly:

- `test_doctor.py` and `test_style.py` share a `COPYABLE` class — the line shapes allowed
  past the 100-column measure, because the reader copies them. Its path arm was `\S/`, so
  `C:\Users\…` was not a path to the gate, and the `data dir` row's 129 columns failed.
  The arm now reads both separators. The check that this did not simply widen the
  exemption: the same gate still refuses a 123-column *sentence*.
- `test_show.py` searched for the stored body's line as `"bodies/"`, twice. On Windows the
  page prints `bodies\`, so the paste test lost its one documented exemption and the
  tilde test indexed an empty list. Both now go through a `_is_body_path` helper that
  accepts either separator, and the elision assertion builds its expected path from
  `os.sep` rather than a literal `/`.
- `test_laya_latency.py` opened with `assert hasattr(os, "getloadavg")` — a premise about
  the host, stated as a test. It now asserts the *correspondence*: three floats where the
  kernel answers, `None` where it does not, and the rendered conditions block saying the
  same either way. The product needed no change; `bench/laya_latency.py:_load_avg` has
  caught `AttributeError` since S40.
- Added `test_a_windows_home_elides_the_same_way`, which drives `cli._shown_path` with
  `os.sep` patched to `\` and a `C:\Users\alice` home. Without it, the elision this repo
  shipped as the fix for a page that broke mid-token was only ever exercised with slashes.

Witnessed locally by replaying the two over-wide lines from job `108951146157`'s log
against the fixed gates (both now pass, at 129 and 164 columns), and by the full suite on
both release interpreters: **444 collected, 442 passed, 2 skipped on 3.11 and on 3.9** —
the two skips are the laya-backend legs that need an interpreter that can import `laya`.
The collected count moved 443 → 444 because of the new test, and `_site/index.html` was
rebuilt so the page prints 444 too. **The next `windows-latest` run is the witness for the
claim itself** — no Windows machine is in this project's loop, so `continue-on-error` stays
on and README's "not tested by us" stays true.

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

### Cloned and run on Linux, by the route a stranger takes

The install claim on the front page said "macOS and Linux". Linux had not been run, so it
was run, on `python:3.11-slim` and `python:3.9-slim` (Linux aarch64):

- **410 passed, 3 skipped** on Python 3.11.16, the same on 3.9. The skips are all
  environment, not defects: root can write anywhere, so the `doctor` permission check has
  nothing to fail on, and the two live Laya legs skip because that container has no
  `laya` install. Byte-compile under `-W error` is clean.
- `python -m build` then `pip install --no-index --find-links dist subproto` into a fresh
  venv, and the commands run from a directory that is not the clone: `--version`, `doctor`,
  `demo --slots`, `report`, `show`. All five print on Linux, and `report` counts the 17
  requests `demo` just pushed through the mock.
- **One real bug surfaced, and it was in CI rather than in the product.** The
  "does the sdist carry the bench and the mock" step computed the tarball's top-level
  prefix as `dist/subproto-0.1.0`, while tar members are named
  `subproto-0.1.0/bench/ablation.py`. Every file therefore read as missing: the step is a
  false red that would have failed the first workflow run on a green artifact. Fixed to use
  the basename, and re-run against the tarball the container built — 136 members, all six
  required paths present.
- Two earlier runs are reported as artifacts rather than hidden: the first Linux suite
  failed one test because `python:*-slim` ships without `git` and the provenance check
  needs the commit graph, and a read-only mount without `PYTHONDONTWRITEBYTECODE=1` made
  `py_compile` raise on writing `__pycache__`.

### The launch pass: a name, a landing page, and the numbers re-measured

- **The project is Limen** — the threshold a stimulus has to cross before it is noticed;
  here, the decision an agent makes before it spends anything. The rename is brand and repo
  only: this repository, the README, the docs and the landing page say Limen, while
  `pip install subproto` and `subproto demo` stay, because every wiring guide and config
  example in the wild says `subproto`. README's `Names` section states the split in one
  place rather than leaving it to be inferred from a mismatch.
- **`bench/launch_stats.py` is the single run the launch numbers come from**: three arms
  (observe / enforce / compiled) over 20 sampled tasks × 5 repeats, seed 7, against the
  in-repo mock — `$0`, no key, nothing leaving the machine. Headline **−43.6%** of billed
  input between observing and compiling, with pass rate and recall held at 100%. The host
  is recorded beside it (Darwin, Python 3.11.15, load average 6.17 at the start), because a
  timing row without its load is a claim rather than a measurement.
- **The landing page is the printout, not a document about it.** `tools/make_site.py` and
  `tools/site.html.tmpl` build `_site/index.html` — published by
  `.github/workflows/pages.yml`, which refuses a page with an unrendered token — from
  `bench/launch_stats.json`, the test/mutant/subcommand counts and two live demo replays.
  **No number on the page is typed by hand.** Each figure carries a stamp naming its kind:
  *measured* (the arms above), *projection* (`bench/ab.py --mock`, inside the same run),
  *delivered* (the 17 replayed requests, bodies stored), *curve* (the checkpoint latency
  curve read from `bench/laya_latency.json`, not re-run for this page). The builder reads
  the projection's command out of the bench source and **fails the build** if the bench
  stops calling `ab.py` the way the page says it does — a caption that cannot go stale
  silently is worth more than a caption that is merely correct today.
- **A figure is a view of the record, so a caption can be fixed without re-measuring.**
  `render_figures(stats)` draws every panel from the json, and
  `bench/launch_stats.py --figures-only` redraws them and measures nothing.
- **A heading takes another line, never a wider canvas** (`CANVAS_MAX = 980`, `_wrap`,
  `_legend`): the page scales each SVG into its own column, so a 1,690-unit panel came back
  as 7-pixel type — the same defect as a clipped title, arrived at from the other
  direction.
- **The mutation gate's bookkeeping went stale in one place, and a sweep found it.** All
  156 `(id, file, anchor, replacement)` triples were re-checked read-only: **S42-3 pointed
  at a line `demo` no longer prints** — the sandbox pointer became a
  `subproto report --home …` action line — so that mutant could no longer be applied, let
  alone killed. Re-anchored, and B7's interpreter branch became mutant **S46-B7**
  (156 → **157**). Both were probed in seconds by applying one anchor, running the two
  covering test files, and restoring, rather than 65 minutes into the gate.

### The landing page, redesigned: what a reveal actually hides

The first generated page was honest about its numbers and ugly to look at — a dark
background, one accent, and sections that faded in on scroll. Rebuilding it from the token
system up turned into an audit of *when the page is allowed to show its content*, and most
of the fixes were about motion that could fail.

- **A reveal that hides a section until an observer fires is a section that can stay
  hidden.** The scroll-in animation set `opacity: 0` on every `section` and un-hid it from
  JavaScript, so with scripting off, on a deep link, or in a full-page capture the page was
  blank below the fold. The reveal is deleted; what is left is a decorative hairline that
  animates itself and no content. Every entrance on the page now **animates *from* a
  start state rather than *to* an end state** — the finished value is the declared style
  (`width: var(--w)`, `transform: scaleX(1)`) and the keyframe runs with `backwards` fill,
  so a browser that never executes it still shows the design as designed.
- **The same bug in the counters.** `data-count` spans began at `0`, so a non-executing
  reader got a page whose headline number was zero. The markup now carries the real value as
  its text content and `countUp` ends by writing that same string — the animation is an
  ornament on the number, not the source of it.
- **A 100-column printout does not fit a 530-pixel column.** In the two-column hero the
  terminal was clipped mid-word, which is the exact defect `MAX_W = 100` exists to prevent.
  The terminal, the threshold bars and the stat strip are now full page width; only the
  headline and the lede share a row. A `.term-hint` tells a narrow reader the block scrolls.
- **Nine figures, nine type sizes.** Each inline SVG scaled to its own `viewBox`, so a
  768-wide panel and a 976-wide panel rendered the same 12px label at different sizes on the
  same screen. `figure_svg()` now normalises every canvas onto one **976×320 plate** and
  centres it in it — margins vary, type holds still — and *fails the build* if a future
  canvas exceeds the plate, because the alternative is a silently cropped drawing.
- **A rule that never matched its own markup.** The plate styling was written as
  `.figscroll.scroller`, and the builder emitted `<div class="figscroll">` — so the white
  ground, the radius, the shadow and the mobile scroll affordance were all dead declarations
  while the page still *looked* roughly right, because the SVG's own `width`/`height`
  attributes happened to give the box the plate's ratio. Found by measuring each plate's
  computed background in the browser rather than reading the CSS: it was `rgba(0,0,0,0)`. The
  selector is now `.figscroll`, the builder emits `scroller` too so the overflow hint applies,
  and the drawings (white ground from `launch_stats.py`) sit on a mat that is the same paper
  rather than the page's cream.
- **Overflow is now signposted, not silent.** Below 900px the wide tables and plates scroll
  sideways; a sticky `slide sideways to read the rest →` strip appears only while there is
  actually more to the right, measured on scroll and resize rather than guessed from the
  breakpoint. URLs wrap at slashes again — `word-break: break-all` had been breaking hosts
  mid-name, which turns a copyable command into a wrong one.
- **The mark.** Limen is the threshold a stimulus has to cross: a dim bar with its lit
  portion, cut by a crimson rule at the crossing point. It is drawn once, in three sizes
  (nav, footer, favicon), and the same geometry as the hero's threshold bars — the logo and
  the argument are the same picture.
- **`og.png` is part of the build.** The social card is a screenshot of this page, so a link
  preview that 404s is a launch post that shows nothing. The builder copies
  `docs/assets/og.png` into `_site/assets/` and exits non-zero if it is missing.
- **Re-verified after the rebuild:** two `--skip-hero` builds are byte-identical; **436
  passed / 2 skipped** on both Python 3.11.15 and 3.9.6; the secret sweep over the pending
  diff and the tracked tree returns no key-shaped string — and the surviving bare `sk-`
  substrings were read rather than waved through: two `WIRING.md` placeholders that end in an
  ellipsis, the word `task-file`, two CSS `mask-image` lines, and one canary in
  `test_doctor.py` whose whole purpose is to assert `doctor` never prints a key's value. At 1440, 1024, 820 and 390 the document has no horizontal
  scroll, no section renders empty, and every element that overflows its box was checked to
  sit inside a scroll container — 7 at 820px and 73 at 390px, all contained. Captures were
  re-shot and read, because a screenshot is the only witness that a layout change laid out.
- **No gate re-run is owed:** nothing under `subproto/` or `bench/` changed, so no mutant
  anchor moved and no print contract moved. The page's numbers are the S46 numbers.

### What the first-time-user run got wrong

Each of these is a surface that said something the code did not do, and each now has a
witness in `subproto/tests/`. The `B` items change a printed number; `M` and `m` items
change what a page does when the reader's input is not what it expected.

- **B1** — `subproto demo` and then `subproto report` printed *0 requests*: the demo wrote
  one home and the report read another.
- **B2** — a per-slot record lists a cut twice and `show` believed it, printing
  `12 kept 24 cut` with 24 rows for 12 tools.
- **B4** — the mock answered with a fixed usage block, so traffic was priced for a body it
  never sent. `fakeup` now prices the body it was handed, which moved every dollar figure
  in the repo: **−43.6%** is that re-measure, and the older number is gone with it.
- **B5** — `0 cached` printed over a request that had read 2,464 tokens from the provider's
  cache. `billed input` follows `report`'s convention now and names the read beside it.
- **B6** — one joint-compiler record carries the whole proof, and the page listed all of it
  under each slot, so a request read as having lost 18 items when it lost 15.
- **B7** — `doctor` told a reader on Python 3.11 that `laya` was "not importable here" and
  to move to a "Python >= 3.10 environment". The version was not the blocker; the missing
  install was. The row branches on the interpreter it is actually running on.
- **B8** — `show`'s one long line — the stored body's path, printed so a drop's pointer can
  be opened — wrapped mid-token in a screenshot. Inside the home it prints as `~/…`, which
  fits the page's 100-column measure and still pastes into a shell; `--json` keeps the
  absolute path, because a script has no home to expand.
- **M1** — the `up` banner read the environment instead of asking the engine, so it could
  say *observing* while the slots were enforcing.
- **M3** — a date `report` could not parse failed without saying what it wanted. One named
  error, raised at the boundary, printed by the dispatcher.
- **M4** — the ablation evidence was a bench-side copy of a file the product ships, which is
  how a figure quietly goes stale. It lives in `subproto/systemone/evidence/` now, and
  `bench/ablation.py --write` refreshes it.
- **m2, m3, m4, m8** — `--path` failing at a file the reader never typed; an unbounded
  filter end silently not applied; a `--json` payload that could not distinguish "no such
  request" from "a request with no decisions"; an unknown `--model` name resolving to
  nothing and printing nothing.

### The terminal, photographed rather than described

- `docs/assets/terminal-stills.sh` re-makes `docs/assets/terminal/*.png`: five pages run
  through a pty against the installed wheel and rendered with `agg`, so the colour choices
  are the ones a real terminal makes. Each page measures its own output first and is given
  exactly that many rows, so nothing scrolls out of the frame, and the still is the last
  frame — everything printed, nothing mid-scroll.
- README's `What it prints` section embeds three of them with the command named in the
  caption, and `docs/assets/first-30-seconds.gif` was re-recorded against the same wheel so
  the hero shows the pages as they now read.

### 0.1.0 is on the index

- `subproto 0.1.0` is published at `pypi.org/project/subproto`, and `v0.1.0` is tagged, so
  README's first install line is `pip install subproto`. Verified the way a stranger
  verifies it: a fresh Python 3.11 venv, install from the index alone, then `--version`,
  `doctor --port 0` and `demo --slots --audit --home /tmp/subproto-try` from a directory
  that is not the clone. The wheel and sdist the index serves hash-match the files built
  here (`e07c4a42255d…`, `7e33a22d4251…`).
- **The first rejection was metadata, not the credential.** `pyproject.toml` declared
  `Topic :: Software Development :: Debug Tools`, which has never been on
  <https://pypi.org/classifiers/>; the index 400s the entire upload for one invented trove,
  after both files have already transferred. Replaced with `Topic :: Internet :: Proxy
  Servers` and `Topic :: Software Development :: Quality Assurance` — the valid troves for
  what this is — and the classifier list is now checked against PyPI's own page before a
  release builds.
- `.github/workflows/publish.yml` is the release path from here: build from the tag, prove
  the artifact carries the version the tag was cut from, install the wheel offline and run
  it outside the clone, then upload by OIDC. No token is stored on a laptop or in this repo.

### Still gated — not in this tree

- **The launch post, and a `1.0`.** The tag and the index are done at `0.1.1` (above). The
  rest is human, not installable: the HN/Product-Hunt post, and any version number that
  claims more than an alpha proxy — which is gated on V2-A labels and a billed V2-B run.
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
