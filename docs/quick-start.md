# Install and Quick Start

This guide has one goal: get a normal nanobot reply in your terminal. Do not add chat apps, MCP servers, fallback models, or deployment until this path works.

If terminals, Python, or API keys are unfamiliar, use the [beginner walkthrough](./start-without-technical-background.md), which explains each term and screen.

These repository docs describe the current source tree. The installer installs the checkout you run it from, in editable mode, so the installed command matches these docs exactly.

## What You Need

- Python 3.11 or newer.
- Access to one supported AI provider, company endpoint, or local model server.
- The credential, endpoint URL, and model ID required by that service. Local providers such as Ollama may not require a key.

Git is only needed to clone the repository; a downloaded source archive works the same way.

## 1. Install nanobot

The recommended installer keeps nanobot out of the system Python environment, installs the checkout in editable mode, and then starts the setup wizard.

**macOS / Linux**

From the repository checkout:

```bash
./scripts/install.sh
```

**Windows PowerShell**

From the repository checkout:

```powershell
.\scripts\install.ps1
```

If you do not have the checkout on this machine yet, run the one-line installer instead; it clones the repository into `~/.nanobot/src` and installs from there:

```bash
curl -fsSL https://raw.githubusercontent.com/Mochi-Sora/MasonAgent/main/scripts/install.sh | sh
```

The installer chooses an active virtual environment, `uv`, `pipx`, or a managed environment under `~/.nanobot/venv`. It installs the checkout in editable mode, so pulling new source changes is enough to update the command. At the end it prints the exact command it used to run nanobot; if `nanobot` is not on `PATH`, reuse that full command in the examples below.

If you prefer to inspect the scripts first, open [`install.sh`](../scripts/install.sh) or [`install.ps1`](../scripts/install.ps1).

## 2. Configure Your Model

Keep the installer terminal open and follow the setup prompts to:

1. Choose the provider or endpoint that owns your credential.
2. Enter its API key or base URL when required.
3. Create or select a model preset using a model ID that provider can run.
4. Save the configuration.

First-run setup creates or updates:

| Path | Purpose |
|---|---|
| `~/.nanobot/config.json` | Provider, model, channel, tool, and runtime settings |
| `~/.nanobot/workspace/` | Memory, skills, automations, and generated files |
| `~/.nanobot/sessions/<workspace-id>/` | Recent session history stored outside the workspace; the ID remains stable across workspace moves |

If the installer did not run the wizard, start it with:

```bash
nanobot onboard
```

SSH, headless, existing-config, and older-release installs retain the terminal setup path:

```bash
nanobot onboard --wizard
```

## 3. Check the Setup

```bash
nanobot status
```

You want:

- a check mark for **Config** and **Workspace**;
- the model or preset you selected;
- a configured state for the provider used by that model.

Most other providers can say `not set`. This command validates local setup but does not call the model.

## 4. Get the First Reply

Send:

```text
Hello!
```

Any normal assistant answer is success. It proves that nanobot can load the config, reach the selected model, use the workspace, and run the agent loop.

If you prefer a persistent background process, run:

```bash
nanobot gateway --background
nanobot gateway status
```

Use `nanobot gateway logs`, `restart`, and `stop` to manage that background gateway.

## Terminal-Only Check

To send one message directly:

```bash
nanobot -m "Hello!"
```

Then start an interactive terminal chat with:

```bash
nanobot
```

In interactive mode, `Enter` sends and `Shift+Enter` inserts a newline (`Ctrl+J` is the
universal fallback). While nanobot is working, `Enter` sends immediately, `Tab` waits until the
current response is finished, and `Option+Up` on macOS (`Alt+Up` on Windows/Linux) edits the
latest waiting message. Exit
with `exit`, `/exit`, `:q`, or `Ctrl+D`.

## Choose One Next Step

After the first reply works, add one capability and test again:

| Goal | Recommended path |
|---|---|
| Learn sessions, workspaces, tools, and access modes | [Concepts](./concepts.md) |
| Connect a chat platform | Run `nanobot onboard --wizard` for the channel step, then use [Chat Apps](./chat-apps.md) for platform prerequisites |
| Change or add a model | Edit `modelPresets` in `~/.nanobot/config.json` or rerun the wizard; use the [Provider Cookbook](./provider-cookbook.md) for a recipe |
| Add web search, voice, or image generation | Set the matching keys in [Configuration](./configuration.md) |
| Add an MCP integration | Follow [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Schedule agent work | Read [Automations](./automations.md) |
| Run continuously or remotely | Read [Deployment](./deployment.md) |
| Integrate from code | Use the [Python SDK](./python-sdk.md) or [OpenAI-Compatible API](./openai-api.md) |

## Other Install Methods

Use one method, then continue at [Configure Your Model](#2-configure-your-model). Every method installs a checkout in editable mode, so the command always matches the source tree.

**uv**

```bash
uv tool install --editable <checkout>
nanobot onboard
```

**pipx**

```bash
pipx install --editable <checkout>
nanobot onboard
```

**pip in a virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
python -m pip install --editable <checkout>
nanobot onboard
```

If pip reports `externally-managed-environment`, use the installer, `uv`, `pipx`, or a virtual environment. Do not force a system-wide install.

Replace `<checkout>` with the path to the cloned or extracted repository. If the package is installed but the shell cannot find `nanobot`, use the runner that owns the installation. The recommended installer prints the exact command to reuse. Common forms are:

```bash
uv tool run --from nanobot-ai nanobot --version
pipx run --spec nanobot-ai nanobot --version
~/.nanobot/venv/bin/python -m nanobot --version
```

On Windows, the managed-environment form is `& "$HOME\.nanobot\venv\Scripts\python.exe" -m nanobot --version`. Replace `--version` with `onboard --wizard` or any other arguments you need. Use plain `python -m nanobot` only when that Python executable belongs to the environment where nanobot was installed.

For development details, follow [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Manual Configuration Fallback

Use this only when the wizard is unavailable or you intentionally manage JSON. First run `nanobot onboard`, then merge a provider and a named model preset into `~/.nanobot/config.json`.

A generic OpenAI-compatible setup has this shape:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${PROVIDER_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "model-id-from-your-provider"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Replace the provider, endpoint, and model together. Do not pair a credential from one service with a model ID from another. See [Provider Cookbook](./provider-cookbook.md) for hosted, OAuth, company, and local examples, and [Configuration](./configuration.md) for exact fields.

## Updating

Editable installs follow the checkout:

```bash
# In the checkout you installed from
git pull --ff-only
./scripts/install.sh     # refresh dependencies and reinstall
```

Because the install is editable, code changes are visible immediately after pulling. Re-running the installer synchronizes any changed dependencies. Then check `nanobot --version`. Run `nanobot onboard --refresh` when you want to add newly introduced default fields while preserving existing settings.

## If the First Reply Fails

Do not change several settings at once. Start with:

```bash
nanobot --version
nanobot status
nanobot agent -m "Hello!"
```

| Symptom | First check |
|---|---|
| `nanobot: command not found` | Reuse the installer command or method-specific runner described under [Other Install Methods](#other-install-methods) |
| JSON parse error | Check commas and braces; remember that docs examples are usually snippets |
| `401` or invalid API key | Verify the selected provider owns that key and remove accidental spaces |
| Model not found | Use a model ID available from the provider selected in the active preset |
| A chat app does not answer | Run `nanobot channels status`, then check the channel credentials in `config.json` |

Continue with the ordered [Troubleshooting guide](./troubleshooting.md) if the cause is still unclear.
