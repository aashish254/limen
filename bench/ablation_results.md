# subproto slot ablation — tool_gate (labelled synthetic)

Cases: 10 · agreement(heuristic vs laya): 100%

| backend | precision | recall | f1 |
|---|--:|--:|--:|
| heuristic | 0.972 | 0.986 | 0.979 |
| laya | 0.972 | 0.986 | 0.979 |

laya − heuristic precision delta: **+0.000**

*Note: `laya` here is the deterministic lexical scorer behind the adapter interface, so it shares the heuristic's core-tool logic and the two agree closely on this lexical ground truth — that agreement is expected, not a claim of parity with a real model. The delta becomes meaningful when the quantized MLX checkpoint (T16/M4) replaces the lexical scorer; this harness is the machinery that will report it. Ground truth is author-labelled and replaced by the `subproto label` corpus at M4 (SPEC §1.2).*
