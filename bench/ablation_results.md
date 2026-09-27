# subproto slot ablation — tool_gate (labelled synthetic)

Cases: 10 · agreement with the heuristic, per adapter:

- `laya`: 27%

## Read as the engine reads it — keep the options at p ≥ 0.5

| backend | precision | recall | f1 | Δprecision vs heuristic |
|---|--:|--:|--:|--:|
| heuristic | 0.972 | 0.986 | 0.979 | — |
| laya | 0.889 | 0.113 | 0.200 | -0.083 |

laya − heuristic precision delta: **-0.083**
laya round trip: p50 131 ms, p95 155 ms, max 160 ms over 10 calls.

## Read as a ranking — keep the top k, k = the gold set size

A calibrated `choice` answer is a distribution over the options (it sums to 1), so at most one option can clear 0.5 and the table above measures the *rule*, not the model. The same answers read the way the compiler selects — rank them and cut at the token budget — score:

| backend | precision | recall | f1 | Δprecision vs heuristic |
|---|--:|--:|--:|--:|
| laya | 0.873 | 0.873 | 0.873 | -0.099 |

Whether the slot should threshold or rank is an open decision (SPEC FR-4): ranking this well on a synthetic set is not evidence to change a keep rule that guards correctness (I3).

## What this corpus can and cannot measure

The gold sets were written around the heuristic's own rule, so on 7 of 10 cases the baseline's answer *is* the answer key (3 of 86 tool decisions disagree: 2 wrong keeps, 1 misses). A precision delta on this set is bounded by those few rows and favours the baseline by construction — it is a pipeline test, not a model comparison. Real labels (`subproto label`, V2-A) or a set labelled without the rule in view is what makes the two columns mean something.
- `read app/config.py and run the failing tests` — gold-only —, heuristic-only mcp__figma__read
- `open a pull request and fetch review comment` — gold-only mcp__github__pr, heuristic-only —
- `refactor the retry loop, keep interfaces sta` — gold-only —, heuristic-only mcp__figma__read

*Note: `laya` is a **configured endpoint** (`LAYA_URL`), not the bundled stand-in: every number in its column came from a live round trip to a real scorer. To reproduce them, start one (`python -m subproto.laya_server --backend laya`, which needs the ≥ 3.10 venv) and export the URL; without an endpoint this column is skipped, never guessed.*

*Every adapter column is a real `POST /score` through `subproto.systemone.HTTPScoreAdapter`, so swapping the scorer behind a label changes only the number, not this file. Ground truth is author-labelled and replaced by the `subproto label` corpus at M4 (SPEC §1.2).*
