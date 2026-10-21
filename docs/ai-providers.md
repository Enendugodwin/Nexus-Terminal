# Optional AI providers

AI is off by default. Everything deterministic - inventory, SSH/SFTP/serial,
diagnostics, suggestions, history, rules, playbooks and audit - works without a
model or a network connection.

## Enabling a provider

Open **Settings > Optional AI**, choose a provider and press **Save settings**.
Hosted providers need an API key; the key is written to the Windows Credential
Manager under the service prefix `nexus-remote`, never to SQLite or logs. The
field is write-only: leave it blank to keep the stored key, or tick **Remove the
stored key** to delete it.

| Provider | Default endpoint | Key |
| --- | --- | --- |
| Ollama | `http://127.0.0.1:11434/api/generate` | not required |
| OpenCode Go / Go Plus | `https://opencode.ai/inference/go/openai/v1/chat/completions` | required (your Go key) |
| OpenAI (GPT / Codex models) | `https://api.openai.com/v1/chat/completions` | required |
| Anthropic (Claude) | `https://api.anthropic.com/v1/messages` | required |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | required |
| OpenAI-compatible | `http://127.0.0.1:1234/v1/chat/completions` | optional |

### OpenCode Go versus OpenCode Console

They are two different products with two different balances and two different
paths:

- **Go / Go Plus** (monthly subscription):
  `https://opencode.ai/inference/go/openai/v1/chat/completions`
- **Console** (pay-as-you-go credits):
  `https://opencode.ai/inference/openai/v1/chat/completions`

A Go key used against the Console path answers
`HTTP 402: Upstream request failed: Insufficient account funds`, even when the Go
subscription is healthy - that balance is empty. Use the **OpenCode Go** preset
above; the app adds the `x-opencode-session` header that Go asks for. Free
models (`*-free`) are reserved for the OpenCode app itself and answer
`FreeTierError` to every other client.

The OpenAI-compatible adapter works with OpenChat servers, OpenRouter, LM Studio,
vLLM, LocalAI and other services that implement
`POST /v1/chat/completions`. Set the **full** endpoint URL, not just the host.
For Gemini, `{model}` in the endpoint is replaced with the model field.

**Test provider** sends one minimal completion request - the same route the
assistant uses - without saving anything. It asks the provider the question the
assistant would ask, because some plans answer a model list but refuse
completions. A failed test leaves the current settings untouched and shows the
provider's own explanation. For hosted requests that can take a few seconds;
the app reports the result when the provider answers.

Every request identifies itself honestly as `Nexus-Remote/<version>`; the
default `Python-urllib` agent is rejected by some bot-protection layers with
"Error 1010: browser signature banned".

## Endpoint rules

- `https://` is accepted anywhere.
- `http://` is accepted for loopback (`127.0.0.1`, `::1`, `localhost`) and for
  local servers without a key.
- A request that carries an API key is refused over plain HTTP unless the
  endpoint is loopback. This prevents a key being sent in clear text across a
  LAN or the internet.
- Responses are capped at 2 MB and requests time out (150 seconds by default;
  provider tests use at most 15 seconds).

## In the assistant

The **Optional AI** tab beside the terminal has a question box, an optional
context box (for error text or a banner), and the answer.

- Only what you type is sent, plus - when **Include the open terminal as context**
  is ticked (the default) - a small bounded snapshot of the attached console:
  shell/edition, working directory or device, and the last few output lines
  (20 lines / 1500 characters at most). The tab says what was attached after
  each question, and unticking the box sends only what you typed.
- Context is fenced as `UNTRUSTED_REMOTE_OUTPUT`; the prompt tells the model to
  treat it as data, not instructions.
- Answers are labelled *AI-generated ... unverified* and are never executed.
- **Select part of the answer** to send exactly that text: highlight it and press
  **Send selection** (or **Insert into shell** with a highlight). A fenced code
  block is preferred when nothing is highlighted, so the guess is no longer just
  "the first command-looking line".
- **Insert into shell** pastes text into the open shell without pressing Enter.
  A single line is always safe; a multi-line block is inserted as one bracketed
  paste so the shell waits for one Enter. If the shell does not support
  bracketed paste, the text goes to the clipboard instead of being typed line by
  line. The remote policy gate still runs when you press Enter yourself; local
  PowerShell commands are your own responsibility.
- Requests are audited (`ai.request`) with provider, model, character count and
  outcome - never the prompt or answer text.

Model output is not a security control. It cannot change policy, approve a
command, read credentials or call tools.

## Keys and privacy

- API keys are stored in the OS vault; the database stores only settings.
- A provider may log prompts and answers. Treat it as a third party: do not send
  credentials, customer data or anything you are not allowed to share.
- If a workstation is shared or lost, remove the key in **Settings > Optional
  AI** (or Windows Credential Manager) and rotate it at the provider.
- There is no telemetry, background prompt upload or silent provider fallback.
  Selecting **Disabled** restores the fully offline assistant immediately.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| "No AI provider is ready" | Choose a provider, enter its key and save. |
| HTTP 401 / 403 | Read the reason the app shows. A key can be valid but the plan can forbid third-party clients: OpenCode's free models, for example, only answer requests made from inside the OpenCode app and reject every other client with `FreeTierError`. |
| HTTP 402 "Insufficient account funds" with an OpenCode key | You are on the OpenCode **Console** path with an empty pay-as-you-go balance. If you pay for **Go**, switch to the **OpenCode Go** preset (the `/inference/go/...` endpoint); the two balances are separate. |
| HTTP 429 "Endpoint is unavailable" | The gateway was briefly busy; retry. Persistent 429 means the Go allowance or rate limit is exhausted. |
| "spent the whole N-token budget on reasoning" | The model thought until the token limit and never wrote an answer. Ask a narrower question or pick a model that answers directly (`deepseek-v4.1-flash`, `kimi-k3`). The app already starts at 8000 tokens for Go. |
| "the answer was cut off" | The reply reached the token limit. Ask for a shorter or narrower answer; the visible part is still usable. |
| A slow/verbose model times out | Reasoning-heavy models can take minutes on broad questions. Use `deepseek-v4.1-flash` for routine help. |
| HTTP 404 | The endpoint path or model name is wrong. `GET https://opencode.ai/inference/go/v1/models` lists the model ids a Go key may use. |
| "refusing to send an API key over plain HTTP" | Use HTTPS or a loopback endpoint. |
| Ollama "not reachable" | Is Ollama running, and is the host/port correct? |
| The provider answers in a browser but not here | Some gateways pin the browser signature or the plan to one client. The app sends a normal JSON request; if the provider refuses it, use another endpoint. |
| Model answer contains prose | **Insert into shell** only accepts command-looking lines; copy and edit instead. |
