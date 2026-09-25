# subproto benchmark (projection)

Corpus: `/var/folders/jn/pnnpl_cn0gx93p8v1yf8jb080000gn/T/subproto-bench-8wuhckzq` — 25 recorded requests, 299,365 est input tokens total.

| slot | decisions | dropped units | est tokens saved | % of input | tokens/req |
|---|--:|--:|--:|--:|--:|
| tool_gate | 25 | 275 | 84,635 | 28.3% | 3,385 |
| compact | 25 | 5 | 12,351 | 4.1% | 494 |
| context | 0 | 0 | 0 | 0.0% | 0 |
| effort | 25 | 0 | 0 | 0.0% | 0 |

**Projected input-token reduction if all slots enforce: 32.4%** (heuristic stand-ins; the Laya fine-tune is expected to raise precision, not change this ceiling.)

*These are projections from measured prompt shapes, not billed savings. The live pass-rate–held harness is on the roadmap.*
