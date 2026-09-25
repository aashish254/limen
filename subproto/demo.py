"""Synthetic coding-agent traffic used by `subproto demo` and the tests.

The shape matters more than the content: a big system prompt that is *slightly*
different on every request, dozens of tool schemas, and multi-kilobyte tool
results replayed every turn. That is exactly the workload frontier agents
generate, and exactly where the tokens go.
"""

import json
import os
import random
import time
import urllib.request

from . import protocol

TOOLS = [
    ("Read", "Read a file from the local filesystem, returning numbered lines."),
    ("Write", "Write a file to the local filesystem, overwriting if it exists."),
    ("Edit", "Perform an exact string replacement inside a file."),
    ("Bash", "Run a shell command and capture stdout and stderr."),
    ("Grep", "Search file contents with a regular expression."),
    ("Glob", "Find files matching a glob pattern, newest first."),
    ("WebFetch", "Fetch a URL and answer a question about its contents."),
    ("WebSearch", "Search the web and return summarised results."),
    ("Task", "Launch a subagent to handle a multi-step task."),
    ("TodoWrite", "Create or update the structured task list for this session."),
    ("NotebookEdit", "Replace the contents of a Jupyter notebook cell."),
    ("KillShell", "Terminate a running background shell session."),
    ("BashOutput", "Read output from a background shell session."),
    ("ExitPlanMode", "Present the current plan for user approval."),
    ("SlashCommand", "Invoke a user-defined slash command."),
    ("SearchExtraTools", "Search for MCP tools that are not loaded into context."),
    ("mcp__browser__click", "Click an element in the browser by selector."),
    ("mcp__browser__screenshot", "Capture a screenshot of the current page."),
    ("mcp__browser__navigate", "Navigate the browser to a URL."),
    ("mcp__browser__fill", "Fill a form field with a value."),
    ("mcp__db__query", "Run a SQL query against the project database."),
    ("mcp__db__schema", "Print the schema of a database table."),
    ("mcp__figma__read", "Read a Figma node tree."),
    ("mcp__jira__issue", "Fetch a Jira issue by key."),
    ("mcp__jira__comment", "Post a comment on a Jira issue."),
    ("mcp__linear__create", "Create a Linear issue."),
    ("mcp__sentry__list", "List recent Sentry errors for a release."),
    ("mcp__slack__post", "Post a message to a Slack channel."),
    ("mcp__github__pr", "Open or update a pull request."),
    ("mcp__github__review", "Fetch review comments on a pull request."),
]

LOG_LINES = [
    "DEBUG 2026-09-25 10:04:11 retrying payment_intent id=pi_3Qr token=sk_live_redacted",
    "INFO  connecting to postgres://db-primary:5432/app pool=12",
    "WARN  slow query 1843ms SELECT * FROM orders WHERE state='pending' ORDER BY id",
    "Traceback (most recent call last):   File app/payments/retry.py, line 88, in refund",
    "    raise RetryExhausted(last_error) from err",
    "app.errors.RetryExhausted: stripe.APIConnectionError: connection reset by peer",
    "INFO  2026-09-25 10:04:12 request_id=8f21 status=500 duration=2144ms",
    "DEBUG serialising payload {'amount': 4210, 'currency': 'usd', 'idempotency_key': 'a1'}",
]


def system_prompt(i, jitter=True):
    base = (
        "You are Claude Code, a coding agent running in a terminal.\n"
        "# Environment\n- Primary working directory: /home/dev/checkout\n"
        "- Platform: darwin. Shell: /bin/zsh. Git repository: yes\n"
        "# Project instructions\n- Run tests before declaring work complete.\n"
        "- Do not add comments that restate the code.\n"
    )
    filler = "\n".join("- convention %03d: keep module boundaries explicit and narrow" % k
                       for k in range(140))
    stamp = ("\n# Session\n  started=%s turn=%d minute=%d"
             % (time.strftime("%Y-%m-%dT%H:%M"), i, (time.time() // 60) if jitter else 0))
    return base + filler + stamp + ("\n" + " " * (i % 7) if jitter else "")


def tool_specs(n=24, shuffle=True):
    picked = TOOLS[:n]
    if shuffle:
        picked = list(picked)
        random.shuffle(picked)
    return [
        {
            "name": name,
            "description": desc + " " + " ".join(
                "parameter %d accepts a JSON object with nested schema definitions;" % k
                for k in range(9)),
            "input_schema": {
                "type": "object",
                "properties": dict((
                    "p%d" % k,
                    {"type": "string", "description": "field %d of the %s input schema" % (k, name)},
                ) for k in range(6)),
                "required": ["p0"],
            },
        }
        for name, desc in picked
    ]


def tool_result_blob(i):
    body = []
    for k in range(20):
        n = random.Random(i * 97 + k).randrange(len(LOG_LINES))
        body.append(LOG_LINES[n])
    return "\n".join(body)


def messages_for(i, turns=6):
    msgs = [{
        "role": "user",
        "content": "Refactor the payment retry path in app/payments/retry.py so a "
                   "RetryExhausted from stripe.APIConnectionError is caught, backed off "
                   "with jitter, and surfaced in app/payments/errors.py. The retry policy "
                   "must stay configurable from app/config.py and the OpenAI-compatible "
                   "client in app/services/gateway_client.py has to keep working. Add tests "
                   "in tests/test_retry.py."
    }]
    for t in range(turns):
        msgs.append({
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "I should look at retry.py and the tests "
                    "before editing. " * (3 + t)},
                {"type": "tool_use", "id": "tu_%d_%d" % (i, t), "name": "Read",
                 "input": {"file_path": "app/payments/retry.py", "offset": t * 40}},
            ],
        })
        msgs.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_%d_%d" % (i, t),
                         "content": tool_result_blob(i * 10 + t)}],
        })
    return msgs


