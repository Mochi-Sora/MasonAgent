# How to Deploy a Long-Running mason AI Agent Gateway

The mason gateway is the long-running self-hosted AI agent process that keeps
chat apps, automations, local triggers, heartbeat and memory
consolidation jobs online.

## What you will build

- a verified mason config
- a gateway process
- a service or container deployment path with Docker, systemd, or macOS
  LaunchAgent

## When to use this

Use this when mason should keep running after a single CLI turn. Chat apps,
background automations, local triggers, and server-side
integrations all depend on a live gateway.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
mason status
mason agent -m "Hello!"
```

## Minimal working example

Run the gateway in the foreground:

```bash
mason gateway
```

Run the gateway in the background:

```bash
mason gateway --background
mason gateway status
mason gateway logs
```

## Production notes

- Docker Compose is the most repeatable Linux container path.
- systemd user services are useful for Linux user-level gateway deployments.
- macOS LaunchAgent keeps the gateway alive after login.
- Persist the active config directory's `sessions/` folder together with the workspace
  (including `.mason/workspace-id`), memory files, channel login state, and generated artifacts.
- Restart the gateway after editing `config.json`.

## Security notes

- Plan ports before exposing services. Gateway health defaults to `18790`
  and `mason serve` defaults to `8900`.
- Bind externally only when you have configured tokens or API keys.
- Keep chat access control intentional before deploying.
- Use Docker or Linux sandboxing when shell tools are enabled for unattended
  work.

## Troubleshooting

- Use the same `--config` and `--workspace` flags for status checks and service
  startup.
- Check logs with `docker compose logs`, `journalctl`, LaunchAgent logs, or
  `mason gateway --verbose`.
- If Docker port publishing does not work, confirm the service is not bound only
  to container loopback.

## Related mason docs

- [Deployment](../deployment.md)
- [Multiple Instances](../multiple-instances.md)
- [Configuration](../configuration.md)
