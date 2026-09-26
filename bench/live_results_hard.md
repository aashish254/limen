# subproto live harness (mock, pass-rate held)

Tasks: `tasks.hard.jsonl` · n=8 · bootstrap CI · mock upstream, $0, no key.

| metric | observe | enforce | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 40272 | 24979 | **38.0%** | [36.6, 39.4] |
| p50 request latency (ms) | 13.1 | 12.8 | 2.6% | [0.9, 4.3] |
| task pass-rate | 100.0% | 0.0% | **-100.0 pp** | [-100.0, -100.0] |

**Correctness floor (I3), per-slot arm: pass-rate delta -100.0 pp — VIOLATED (gate: >= -1.0pp).**
**Correctness floor (I3), compiled arm: 100.0% vs 100.0% observed — HELD.** The exit code follows the arm that would ship.

## v5 S23: one joint budget vs three per-slot budgets

Same three slots, same code graph, same tasks. `compiled` spends one
budget over messages + tool specs + file notes; `enforce` spends one per slot.

| metric | per-slot | compiled | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 24979 | 20405 | **18.2%** | [16.2, 20.2] |
| p50 request latency (ms) | 12.8 | 16.3 | — | [-27.4, -24.6] |
| task pass-rate | 0.0% | 100.0% | — | [+100.0, +100.0] pp |
| **recall of required files** | 2.7% | 100.0% | +97.3 pp | [+97.3, +97.4] pp |
| **precision of what survived** | 0.5% | 25.1% | +24.6 pp | [+24.6, +24.7] pp |

**Verdict (S23 gate: recall holds at 100% *and* precision improves): BETTER** — recall held at 100% and precision improved by 24.6 pp

Latency here is a request-shape proxy against the local mock, not a real time-to-first-token; it is reported for completeness. The token reduction and held pass-rate are the mock-measurable signals. Against a real SWE-bench-style suite + grader these become the hero number.
