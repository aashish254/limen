# Security

## What this is

A local, single-user proxy between a coding agent on your machine and the provider you
configured it for. It is not a multi-tenant service, has no accounts, no server side,
and no telemetry pipeline.

## The data-handling claims, stated plainly

- **Nothing leaves the machine except your own request.** subproto forwards to the
  base URL you configure and to no other host. There is no analytics beacon, no update
  check, no crash reporter, and no third-party SDK in the dependency list — the runtime
  has zero third-party dependencies. This is invariant I4, and it is tested.
- **Prompt bodies are not written to disk by default.** Telemetry stores request
  metadata, token counts and decisions. Storing the request body is opt-in
  (`--store-bodies`), because the body is your source code and your secrets.
- **The model backend is a local HTTP call.** `bench` and the optional Laya server bind
  to loopback. Pointing `SUBPROTO_MODEL` at a remote host is a config choice you make,
  and it is then a provider of your prompt content — treat it like one.
- **`subproto doctor` reports which key variables are set, never what they contain.** A
  diagnostic page is the one place a secret can leak into a bug report, so the row prints
  the variable name (`ANTHROPIC_API_KEY`), and the value, its prefix and its length are
  never computed into the page. This is pinned by a test that puts a canary in the
  environment and fails if any part of it appears in the output.
- **Telemetry lives in your home directory** (`~/.subproto/`, or `SUBPROTO_HOME`), in a
  SQLite file with no encryption at rest. It contains paths, tool names and token
  counts. Anyone with your filesystem can read it; treat it like shell history.

## What is out of scope

The proxy is designed to sit on localhost, in a single-user session. Do not deploy it
as a shared gateway: it has no authentication, no rate limiting and no request
isolation between users, and it would need all three.

## Known limits worth knowing before you rely on it

- Dropping context is a judgement call made by heuristics or a small model. Invariant
  I3 protects the recent tail and open errors, and enforcement is opt-in and
  reversible, but a wrong drop can still degrade an answer. The measured price of
  enforcing is printed by `subproto report` as *regrettable drops*; read it before you
  turn a slot on.
- The accuracy of the routing decisions is not yet measured on real traffic — that
  needs a labelled corpus, which is a human action rather than a code task. Every
  public number is labelled measured, projected or gated.

## Reporting a vulnerability

Open a **private** security advisory on the repository rather than a public issue, so
no working exploit is published before a fix ships. Expect a response within a week.
