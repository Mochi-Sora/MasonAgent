# Deployment

Use this page after `mason agent -m "Hello!"` works locally. Deployment keeps long-running surfaces online: chat apps, heartbeat, memory consolidation, cron jobs, and channel connections.

## Before You Deploy

Check these once before Render, Docker, systemd, or LaunchAgent:

| Check | Why it matters |
|---|---|
| `mason status` shows the expected config and workspace | Confirms the process will read the instance you meant to run |
| `mason agent -m "Hello!"` works | Proves install, config, provider, model, and workspace writes before adding a service layer |
| Secrets are in environment variables or protected config files | API keys, bot tokens, OAuth state, and chat credentials should not be world-readable |
| The active config directory (including `sessions/`) and workspace are persistent | Sessions follow `--config`; memory, generated artifacts, and the workspace identity marker follow the workspace |
| Channel access control is intentional | Use `allowFrom`, pairing, or private test channels before exposing the bot |
| Ports are planned | Gateway health defaults to local-only `127.0.0.1:18790`; `mason serve` defaults to `8900` |
| Logs are easy to reach | Use `docker compose logs`, `journalctl`, LaunchAgent log files, or `mason gateway --verbose` while diagnosing startup |

Restart the deployed process after editing `config.json`. Long-running processes read config at startup.

## Choose a Runtime

