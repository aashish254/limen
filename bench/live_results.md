# subproto live harness (mock, pass-rate held)

Tasks: `tasks.sample.jsonl` · n=20 · bootstrap CI · mock upstream, $0, no key.

| metric | observe | enforce | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 19746 | 13822 | **29.9%** | [28.2, 31.3] |
| p50 request latency (ms) | 6.3 | 6.1 | 2.9% | [1.4, 4.2] |
| task pass-rate | 100.0% | 100.0% | **+0.0 pp** | [+0.0, +0.0] |

**Correctness floor (I3), per-slot arm: pass-rate delta 0.0 pp — HELD (gate: >= -1.0pp).**
**Correctness floor (I3), compiled arm: 100.0% vs 100.0% observed — HELD.** The exit code follows the arm that would ship.

## v5 S23: one joint budget vs three per-slot budgets

Same three slots, same code graph, same tasks. `compiled` spends one
budget over messages + tool specs + file notes; `enforce` spends one per slot.

| metric | per-slot | compiled | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 13822 | 11133 | **19.3%** | [18.0, 20.9] |
| p50 request latency (ms) | 6.1 | 6.3 | — | [-5.3, -2.6] |
| task pass-rate | 100.0% | 100.0% | — | [+0.0, +0.0] pp |
| **recall of required files** | 100.0% | 100.0% | +0.0 pp | [+0.0, +0.0] pp |
| **precision of what survived** | 2.6% | 19.6% | +17.0 pp | [+2.8, +35.8] pp |

**Verdict (S23 gate: recall holds at 100% *and* precision improves): BETTER** — recall held at 100% and precision improved by 17.0 pp

Latency here is a request-shape proxy against the local mock, not a real time-to-first-token; it is reported for completeness. The token reduction and held pass-rate are the mock-measurable signals. Against a real SWE-bench-style suite + grader these become the hero number.
