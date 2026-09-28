# Wiring subproto into each agent

subproto is a **local, transparent proxy**. You never change your editor, your
provider account, or your code — you point one environment variable (the agent's
`base_url`) at `http://127.0.0.1:8787` and start the proxy.

```bash
subproto up                 # starts the listener on 127.0.0.1:8787 (config: --port)
```

Observation is the default: every decision is *recorded*, none are *enforced*.
Turn a slot on explicitly when you trust its numbers (invariant I1):

```bash
SUBPROTO_APPLY=tool_gate,compact subproto up
```

Keep your real API key in the environment (or pass it through); the proxy forwards
it to the vendor upstream untouched. Nothing is uploaded anywhere (invariant I4).

## Endpoints subproto serves

| Dialect | Path | Vendor upstream |
|---|---|---|
| Anthropic | `/v1/messages` | `https://api.anthropic.com` |
| OpenAI chat | `/v1/chat/completions`, `/v1/completions` | `https://api.openai.com` |
| OpenAI responses | `/v1/responses` | `https://api.openai.com` |
| Gemini | `/v1beta/models/<model>:generateContent` | `https://generativelanguage.googleapis.com` |
| Gemini stream | `/v1beta/models/<model>:streamGenerateContent` | `https://generativelanguage.googleapis.com` |

Override an upstream with `SUBPROTO_ANTHROPIC_UPSTREAM`, `SUBPROTO_OPENAI_UPSTREAM`,
or `SUBPROTO_GEMINI_UPSTREAM` (handy for pointing a dialect at the test mock).

An override is the **origin only** — no `/v1`, no trailing slash. The proxy forwards to
`<override> + the path the agent asked for`, so `http://127.0.0.1:11434/v1` sends
`/v1/chat/completions` to `/v1/v1/chat/completions` and the upstream answers 404. This
is the opposite of the client-side variables below, which do take the `/v1` because the
client is the one appending the path. To run a dialect against a local Ollama:

```bash
SUBPROTO_OPENAI_UPSTREAM=http://127.0.0.1:11434 subproto up
```

## Per-tool wiring

### Claude Code
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
export ANTHROPIC_API_KEY="sk-ant-…"      # your real key; passed through
claude
```

### Codex (OpenAI CLI)
Codex speaks the Responses API; `/v1/responses` and chat are both relayed.
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8787/v1"
export OPENAI_API_KEY="sk-…"
codex
```

### Gemini CLI
```bash
export GOOGLE_API_KEY="AIza…"            # / x-goog-api-key is injected if absent
export GOOGLE_GEMINI_BASE_URL="http://127.0.0.1:8787/v1beta"
gemini
```

### Cline (VS Code)
Settings → API Provider → *OpenAI Compatible*:
- Base URL: `http://127.0.0.1:8787/v1`
- API Key: your real key (Anthropic models route via the OpenAI-compatible shim;
  for native Anthropic use `http://127.0.0.1:8787` as the base and the Claude
  Code path above).

### aider
```bash
export OPENAI_API_BASE="http://127.0.0.1:8787/v1"
aider --model gpt-5            # or --anthropic-api-key + ANTHROPIC_BASE_URL
```

### OpenCode
Global config is `~/.config/opencode/opencode.jsonc`, and the key is **`provider`**
(singular) — `~/.opencode/config.json` with `providers` is not read by OpenCode, so
editing it silently does nothing.

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "limen": {
      "name": "Limen",
      "npm": "@ai-sdk/anthropic",
      "options": { "baseURL": "http://127.0.0.1:8787" },
      "models": { "claude-sonnet-4-5": { "name": "sonnet via limen" } }
    },
    "limen-openai": {
      "name": "Limen (OpenAI)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:8787/v1" },
      "models": { "gpt-5": { "name": "gpt-5 via limen" } }
    }
  }
}
```

Pick the model in `opencode` (`/models`) after starting the proxy. The key is
supplied through `opencode auth login`, and subproto forwards it untouched.

Use a model name the vendor really serves: subproto relays to `api.anthropic.com`
or `api.openai.com` for that dialect, so a name that only exists on some other
inference host will be rejected upstream.

### Antigravity (Gemini-native)
Point the model endpoint at the Gemini base above:
```bash
export GOOGLE_GEMINI_BASE_URL="http://127.0.0.1:8787/v1beta"
export GEMINI_API_KEY="AIza…"
```

## Verify it is live
```bash
curl -s http://127.0.0.1:8787/healthz | python3 -m json.tool
```
`subproto report` then shows where your input tokens came from and the per-slot
headroom, from real traffic.