| Runtime | Use it for | State location | Useful first command |
|---|---|---|---|
| Render | One-click hosted gateway | Persistent disk at `/home/mason/.mason` | [Deploy to Render](#render) |
| Docker Compose | Repeatable container runs on Linux servers or workstations | Bind-mount `~/.mason` to `/home/mason/.mason` | `docker compose run --rm mason-cli agent -m "Hello!"` |
| Docker CLI | Manual container testing or small one-off hosts | Bind-mount `~/.mason` to `/home/mason/.mason` | `docker run -v ~/.mason:/home/mason/.mason --rm mason status` |
| systemd user service | Linux user-level gateway that restarts automatically | Host user's `~/.mason` unless you pass explicit paths | `systemctl --user status mason-gateway` |
| macOS LaunchAgent | macOS gateway that starts after login | Host user's `~/.mason` unless the plist passes explicit paths | `launchctl list | grep ai.mason.gateway` |

## Render

Run mason online without managing a server. The blueprint deploys the gateway with a persistent disk so sessions, memory, and chat history survive restarts. Chat channels connect outbound, so the service needs no public inbound route; the gateway health endpoint is the only HTTP surface.

> [!IMPORTANT]
> This setup requires a paid Render service because persistent disks are not available on the free tier. During setup, provide `ANTHROPIC_API_KEY` (or replace it with your own provider's key in `render-config.json`).

[Review the deployment blueprint](../render.yaml)

### First Deployment

1. Fork this repository, then create a Render Blueprint from your fork. It creates one Starter web service and a 1 GB persistent disk.
2. Enter your `ANTHROPIC_API_KEY`.
3. Create the Blueprint and wait for the service status to become **Live**. The first build can take several minutes.
4. Add your chat channel credentials to `config.json` on the persistent disk (or edit `render-config.json` before deploying) and redeploy.

The model API key is used by mason to call the provider; do not share it in issues, screenshots, or chat.

### Updates and Data

The Blueprint disables automatic deploys so upstream repository changes do not unexpectedly restart your agent. To update, open the service in the Render Dashboard and choose **Manual Deploy → Deploy latest commit**.

The persistent disk keeps `config.json`, sessions, memory, cron state, media, and logs across restarts and updates. The deployment initializes `config.json` only when it does not already exist, so settings changed later are not replaced on every boot.

If deployment fails, open the service **Logs** page first. A missing model key fails provider requests after startup.

## Docker

> [!TIP]
> The `-v ~/.mason:/home/mason/.mason` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as the non-root user `mason` (UID 1000) and reads config from `/home/mason/.mason`. Always mount your host config directory to `/home/mason/.mason`, not `/root/.mason`.
> If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.mason`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.
>
> [!IMPORTANT]
> Official Docker usage currently means building from this repository with the included `Dockerfile`. Docker Hub images under third-party namespaces are not maintained or verified by the mason project; do not mount API keys or bot tokens into them unless you trust the publisher.

> [!IMPORTANT]
> The gateway defaults to `host: "127.0.0.1"` in `config.json` (set in `mason/config/schema.py`). Docker `-p` port forwarding cannot reach a container's loopback interface, so for the host or LAN to reach the health endpoint you must set the bind to `0.0.0.0` in `~/.mason/config.json` before starting the container:
>
> ```json
> {
>   "gateway": { "host": "0.0.0.0" }
> }
> ```
>
> When the container binds it to `0.0.0.0`, publish port `18790` to host loopback only; place any
> remotely monitored health endpoint behind a firewall or reverse proxy. If another host
> must probe it directly, replace `127.0.0.1` in the port mapping with a trusted host
> interface and restrict inbound traffic to the monitoring system.

### Docker Compose

The default image preinstalls WhatsApp dependencies. To bake other enabled
channels into an image (recommended for deployments without PyPI access), pass
a comma-separated `MASON_CHANNELS` build argument:

```bash
MASON_CHANNELS=telegram,slack docker compose build
```

The image keeps mason in a virtual environment owned by its built-in non-root
runtime user (UID 1000). If an enabled channel was not preinstalled, gateway
startup can therefore install its manifest-declared dependencies. Rebuilding
with `MASON_CHANNELS` keeps that installation reproducible instead of relying
on the container's writable layer. If you override the container with a
different `--user`, bake every enabled channel into the image because that UID
is not guaranteed write access to the virtual environment.

```bash
docker compose run --rm mason-cli onboard   # first-time setup
vim ~/.mason/config.json                     # add API keys
docker compose up -d mason-gateway           # start gateway
```

```bash
docker compose run --rm mason-cli agent -m "Hello!"   # run CLI
docker compose logs -f mason-gateway                   # view logs
docker compose down                                      # stop
```

The default Compose file drops all Linux capabilities except `CHOWN`, `SETUID`, and
`SETGID`, which the root entrypoint needs to fix bind-mount ownership and become UID
1000. It also enables `no-new-privileges`, so the non-root process cannot regain those
bootstrap capabilities through setuid binaries or file capabilities. Docker's default
AppArmor/seccomp profiles remain enabled. If you explicitly set
`"tools.exec.sandbox": "bwrap"` in `~/.mason/config.json`, add the bwrap
override file when starting containers:

```bash
docker compose -f docker-compose.yml -f docker-compose.bwrap.yml up -d mason-gateway
docker compose -f docker-compose.yml -f docker-compose.bwrap.yml run --rm mason-cli agent -m "Hello!"
```

The override adds `CAP_SYS_ADMIN` and disables AppArmor/seccomp confinement for the
container so bubblewrap can create its nested namespaces. It preserves
`no-new-privileges`. The host must also allow unprivileged user namespaces; the
override cannot bypass a host-level namespace restriction. Use it only when the
bwrap sandbox is enabled.

### Docker

```bash
# Build the image
docker build -t mason .

# Or preinstall a regular Python extra such as Bedrock support
docker build --build-arg MASON_EXTRAS=bedrock -t mason .

# Or preinstall dependencies for a specific set of channels
docker build --build-arg MASON_CHANNELS=telegram,slack -t mason .

# Initialize config (first time only)
docker run -v ~/.mason:/home/mason/.mason --rm mason onboard

# Edit config on host to add API keys
vim ~/.mason/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Mochat)
# and exposes the health endpoint on 18790.
docker run \
  --cap-drop ALL \
  --cap-add CHOWN --cap-add SETGID --cap-add SETUID \
  --security-opt no-new-privileges:true \
  -v ~/.mason:/home/mason/.mason \
  -p 127.0.0.1:18790:18790 \
  mason gateway

# If `tools.exec.sandbox: "bwrap"` is enabled, run with the extra permissions
# bubblewrap needs for nested namespaces. Without them, `bwrap` may exit with
# `clone3: Operation not permitted`.
docker run \
  --cap-drop ALL \
  --cap-add CHOWN --cap-add SETGID --cap-add SETUID --cap-add SYS_ADMIN \
  --security-opt no-new-privileges:true \
  --security-opt apparmor=unconfined \
  --security-opt seccomp=unconfined \
  -v ~/.mason:/home/mason/.mason \
  -p 127.0.0.1:18790:18790 \
  mason gateway

# Or run a single command
docker run -v ~/.mason:/home/mason/.mason --rm mason agent -m "Hello!"
docker run -v ~/.mason:/home/mason/.mason --rm mason status
```

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

Preview the generated unit first:

```bash
mason gateway install-service --manager systemd --dry-run
```

Install, enable, and start it:

```bash
mason gateway install-service --manager systemd
```

For a custom instance, pass the same config/workspace selector you use to run the gateway:

```bash
mason gateway install-service \
  --manager systemd \
  --name mason-telegram \
  --config ~/.mason-telegram/config.json \
  --workspace ~/.mason-telegram/workspace
```

Common operations:

```bash
systemctl --user status mason-gateway        # check status
systemctl --user restart mason-gateway       # restart after config changes
journalctl --user -u mason-gateway -f        # follow logs
mason gateway uninstall-service --manager systemd
```

The installer writes `~/.config/systemd/user/mason-gateway.service`, runs
`systemctl --user daemon-reload`, enables the unit, and restarts it. It uses the
current Python executable with `python -m mason gateway --foreground`, so the
service runs in the same environment you used to install mason.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## macOS LaunchAgent

Use a LaunchAgent when you want `mason gateway` to stay online after you log in, without keeping a terminal open.

Preview the generated plist first:

```bash
mason gateway install-service --manager launchd --dry-run
```

Install, load, enable, and start it:

```bash
mason gateway install-service --manager launchd
```

For a custom instance:

```bash
mason gateway install-service \
  --manager launchd \
  --name mason-telegram \
  --config ~/.mason-telegram/config.json \
  --workspace ~/.mason-telegram/workspace
```

Common operations:

```bash
launchctl list | grep ai.mason.gateway
launchctl kickstart -k gui/$(id -u)/ai.mason.gateway
mason gateway uninstall-service --manager launchd
```

The installer writes `~/Library/LaunchAgents/ai.mason.gateway.plist`, uses the
current Python executable with `python -m mason gateway --foreground`, and
writes LaunchAgent logs under `~/.mason/logs/`.

> **Note:** if startup fails with "address already in use", stop the manually started `mason gateway` process first.
