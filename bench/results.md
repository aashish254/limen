# subproto benchmark (projection)

Corpus: `mock — 24 deterministic synthetic requests` — 30 recorded requests, 359,259 est input tokens total.

| slot | decisions | dropped units | est tokens saved | % of input | tokens/req |
|---|--:|--:|--:|--:|--:|
| tool_gate | 30 | 330 | 101,562 | 28.3% | 3,385 |
| compact | 30 | 6 | 14,821 | 4.1% | 494 |
| context | 0 | 0 | 0 | 0.0% | 0 |
| effort | 30 | 0 | 0 | 0.0% | 0 |

**Projected input-token reduction if all slots enforce: 32.4%** (heuristic stand-ins; the Laya fine-tune is expected to raise precision, not change this ceiling.)

*These are projections from measured prompt shapes, not billed savings. See `bench/live.py` (`live_results.md`) for the delivered, pass-rate–held numbers measured against the mock.*
