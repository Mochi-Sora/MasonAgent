# CLI Reference

Use this page when you know what you want to run and need the command shape. For a guided first run, start with [`quick-start.md`](./quick-start.md).

## Choose a Command

| Goal | Command | Notes |
|---|---|---|
| Check the install | `mason --version` | If this fails, try `python -m mason --version` |
| Create or refresh config | `mason onboard` | Creates `~/.mason/config.json` and `~/.mason/workspace/` |
| Refresh config non-interactively | `mason onboard --refresh` | Preserves existing values and adds missing default fields without prompting |
| Use guided setup | `mason onboard --wizard` | Best when you prefer prompts over hand-editing JSON |
| Check readiness without calling a model | `mason status` | Summarizes config/workspace and validates the active provider/model configuration |
| Send one test message | `mason -m "Hello!"` | First proof that install, config, provider, model, and workspace all work |
| Chat in the terminal | `mason` | Interactive local chat; `mason agent` remains an explicit alias |
| Run the gateway directly | `mason gateway` | Service/ops command for chat apps, cron, and heartbeat |
| Deliver a local trigger | `mason trigger <id> "message"` | Created first with `/trigger <name>` in the target chat/session |
| Serve an OpenAI-compatible API | `mason serve` | Starts `/v1/chat/completions`, `/v1/models`, and `/health` |
| Check chat channel setup | `mason channels status` | Useful before starting `mason gateway` |
| Manage optional features | `mason plugins list` | Shows channels and optional capabilities you can turn on |
| Log in to QR/OAuth-style channels | `mason channels login <channel>` | Used by channels such as WhatsApp and WeChat |
| Complete a browser authorization for a channel | `mason channels connect <channel>` | Used by Linear |
| Authorize an OAuth MCP server | `mason mcp login <server>` | Stores tokens outside `config.json` |
| Log in to OAuth model providers | `mason provider login <provider>` | Used by OpenAI Codex, xAI subscription, and GitHub Copilot providers |

## Global

```bash
mason --help
mason --version
python -m mason --help
python -m mason --version
```

`python -m mason ...` is useful when the package is installed but the `mason` script is not on `PATH`.

## Common Patterns

Most day-to-day commands use the default config and workspace. Advanced or multi-instance runs usually pass both paths explicitly:

```bash
mason agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
mason gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
mason serve --config ./bot-a/config.json --workspace ./bot-a/workspace
```

Use `--verbose` on long-running processes when you need startup or runtime logs:

```bash
mason gateway --verbose
mason serve --verbose
```

Long-running commands keep working until you stop them. Press `Ctrl+C` in that terminal
to stop foreground `mason gateway` or `mason serve`. If you started the gateway
with `--background`, use `mason gateway stop`.

## Setup

| Command | Description |
|---|---|
| `mason onboard` | Initialize or refresh the default config and workspace |
| `mason onboard --refresh` | Refresh an existing config without prompting, preserving existing values |
| `mason onboard --wizard` | Use the interactive setup wizard |
| `mason onboard --config <path> --workspace <path>` | Initialize or refresh a specific instance |

Default paths:

| Path | Default |
|---|---|
| Config | `~/.mason/config.json` |
| Workspace | `~/.mason/workspace/` |

## Status

| Command | Description |
|---|---|
| `mason status` | Summarize the default config/workspace and check Agent provider/model readiness |
| `mason status --config <path>` | Check a specific config file |
| `mason status --workspace <path>` | Show status with a workspace override |

Status does not send a model request. On success, run the printed
`mason agent -m "Hello!"` command to verify network access and credentials. On failure,
follow the printed `mason onboard --wizard` route.

## Agent CLI

| Command | Description |
|---|---|
| `mason -m "Hello!"` | Send one message and exit |
| `mason` | Start interactive terminal chat |
| `mason --session <id>` | Use a specific saved session |
| `mason --workspace <path>` | Override workspace |
| `mason --config <path>` | Use a specific config file |
| `mason --no-markdown` | Print plain text instead of Markdown |
| `mason --logs` | Show runtime logs while chatting |

## Session Storage and Rollback

Session JSONL files live under `<config-dir>/sessions/<workspace-id>/`, outside the
agent-readable workspace. On the first upgraded start, mason safely migrates existing
`<workspace>/sessions/*.jsonl` files after verifying an atomic copy. Stop every old mason
process that uses the workspace before upgrading; old and new binaries must not write the
same session concurrently.

To prepare a downgrade, stop mason and copy the current sessions back to the path understood
by older releases:

