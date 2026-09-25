# subproto benchmark (projection)

Corpus: `mock — 25 deterministic synthetic requests` — 31 recorded requests, 372,989 est input tokens total.

| slot | decisions | dropped units | est tokens saved | % of input | tokens/req |
|---|--:|--:|--:|--:|--:|
| tool_gate | 31 | 342 | 105,713 | 28.3% | 3,410 |
| compact | 31 | 6 | 14,821 | 4.0% | 478 |
| context | 0 | 0 | 0 | 0.0% | 0 |
| effort | 31 | 0 | 0 | 0.0% | 0 |

**Projected input-token reduction if all slots enforce: 32.3%** (heuristic stand-ins; the Laya fine-tune is expected to raise precision, not change this ceiling.)

*These are projections from measured prompt shapes, not billed savings. See `bench/live.py` (`live_results.md`) for the delivered, pass-rate–held numbers measured against the mock.*
