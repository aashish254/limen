# subproto live harness (mock, pass-rate held)

Tasks: `tasks.sample.jsonl` · n=8 · bootstrap CI · mock upstream, $0, no key.

| metric | observe | enforce | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 19920 | 14306 | **27.9%** | [24.8, 30.9] |
| p50 request latency (ms) | 6.3 | 6.2 | 6.9% | [-3.3, 18.4] |
| task pass-rate | 100.0% | 100.0% | **+0.0 pp** | [+0.0, +0.0] |

**Correctness floor (I3): pass-rate delta 0.0 pp — HELD (gate: >= -1.0pp).**

Latency here is a request-shape proxy against the local mock, not a real time-to-first-token; it is reported for completeness. The token reduction and held pass-rate are the mock-measurable signals. Against a real SWE-bench-style suite + grader these become the hero number.
