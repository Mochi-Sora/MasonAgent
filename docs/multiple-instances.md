# Multiple Instances

Run multiple mason instances simultaneously with separate configs and runtime data. Use `--config` as the main entrypoint. Optionally pass `--workspace` during `onboard` when you want to initialize or update the saved workspace for a specific instance.

## Quick Start

If you want each instance to have its own dedicated workspace from the start, pass both `--config` and `--workspace` during onboarding.

**Initialize instances:**

```bash
# Create separate instance configs and workspaces
mason onboard --config ~/.mason-telegram/config.json --workspace ~/.mason-telegram/workspace
mason onboard --config ~/.mason-discord/config.json --workspace ~/.mason-discord/workspace
mason onboard --config ~/.mason-feishu/config.json --workspace ~/.mason-feishu/workspace
```

**Configure each instance:**

Edit `~/.mason-telegram/config.json`, `~/.mason-discord/config.json`, etc. with different channel settings. The workspace you passed during `onboard` is saved into each config as that instance's default workspace.

**Run instances:**

```bash
# Check one instance before starting it
mason status --config ~/.mason-telegram/config.json

# Instance A - Telegram bot
mason gateway --config ~/.mason-telegram/config.json

# Instance B - Discord bot
mason gateway --config ~/.mason-discord/config.json

# Instance C - Feishu bot with custom port
mason gateway --config ~/.mason-feishu/config.json --port 18792
```

## Path Resolution

When using `--config`, mason derives its runtime data directory from the config file location. The workspace still comes from `agents.defaults.workspace` unless you override it with `--workspace`.

To open a CLI session against one of these instances locally:

```bash
mason agent -c ~/.mason-telegram/config.json -m "Hello from Telegram instance"
mason agent -c ~/.mason-discord/config.json -m "Hello from Discord instance"

# Optional one-off workspace override
mason agent -c ~/.mason-telegram/config.json -w /tmp/mason-telegram-test
```

> Interactive `mason agent` commands with the same `--config` and explicit `--workspace` selectors share one gateway instance. Different selectors produce isolated runtime state and processes. The one-shot agent path remains a direct local execution.

| Component | Resolved From | Example |
|-----------|---------------|---------|
| **Config** | `--config` path | `~/.mason-A/config.json` |
| **Workspace** | `--workspace` or config | `~/.mason-A/workspace/` |
| **Sessions** | config directory + workspace ID | `~/.mason-A/sessions/<workspace-id>/` |
| **Cron Jobs** | workspace directory | `~/.mason-A/workspace/cron/` |
| **Media / runtime state** | config directory | `~/.mason-A/media/` |

## How It Works

- `--config` selects which config file to load
- By default, the workspace comes from `agents.defaults.workspace` in that config
- If you pass `--workspace`, it overrides the workspace from the config file

## Minimal Setup

1. Copy your base config into a new instance directory.
2. Set a different `agents.defaults.workspace` for that instance.
3. Start the instance with `--config`.

Example config fragment:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.mason-telegram/workspace"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

The copied base config can keep using the same `modelPresets` and `agents.defaults.modelPreset`. If this instance needs a different model, change `agents.defaults.model` and `agents.defaults.provider` (or select it once with `/model`).

Start separate instances:

```bash
mason status --config ~/.mason-telegram/config.json
mason gateway --config ~/.mason-telegram/config.json
mason gateway --config ~/.mason-discord/config.json
```

Each gateway instance also exposes a lightweight HTTP health endpoint on `gateway.host:gateway.port`. By default, the gateway binds to `127.0.0.1`, so the endpoint stays local unless you explicitly set `gateway.host` to a public or LAN-facing address.

`GET /health` reports process liveness:

- Returns `200 OK` with `{"status":"ok","process":"alive","ready":true}`.
- Returns `503 Service Unavailable` when the gateway is not ready.

A successful response does not verify chat channels, MCP servers, or model provider connectivity.

Other paths return `404`.

Override workspace for one-off runs when needed:

```bash
mason gateway --config ~/.mason-telegram/config.json --workspace /tmp/mason-telegram-test
```

## Common Use Cases

- Run separate bots for Telegram, Discord, Feishu, and other platforms
- Keep testing and production instances isolated
- Use different models or providers for different teams
- Serve multiple tenants with separate configs and runtime data

## Notes

- Each instance must use a different port if they run at the same time
- Session data follows the active config directory; use a different workspace per instance to isolate memory, skills, and the stable session namespace ID
- `--workspace` overrides the workspace defined in the config file
- Cron jobs are stored in the active workspace; runtime media/state is derived from the config directory