```bash
mason sessions restore-workspace --config ./bot-a/config.json --workspace ./bot-a/workspace
```

The command never deletes the external store and refuses to overwrite a different existing
workspace file. Back up both the config directory and workspace before changing versions.

Interactive mode is the terminal prompt. It exits with `exit`, `quit`, `/exit`, `/quit`, `:q`,
or `Ctrl+D`. Non-interactive input/output keeps the same prompt so scripts and diagnostic
workflows stay predictable.

## Gateway

`mason gateway` starts enabled chat channels, cron-backed system jobs such as memory consolidation, memory rollover, and heartbeat, and the health endpoint. By default it runs in the foreground, which keeps existing scripts and terminal workflows unchanged. Use `--background` when you want a local macOS, Linux, or Windows process that you can manage from the CLI.

| Command | Description |
|---|---|
| `mason gateway` | Start the gateway in the foreground with config defaults |
| `mason gateway --verbose` | Show verbose runtime output |
| `mason gateway --port <port>` | Override `gateway.port` for the health endpoint |
| `mason gateway --workspace <path>` | Override workspace |
| `mason gateway --config <path>` | Use a specific config file |
| `mason gateway --background` | Start the gateway as a background process |
| `mason gateway status` | Show PID, foreground/background launch mode, explicit/on-demand lifetime, live client count, state, and logs |
| `mason gateway logs --no-follow` | Print recent background gateway logs and exit |
| `mason gateway logs` | Follow background gateway logs |
| `mason gateway restart` | Restart the recorded background gateway with the current config |
| `mason gateway stop` | Stop the recorded background gateway |
| `mason gateway install-service` | Install a systemd user service or macOS LaunchAgent |
| `mason gateway install-service --dry-run` | Preview the generated service file and system commands |
| `mason gateway uninstall-service` | Remove the installed system service |

For custom instances, pass the same selector flags to management commands:

```bash
mason gateway --background --config ./bot-a/config.json --workspace ./bot-a/workspace
mason gateway status --config ./bot-a/config.json --workspace ./bot-a/workspace
mason gateway stop --config ./bot-a/config.json --workspace ./bot-a/workspace
mason gateway install-service --config ./bot-a/config.json --workspace ./bot-a/workspace --name bot-a
```

`--background` is a lightweight detached process. `install-service` is for
login/startup integration: Linux uses a systemd user service; macOS uses a
LaunchAgent plist. System services run the foreground gateway under the OS
supervisor rather than nesting another background process.

Default health endpoint:

```text
http://127.0.0.1:18790/health
```

## Local Triggers

`mason trigger` delivers one local message to a trigger that was created from
a chat/session with `/trigger <name>`.

```bash
mason trigger trg_8K4P2Q9X "Review PR #4502"
```

Keep `mason gateway` running so the message can be delivered to the linked
chat/session. The message is recorded as an automation turn in that session,
not as a normal chat message typed by the user.

The command writes to a workspace-local durable queue. If `mason gateway` is
not running yet, the message waits in that workspace. If the target session is
already running a turn, the trigger waits for that session to become idle. If the
gateway exits after claiming a delivery but before the linked turn completes,
the next gateway start requeues that delivery. The queue is at-least-once, not
exactly-once, so the same message can be delivered again after an interrupted
process. If the agent receives the delivery and the turn fails, the delivery is
marked failed instead of retried indefinitely. Each delivery also writes an
audit record under `<workspace>/triggers/runs`. Run one gateway consumer per
workspace; this local queue is not a distributed multi-consumer queue.

Use stdin when another local process generates the message:

```bash
generate-report | mason trigger trg_8K4P2Q9X
```

Options:

| Command | Description |
|---|---|
| `mason trigger <id> "message"` | Deliver one message through a trigger |
| `mason trigger <id>` | Read the message from stdin |
| `mason trigger --config <path> <id> "message"` | Use the workspace from a specific config |
| `mason trigger --workspace <path> <id> "message"` | Use a specific workspace |

For webhooks or other external systems, run your own small service and have it
call this CLI after it decides what message mason should receive.

See [Automations](./automations.md) for the broader automation model and delivery
behavior.

## OpenAI-Compatible API

| Command | Description |
|---|---|
| `mason serve` | Start `/v1/chat/completions`, `/v1/models`, and `/health` |
| `mason serve --host <host>` | Override API bind host |
| `mason serve --port <port>` | Override API port |
| `mason serve --timeout <seconds>` | Override per-request timeout |
| `mason serve --verbose` | Show runtime logs |
| `mason serve --workspace <path>` | Override workspace |
| `mason serve --config <path>` | Use a specific config file |

