## What rule does this change?

One or two sentences. Name the decision the code makes, not the file you edited.

## How do I see it fail without this change?

```
the command you ran
```

Paste the **real** output, including the line that shows the fix.

## Gate

- [ ] `bash run_tests.sh` prints `ALL GATES PASS` (paste the last 3 lines)
- [ ] New rule ⇒ new mutant in `bench/mutation_gate.py`, and it is reported `KILLED`
- [ ] Any printed page touched ⇒ `subproto/tests/test_style.py` still passes
- [ ] Any claim in README/SPEC/ROADMAP changed ⇒ the number came from a command above

## Invariants

Which of I2 (cached prefix), I3 (tail is sacred), I4 (local only), I5 (byte
transparent), I6 (measured not claimed) does this touch, and what keeps it whole?

## Labels

Fixes #… — or say `no issue` for a drive-by.
