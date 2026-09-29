# Start Without Technical Background

This walkthrough is for people who have not used a terminal, API key, or JSON config file before. The goal is only to get one reply in the terminal. You do not need to understand nanobot's architecture or edit its config by hand.

## What You Will Need

- A Windows, macOS, or Linux computer.
- Python 3.11 or newer.
- An account or endpoint that can run an AI model.
- The API key, login, endpoint, and model name required by that service. A local model such as Ollama may not require an API key.

An API key is password-like. Do not post it in an issue, screenshot, chat, or public config file.

## A Few Useful Words

| Word | Meaning |
|---|---|
| Terminal | A text window where you paste a command and press Enter |
| Command | One instruction typed into the terminal |
| Provider | The service or local server that runs the AI model |
| Model ID | The exact model name expected by that provider |
| API key | A secret credential that lets software call the provider |
| Wizard | A question-and-answer setup menu |
| Chat app | Telegram, Discord, Slack, Feishu, WeChat, or another platform where nanobot can talk to you |

## 1. Install Python

Download Python from [python.org](https://www.python.org/downloads/) if you do not already have version 3.11 or newer. On Windows, enable **Add python.exe to PATH** if the installer shows that option.

Open a terminal:

| System | How |
|---|---|
| Windows | Press `Win`, type `PowerShell`, and open Windows PowerShell |
| macOS | Press `Command+Space`, type `Terminal`, and press Enter |
| Linux | Open your application menu and search for Terminal |

Check Python:

```bash
python --version
```

The result should start with `Python 3.11` or a newer number. If the command is not found, close and reopen the terminal. You can also try `python3 --version` on macOS/Linux or `py --version` on Windows.

## 2. Prepare Your Model Details

nanobot does not create an AI provider account for you. Before setup, have these details nearby:

1. The provider or company endpoint name.
2. Its API key, if it requires one.
3. Its base URL, if its documentation gives you one.
4. A model ID your account can use.

The provider, credential, endpoint, and model must belong together. For example, an API key from one provider usually cannot call a model name copied from a different provider.

## 3. Install nanobot

nanobot is installed from a copy of its source code. Get that copy first:

1. Open the Mason repository at `github.com/Mochi-Sora/MasonAgent` in a browser.
2. Choose **Code → Download ZIP**, then extract the ZIP (for example to your home folder). If you have Git, `git clone https://github.com/Mochi-Sora/MasonAgent.git` does the same thing.
3. Open a terminal inside the extracted folder. On macOS, right-click the folder and choose **Services → New Terminal at Folder**. On Windows, open the folder and type `PowerShell` in the address bar. On Linux, right-click inside the folder and choose **Open in Terminal**.

Then run the installer for your system. Copy only the text inside the code block.

**macOS / Linux**

```bash
./scripts/install.sh
```

**Windows PowerShell**

```powershell
.\scripts\install.ps1
```

If Windows refuses to run the script, open PowerShell and run it with a one-time permission bypass:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

The installer sets up nanobot in an isolated Python environment, then opens the setup wizard. This can take a few minutes on the first run. It prints the exact command used to run nanobot; if `nanobot` is not found later, reuse that whole command instead of switching to a different Python command.

If you prefer not to keep the source folder, run the one-line installer instead; it clones the repository into `~/.nanobot/src` and installs from there:

```bash
curl -fsSL https://raw.githubusercontent.com/Mochi-Sora/MasonAgent/main/scripts/install.sh | sh
```

## 4. Configure Your Model in the Wizard

The installer starts the wizard automatically. Choose **Quick Start** and answer its prompts:

1. Choose your provider.
2. Enter its API key and base URL when required.
3. Enter a model ID available to your provider account.
4. Save the configuration.

Treat every API key like a password. Do not include it in screenshots or support requests.

If the wizard did not start, run it yourself:

```bash
nanobot onboard --wizard
```

If the terminal cannot find `nanobot`, take the exact command printed by the installer and replace its final arguments with `onboard --wizard`. That command may begin with `uv tool run`, `pipx run`, or the full path to nanobot's private Python environment.

## 5. Get the First Reply

Type this command:

```bash
nanobot agent -m "Hello!"
```

A normal assistant reply means setup is complete. The exact reply does not matter.

For an ongoing conversation, run `nanobot` without arguments. Type `/help` inside to see the available commands, and press `Ctrl+C` to leave.

## 6. Add One Thing at a Time

Do not configure every feature immediately. Choose one next goal:

| Goal | What to do |
|---|---|
| Change the AI model | Run `nanobot onboard --wizard` and update the model step, or edit `~/.nanobot/config.json` |
| Add a provider credential | Run `nanobot onboard --wizard`, or edit the provider block in `~/.nanobot/config.json` |
| Connect Telegram, Discord, Slack, Feishu, WeChat, or another chat app | Run `nanobot onboard --wizard`, choose the channel, then start `nanobot gateway` |
| Add a tool integration | Edit `~/.nanobot/config.json`, then restart the gateway |
| Schedule a reminder or recurring task | Ask nanobot in a chat; manage jobs with `/cron` or the `cron` tool |
| Work with project files | Set the workspace with `--workspace` or `agents.defaults.workspace`, then ask in a chat |

For a chat platform's account, bot, token, or permission prerequisites, use the [Chat Apps guide](./chat-apps.md). For local models and provider-specific recipes, use the [Provider Cookbook](./provider-cookbook.md).

## If Something Fails

Run these commands one at a time:

```bash
nanobot --version
nanobot status
nanobot agent -m "Hello!"
```

| What you see | What it usually means |
|---|---|
| `nanobot: command not found` | Reuse the exact nanobot command printed by the installer; it points to the isolated environment that contains the package |
| `401`, unauthorized, or invalid API key | The key is wrong, expired, or belongs to a different provider |
| Model not found | The model ID is misspelled or unavailable to your provider account |
| No reply appears | Check the error text above the prompt; rerun the wizard if the model step was skipped |
| A change was saved but nothing changed | Restart nanobot so the running process reloads the config |

If you ask for help, include your operating system, `nanobot --version`, `nanobot status`, the exact command, and the exact error. Remove every API key, bot token, password, OAuth token, and private account ID first.

Continue with the full [Troubleshooting guide](./troubleshooting.md) for an ordered diagnosis.

## Run nanobot Later

For one-off messages, run `nanobot agent -m "..."`. For a chat app that should always answer, keep the gateway online:

```bash
nanobot gateway
```

Press `Ctrl+C` to stop it, or run it in the background with `nanobot gateway --background` and manage it with `nanobot gateway status`, `logs`, `restart`, and `stop`.