Default API endpoint:

```text
http://127.0.0.1:8900
```

Public binds (`0.0.0.0` or `::`) require `api.apiKey`; send it as a Bearer token on API routes.

See [`openai-api.md`](./openai-api.md) for request examples.

## Status

```bash
mason status
```

Shows the config path, workspace path, active model, and provider summary without calling a model.

| Command | Description |
|---|---|
| `mason status` | Inspect the default instance |
| `mason status --config <path>` | Inspect a specific config |
| `mason status --config <path> --workspace <path>` | Inspect a specific config with a workspace override |

## Channels

| Command | Description |
|---|---|
| `mason channels status` | Show configured channel status |
| `mason channels status --config <path>` | Show channel status for a specific config |
| `mason channels login <channel>` | Run interactive login for supported channels |
| `mason channels login <channel> --force` | Re-authenticate even if credentials already exist |
| `mason channels login <channel> --config <path>` | Use a specific config file |
| `mason channels connect <channel>` | Complete a browser authorization and store the result |
| `mason channels connect <channel> --config <path>` | Use a specific config file |
| `mason plugins list --config <path>` | Show plugin/channel enabled state for a specific config |

Examples:

```bash
mason channels login whatsapp
mason channels login weixin
mason channels connect linear
mason channels status
```

See [`chat-apps.md`](./chat-apps.md) for channel-specific setup.

## MCP

| Command | Description |
|---|---|
| `mason mcp login <server>` | Authorize an OAuth-protected remote MCP server |
| `mason mcp login <server> --config <path>` | Use a specific config file |
| `mason mcp logout <server>` | Remove stored OAuth credentials for one server |

The server must already be configured under `tools.mcpServers` with `"auth": "oauth"`. The command prints the authorization URL, then asks you to paste the full callback URL from the browser address bar. Tokens are stored in the mason data directory under `auth/mcp.json`, not in `config.json`. Use `mason mcp logout <server>` to remove them.

## Optional Features

Use these commands when you want mason to add or remove a built-in capability
without hand-editing JSON. Enabling may install the support package first.
Disabling is for channels such as Telegram, Matrix, or Slack; it keeps your
saved settings and turns the channel off.

The `plugins` command name is retained for compatibility, but these entries are
mason runtime support packages rather than user-invokable tools. They cannot be
attached to a chat turn with `@`.

| Feature name | What it enables |
|---|---|
| `api` | Dependencies required by the OpenAI-compatible `mason serve` process |
| `azure` | Azure identity support for Azure-hosted models |
| `bedrock` | AWS Bedrock model provider support |
| `langfuse` | Langfuse tracing support for OpenAI-compatible providers |
| `olostep` | Olostep web search provider support |
| A channel name such as `telegram` or `slack` | The connector package and saved channel enablement |

| Command | Description |
|---|---|
| `mason plugins list` | Show available channels and optional capabilities |
| `mason plugins enable <name>` | Install missing support and enable the feature or channel |
| `mason plugins enable <name> --logs` | Show package install logs while enabling |
| `mason plugins disable <channel>` | Turn off a channel without deleting its saved settings |
| `mason plugins list --config <path>` | Read a specific config file |
| `mason plugins enable <name> --config <path>` | Update a specific config file |
| `mason plugins disable <channel> --config <path>` | Turn off a channel in a specific config file |

Document and PDF reading are included in the standard installation. The old
`mason plugins enable documents` and `mason plugins enable pdf` commands
remain accepted as no-op compatibility aliases.

## Provider OAuth

| Command | Description |
|---|---|
| `mason provider login openai-codex --set-main` | Authenticate Codex and select its current default model |
| `mason provider login xai-grok --set-main` | Authenticate an eligible X Premium / Grok subscription and select Grok 4.6; hosted X Search is enabled for models that advertise support |
| `mason provider login github-copilot --set-main` | Authenticate GitHub Copilot and select its current default model |
| `mason provider logout openai-codex` | Remove OpenAI Codex OAuth state |
| `mason provider logout xai-grok --config <path>` | Remove the selected mason instance's xAI OAuth state |
| `mason provider logout github-copilot` | Remove GitHub Copilot OAuth state |

See [`providers.md`](./providers.md#oauth-providers) for when OAuth providers need explicit provider/model selection.

## Useful First Checks

```bash
mason --version
mason status
mason agent -m "Hello!"
```

If these fail, use [`troubleshooting.md`](./troubleshooting.md) before debugging chat apps, Docker, systemd, or SDK integrations.
