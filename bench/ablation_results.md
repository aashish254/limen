# subproto slot ablation — tool_gate (labelled synthetic)

Cases: 10 · agreement with the heuristic, per adapter:

- `laya`: 100%

| backend | precision | recall | f1 | Δprecision vs heuristic |
|---|--:|--:|--:|--:|
| heuristic | 0.972 | 0.986 | 0.979 | — |
| laya | 0.972 | 0.986 | 0.979 | +0.000 |

laya − heuristic precision delta: **+0.000**

*Note: every adapter column is a real `POST /score` through `subproto.systemone.HTTPScoreAdapter`. Today the only endpoint in the repo is the bundled deterministic lexical stand-in (`python -m subproto.laya_server`), which shares the heuristic's core-tool logic, so close agreement and a ~zero delta are the expected reading — that is a property of the stand-in, not a claim about any real model. Point a registry label at a quantized checkpoint (T16/M4) and this table measures it unchanged. Ground truth is author-labelled and replaced by the `subproto label` corpus at M4 (SPEC §1.2).*
