# Concepts

Use this page when you want to understand mason before changing advanced settings. It explains the moving parts without requiring you to read the source first.

If you want source-file ownership and extension points, read [`architecture.md`](./architecture.md) after this page.

## Runtime Shape

mason has one small core loop and several ways to enter it:

| Part | What it does |
|---|---|
| Agent loop | Builds context, selects the session, calls the provider, runs tools, and publishes replies |
| Providers | LLM backends such as OpenRouter, Anthropic, OpenAI, Bedrock, Ollama, vLLM, and other OpenAI-compatible APIs |
| Channels | User-facing transports such as CLI, Telegram, Discord, Slack, Feishu, WeChat, Email, Mattermost, and others |
| Tools | Capabilities the model may call, including files, shell, web search/fetch, MCP, cron, image generation, and subagents |
| Memory | Session state, SQLite structured memory, the permanent verbatim journal, and the dated-events file |
| Gateway | Long-running process that connects enabled channels and serves the health endpoint |

The simplest path is `mason agent -m "Hello!"`: one inbound message goes through the agent loop and prints the reply in your terminal. The long-running path is `mason gateway`: channels receive messages from chat apps, publish them to the same agent loop, and send replies back to the originating channel.

## Config vs Workspace

The default instance lives under `~/.mason/`:

| Path | Meaning |
|---|---|
| `~/.mason/config.json` | Instance configuration: providers, model defaults, channels, tools, gateway, API, and runtime options |
| `~/.mason/workspace/` | Agent workspace: memory, heartbeat tasks, cron jobs, skills, and generated artifacts |
| `~/.mason/sessions/<workspace-id>/` | Session history stored outside the agent-accessible workspace; the opaque ID follows workspace moves |

You can override both with command flags:

```bash
mason onboard --config ./bot-a/config.json --workspace ./bot-a/workspace
mason agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
mason gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
```

The config file controls what mason may use. The workspace is where mason keeps state for that instance.

### Agent Workspace and Project Workspace

The configured workspace is the **agent workspace**. A session can also carry
a different **project workspace** for repository-specific work without moving the
agent's identity or durable state.

| Resource | Owner when a project is selected |
|---|---|
| Project instructions | `AGENTS.md` from the selected project; there is no fallback to the agent workspace's `AGENTS.md` |
| Agent profile | `SOUL.md` and `USER.md` from the agent workspace; project-local files with those names are ignored |
| Memory and custom skills | `memory/` and `skills/` from the agent workspace |
| Relative file paths and shell working directory | The selected project workspace |

When no separate project is selected, one directory normally serves both roles.
Selecting a project changes the working context for that chat; it does not create
a second agent or relocate the configured agent workspace.

## Config Format

`config.json` accepts both camelCase and snake_case keys. The docs use camelCase because mason writes config back to disk with camelCase aliases, for example `apiKey`, `modelPresets`, `intervalS`, and `maxToolResultChars`.

Most examples are partial snippets. Merge them into the existing file created by `mason onboard`; do not replace the whole file unless you want to reset the instance.

## One Agent Turn

A normal turn follows this flow:

1. A channel receives a user message and publishes it to the message bus.
2. The agent loop chooses a session key and builds context from the system prompt (identity files and the session state), agent-owned skills and memory, channel metadata, and runtime settings. Stored conversation history is only replayed to the model when `sessionReplay` enables it.
3. The provider receives the model request.
4. If the model asks for tools, the runner executes them and feeds results back to the model.
5. The final reply is saved to the session and sent back through the channel.

That flow is the same whether the message starts in the CLI, Telegram, Discord, or another channel.

## CLI, Gateway, and API

| Entry point | Command | Use it for |
|---|---|---|
| CLI one-shot | `mason agent -m "..."` | First-run checks, scripts, and quick local questions |
| CLI interactive | `mason agent` | Terminal chat; stored history replays only when `sessionReplay` is enabled |
| Gateway | `mason gateway` | Chat apps, heartbeat, memory consolidation, and long-running service mode |
| OpenAI-compatible API | `mason serve` | Programmatic access through `/v1/chat/completions` |

The gateway keeps enabled channels and other long-running services alive. Its health endpoint is on `gateway.port` (`18790` by default).

## Provider and Model Selection

The active model is selected by `agents.defaults.model` and `agents.defaults.provider`; in chat, `/model <provider> <model>` overrides that for the current session. Generation settings are separate: `agents.defaults.modelPreset` (or `/presets <name>`) applies one of the top-level `modelPresets`, and a preset never changes the model. The active provider is resolved in this order:

1. If the explicit provider is not `"auto"`, mason uses that provider.
2. If provider is `"auto"`, mason tries to infer the provider from the model name, configured API keys, local provider base URLs, or gateway providers.
3. OAuth providers such as OpenAI Codex and GitHub Copilot require explicit login and explicit provider/model selection.

Pin the provider in `agents.defaults` when setting up for the first time. It is easier to debug:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4.5",
      "provider": "openrouter"
    }
  }
}
```

See [`providers.md`](./providers.md) for practical examples and [`configuration.md#providers`](./configuration.md#providers) for the full provider reference.

## Channels and Sessions

Each channel maps inbound messages to a session key. That lets independent conversations keep separate history.

`agents.defaults.unifiedSession` can intentionally share one session across channels for a single-user multi-device setup. Leave it off if you expect separate people, groups, channels, or projects to keep separate context.

## Memory and Sessions