def synthetic_request(i, model="claude-sonnet-4-5", jitter=True, turns=6):
    return {
        "model": model,
        "max_tokens": 8192,
        "system": system_prompt(i, jitter=jitter),
        "tools": tool_specs(24, shuffle=(i % 3 == 0)),
        "messages": messages_for(i, turns=turns),
        "stream": True,
    }


def openai_synthetic(i, model="gpt-5-mini"):
    msgs = [{"role": "system", "content": system_prompt(i)}]
    msgs.append({"role": "user", "content": "Fix the retry loop in app/payments/retry.py "
                 "and keep gateway_client.py working."})
    for t in range(4):
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": "c%d" % t, "type": "function",
                                     "function": {"name": "Bash",
                                                  "arguments": json.dumps({"cmd": "pytest -q"})}}]})
        msgs.append({"role": "tool", "tool_call_id": "c%d" % t,
                     "content": tool_result_blob(i + t)})
    return {"model": model, "messages": msgs, "stream": True,
            "tools": [{"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": {}}}}
                for name, desc in TOOLS[:18]]}


def gemini_synthetic(i, model="gemini-2.5-flash"):
    contents = [{"role": "user", "parts": [{"text":
        "Fix the retry loop in app/payments/retry.py and keep gateway_client.py working."}]}]
    for t in range(4):
        contents.append({"role": "model", "parts": [{"text": "reading retry.py " * (3 + t)}]})
        contents.append({"role": "user", "parts": [{"text": tool_result_blob(i + t)}]})
    return {
        "systemInstruction": {"parts": [{"text": system_prompt(i)}]},
        "contents": contents,
        "tools": [{"functionDeclarations": [
            {"name": name, "description": desc,
             "parameters": {"type": "object", "properties": {}}
             # keep the first 14 so tool_gate has something to drop
             } for name, desc in TOOLS[:14]]}],
        }


def responses_synthetic(i, model="gpt-5"):
    return {
        "model": model,
        "instructions": system_prompt(i),
        "input": [{"role": "user", "content": [
            {"type": "input_text",
             "text": "Fix the retry loop in app/payments/retry.py."}]}],
        "tools": [{"type": "function", "name": name, "description": desc,
                   "parameters": {"type": "object", "properties": {}}}
                  for name, desc in TOOLS[:16]],
        "stream": True,
    }


def post(url, obj, headers=None, timeout=30):
    raw = protocol.dump_body(obj)
    h = {"content-type": "application/json", "x-api-key": "mock-key",
         "anthropic-version": "2023-06-01", "user-agent": "claude-cli/1.0 (synthetic)"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=raw, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()
        return r.status


def replay(config, mock_port=None, n=14, jitter=True, host="127.0.0.1"):
    """Run the mock upstream + push synthetic traffic through the proxy."""
    import fakeup.server as mock

    mock = mock.MockUpstream(port=mock_port or (config.port + 1)).start()
    src = dict(config.source)
    src["anthropic_upstream"] = "http://%s:%d" % (host, mock.port)
    src["openai_upstream"] = src["anthropic_upstream"]
    config.source = src
    sent = 0
    try:
        base = "http://%s:%d" % (host, config.port)
        for i in range(n):
            body = synthetic_request(i, jitter=jitter)
            if post(base + "/v1/messages", body) == 200:
                sent += 1
            if i % 4 == 3:
                post(base + "/v1/chat/completions", openai_synthetic(i),
                     headers={"Authorization": "Bearer mock",
                              "user-agent": "codex_cli_rs/0.1"})
                sent += 1
            time.sleep(0.004)
    finally:
        mock.stop()
    return sent
