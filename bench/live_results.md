# subproto live harness (mock, pass-rate held)

Tasks: `tasks.sample.jsonl` · n=20 · bootstrap CI · mock upstream, $0, no key.

| metric | observe | enforce | delta | 95% CI |
|---|--:|--:|--:|:--|
| input tokens (mean/task) | 19704 | 13728 | **30.2%** | [28.5, 31.7] |
| p50 request latency (ms) | 12.6 | 12.4 | 0.8% | [-3.9, 4.7] |
| task pass-rate | 100.0% | 100.0% | **+0.0 pp** | [+0.0, +0.0] |

**Correctness floor (I3): pass-rate delta 0.0 pp — HELD (gate: >= -1.0pp).**

Latency here is a request-shape proxy against the local mock, not a real time-to-first-token; it is reported for completeness. The token reduction and held pass-rate are the mock-measurable signals. Against a real SWE-bench-style suite + grader these become the hero number.
