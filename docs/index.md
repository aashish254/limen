# Limen — documentation map

A local *System One* layer between a terminal coding agent and its LLM provider: it
decides, before the turn is paid for, which tools and which context that turn needs.
Zero runtime dependencies, Python 3.9+, stdlib only, no key and no network to run.

**Limen** is the project. Every command below is `subproto`, the package name, because
these pages document the code rather than the brand.

This is the map. Read `README.md` first; the rest is reference.

## The repository documents

| File | What it is |
|---|---|
| [`README.md`](../README.md) | The front page: what it does, one-command install, the quick start, the honest number and its conditions, the design-system note. Start here. |
| [`WIRING.md`](../WIRING.md) | Per-agent setup. The `base_url` each supported tool uses (Claude Code, Codex, Gemini CLI, Cline, aider, OpenCode, Antigravity), the endpoints subproto serves, and how to confirm it is live. |
| [`subproto/tests/test_invariants.py`](../subproto/tests/test_invariants.py) | The six invariants (I1–I6) as executable checks — byte-for-byte passthrough, tail protection, the correctness floor. What the project promises is a test, not a paragraph. |
| [`CHANGELOG.md`](../CHANGELOG.md) | Release history grouped by what a user gets, honest negatives kept visible, ending in the release-readiness work in flight. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`SECURITY.md`](../SECURITY.md), [`LICENSE`](../LICENSE) | How to contribute; the security and disclosure posture; Apache-2.0. |

## Reference docs (here)

| File | Contents |
|---|---|
| [`commands.md`](commands.md) | All 17 subcommands: what each does, the flags it actually accepts, a real invocation, and the page it prints. |
| [`configuration.md`](configuration.md) | Every env var and config-file key, its default, what it changes, and which invariant it can break. `SUBPROTO_APPLY`, `SUBPROTO_MODEL`/`LAYA_URL`, and `SUBPROTO_ROUTER` have their own subsections. |
| [`troubleshooting.md`](troubleshooting.md) | The failures a newcomer actually hits, as symptom → meaning → fix. |

## Bench artifacts

Numbers must say where they came from. The committed artifacts in [`bench/`](../bench)
split three ways: **measured** (a command prints it), **projected** (a model of what real
traffic would do), and **gated** (blocked on a human action) — see SPEC invariant I6.

| Artifact | Kind | What it establishes |
|---|---|---|
| [`bench/live_results.md`](../bench/live_results.md) | measured (mock) | Pass-rate–held harness, sample set n=20: **−29.9%** input tokens (CI `[28.2, 31.3]`), pass-rate held 100%→100%, against the local mock at $0. The compiled arm beats per-slot on accuracy (precision +17.0 pp). |
| [`bench/live_results_hard.md`](../bench/live_results_hard.md) | measured (mock) | The hard set, kept deliberately: the **per-slot** arm drives pass-rate to 0.0% (I3 VIOLATED); the **compiled** arm holds 100%. |
| [`bench/results.md`](../bench/results.md) | projected | If all slots enforced: **−32.4%** input tokens, labelled a projection from measured prompt shapes — not delivered savings. |
| [`bench/laya_latency.md`](../bench/laya_latency.md) | measured (one host) | The real Laya checkpoint's per-decision latency curve, carrying its host load average and runtime conditions; shows `choice` fits the 350 ms budget and that there is no MLX path to the model. |
| [`bench/ablation_results.md`](../bench/ablation_results.md) | measured (synthetic) | Laya vs the heuristic on the shipped corpus — Laya lands **below** the heuristic (Δ precision −0.083), and the file says the gold sets were written around the heuristic's own rule. |

The **billed hero number** (input tokens down, p50 TTFT down, at a held pass rate against
a real provider) is **gated** on real API spend and a public task suite; it is not in these
files. `README.md`'s roadmap names each open item and what it waits on.
