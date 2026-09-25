# subproto benchmark (projection)

Corpus: `/var/folders/jn/pnnpl_cn0gx93p8v1yf8jb080000gn/T/subproto-bench-gf3jgbid` — 37 recorded requests, 446,665 est input tokens total.

| slot | decisions | dropped units | est tokens saved | % of input | tokens/req |
|---|--:|--:|--:|--:|--:|
| tool_gate | 37 | 409 | 126,791 | 28.4% | 3,426 |
| compact | 37 | 7 | 17,292 | 3.9% | 467 |
| context | 0 | 0 | 0 | 0.0% | 0 |
| effort | 37 | 0 | 0 | 0.0% | 0 |

**Projected input-token reduction if all slots enforce: 32.3%** (heuristic stand-ins; the Laya fine-tune is expected to raise precision, not change this ceiling.)

*These are projections from measured prompt shapes, not billed savings. The live pass-rate–held harness is on the roadmap.*