mason separates short-term replay from longer-lived memory:

| Store | Location | Purpose |
|---|---|---|
| Sessions | `<config-dir>/sessions/<workspace-id>/*.jsonl` | Conversation transcripts; replayed to the model only when `sessionReplay` is `tail` or `full` |
| Session state | `<workspace>/memory/state/<session>.md` | Framework-maintained four-section summary (4 KB default) kept in the system prompt; persists until `/state clear` |
| Structured memory | `<workspace>/memory/memory.db` | Facts, preferences, decisions, summaries, relationships, and open gaps, searched with `recall_memory` |
| Verbatim journal | `<workspace>/memory/memory.db` | Every captured turn, kept indefinitely, searched with `recall_journal` |
| Recovery backup | `<workspace>/backups/memory/*.db.gz` | Compressed full copies of the database written daily; not a memory surface the model reads |

Consolidation is a periodic LLM pass that derives structured memory from the permanent verbatim journal. It promotes worthwhile entries, deduplicates, resolves or flags contradictions, links related memories, synthesises broader memories, assesses retention, records knowledge gaps, and writes small generated skills under `skills/`. The model never writes structured memory directly; it checks `recall_memory`/`recall_journal` before claiming it does not remember something. The framework refreshes each session's state after every turn, and the daily rollover writes a compressed full backup and runs SQLite optimisation without clearing session state. The journal is never purged automatically.

Memory is SQLite-backed (`memory/memory.db`) and does not support Windows. When upgrading a file-based workspace, a customized `memory/MEMORY.md` and the existing `memory/history.jsonl` are imported once and moved to `memory/legacy/`. From then on, `memory/history.jsonl` holds the archived conversation summaries written by session compaction.

The configured workspace contains a `.mason/workspace-id` file. It contains only an
opaque random identifier—never conversation content or credentials. Keep it with workspace
backups: it lets mason find the same external session namespace after the workspace is
renamed, moved, or restored. A live copy opened alongside the original receives a new ID so
the two workspaces do not share conversations accidentally.

See [`memory.md`](./memory.md) for the detailed design.

## Apps and Agent Plugins

Agent Plugins are mason's common package and activation boundary for
installable capabilities. They organize existing extension types instead of
replacing them:

| Part | Role |
|---|---|
| Agent Plugin | Installable package that can bundle skills, MCP servers, or both |
| Skill | Workflow guidance loaded progressively or invoked with `$skill-name` |
| MCP server | Runtime tools exposed to the agent |
| CLI App | Locally managed executable whose adapter is packaged and activated like a plugin |

Native providers, channels, built-in tools, standalone workspace skills, and
directly configured MCP servers keep their existing extension paths. See
[`configuration.md#agent-plugins-v1`](./configuration.md#agent-plugins-v1) for
the package contract.

## Tools and Safety

Tools are discovered automatically from built-in modules and plugin entry points. Common tool groups include:

- file read/write/edit and patching;
- shell execution with configurable sandboxing;
- web search and web fetch with SSRF checks;
- MCP servers;
- cron reminders, local triggers, and heartbeat tasks;
- image generation;
- subagents and runtime self-inspection.

Tool schemas are loaded lazily by default. Only `find_capabilities`, `recall_memory`, and `recall_journal` are visible up front; the model loads every other tool or skill on demand by calling `find_capabilities` with a plain-language need. That includes `update_state` and `read_state`. The names-only capability catalog lives in that tool's description instead of the system prompt.

Security-sensitive controls live in [`configuration.md#security`](./configuration.md#security). For production or shared chat apps, also configure channel access controls such as `allowFrom` or pairing.

## Background Jobs

When `mason gateway` starts, it runs workspace-scoped automations and
registers system jobs:

- `consolidation`, when `agents.defaults.memory.enabled` and `agents.defaults.memory.consolidation.enabled` are true;
- `memory_rollover`, daily at 00:00, when `agents.defaults.memory.enabled` is true;
- `heartbeat`, when `gateway.heartbeat.enabled` is true.

Heartbeat reads `<workspace>/HEARTBEAT.md`. If the file has tasks under `## Active Tasks`, mason executes them and sends only useful/actionable results to the most recently active chat target. Routine "nothing changed" results are suppressed.

User-created reminders use the same cron service but are not the same as the
protected heartbeat system job. They run as scheduled turns in their origin
chat/session and normally deliver the result back to that channel.

Local triggers are also session-bound, but they do not have their own
schedule. Create one from the target chat with `/trigger <name>`, then call
`mason trigger <id> "<message>"` when a local script or external service wants
mason to respond in that session. Webhook servers, third-party auth, and
event-to-message formatting stay outside mason. Trigger deliveries are stored
in the workspace until the linked agent turn finishes successfully. If the
target session is busy, the trigger waits until that session is idle instead of
being injected into the active turn. The message is recorded as an automation
turn in that session. Delivery is at-least-once, so external systems should
tolerate repeated trigger messages; a delivery that reaches the agent but fails
is marked failed rather than retried forever.

## Where to Go Next

| Need | Read |
|---|---|
| First working install | [`quick-start.md`](./quick-start.md) |
| Provider/model setup | [`providers.md`](./providers.md) |
| Chat app setup | [`chat-apps.md`](./chat-apps.md) |
| Complete config reference | [`configuration.md`](./configuration.md) |
| Runtime debugging | [`troubleshooting.md`](./troubleshooting.md) |
