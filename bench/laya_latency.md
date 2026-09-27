# Laya per-decision latency curve (torch CPU, 30 cases)

Measured through `subproto.laya_server.LayaScorer`, so the question asked here is the question the server asks.

- model `convaiinnovations/laya` on device `cpu`, torch 2.6.0, laya 0.3.20, python 3.11.15, 4 threads on 10 cores
- host load average at start 7.79, 7.47, 7.74, at end 10.16, 8.17, 7.98 (per-core: 0.78 → 1.02). The start reading is the machine this ran on; the end reading includes this run's own 4 threads, so read it as evidence of what the run cost, not of what it was competing with.
- what the runtime said while answering: `laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as uncalibrated.`
- the shipped per-decision budget is `SUBPROTO_MODEL_TIMEOUT_MS` = 350 ms; a shape that exceeds it is not usable in the engine, and the verdict column says which is which.

| shape | passes per decision | ms p50 | ms p95 | ms max | fits 350 ms | precision | recall | f1 |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| choice | 1 | 123 | 133 | 139 | yes | 0.889 | 0.113 | 0.200 |
| keep_drop | one per option | 800 | 1250 | 1383 | no | 0.815 | 0.620 | 0.704 |
| noul (not shipped) | one per option | 1097 | 1581 | 2386 | no | 1.000 | 0.042 | 0.081 |
| heuristic (in-process, no model) | 0 | 0 | 0 | 0 | yes | 0.972 | 0.986 | 0.979 |

## The same answers read as a ranking

A `choice` answer is a distribution over the option set (it sums to 1), so at most one option can clear 0.5 and the threshold column above measures the *rule*. Ranked and cut at the gold set's size — which is what a token budget does — the same passes score:

| shape | threshold f1 | rank f1 | relative f1 |
|---|--:|--:|--:|
| choice | 0.200 | 0.873 | 0.244 |
| keep_drop | 0.704 | 0.873 | 0.829 |
| noul | 0.081 | 0.859 | 0.695 |

The heuristic's f1 on this set is 0.979, and the gold sets were written around its own rule (see `bench/ablation.py::corpus_ceiling`), so these columns bound how far apart any scorer can get on it. Whether the slot should threshold or rank is an open decision (SPEC FR-4); ranking well here is not evidence to relax a keep rule that guards correctness (I3).

## What this says about the locked MLX decision

There is **no MLX path to this checkpoint**, and the reason is architectural rather than logistical: `convaiinnovations/laya` is a ModernBERT-large encoder with a two-layer classification head (`pipeline_tag: text-classification`, `usage.output_tokens: 0` on every answer), and `mlx_lm` loads causal language models. SPEC §11's "on-device = Apple MLX" could not have been built as written, so `--backend mlx` refuses with that explanation instead of serving a stand-in and calling it a checkpoint.

The other half of that decision was a latency bet — that a local decision needs a quantised MLX build to be affordable. Measured on the runtime the checkpoint actually has (torch CPU, 4 threads): the shipped `choice` shape costs 123 ms p50, 133 ms p95, 139 ms worst observed against a shipped budget of 350 ms. It fits, so MLX would have been an optimisation of something already fast enough, not a prerequisite.

Shapes whose p95 exceeds the budget: `keep_drop`, `noul`. `--shape choice` is the shipped default because it is the only one-pass shape; `keep_drop` trades 7x the cost for a materially better read under the same threshold rule (f1 0.704 vs 0.200), which is the trade a slot owner has to make deliberately, per model, and which is why it is a flag and not a default.

*Ground truth is author-labelled synthetic data: this curve measures the cost of the shipped question shapes and the pipeline that reads them, not any model's real-world accuracy (I6).*
