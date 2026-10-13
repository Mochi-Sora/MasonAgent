# How to Run a Self-Hosted AI Agent with mason

This guide sets up mason as a self-hosted AI agent runtime on your own
machine or server. The result is a gateway process that can serve
chat apps, automations, and API integrations.

## What you will build

- a mason config and workspace under your control
- a model provider connected through `config.json`
- a long-running `mason gateway`
- optional chat app and API access

## When to use this

Use this path when you want local or server-side ownership of the agent process,
workspace files, memory files, and provider keys. It is also the right path when
the agent must keep running after one terminal command finishes.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
mason agent -m "Hello!"
```

Complete the CLI check before deploying the gateway. A deployment problem is
much easier to debug after the provider and model are known to work.

## Minimal working example

For chat apps, automations, and trigger delivery, start the gateway:

```bash
mason gateway
```

Or connect a channel in `~/.mason/config.json`, then keep the same gateway
process running for messages.

## Production notes

- Use Docker, systemd, or a macOS LaunchAgent when the process should survive
  terminal exits.
- Give every deployed instance a distinct config path, workspace path, and port
  set.
- Keep secrets in environment variables and start the service from the same
  environment.
- Use health checks against the gateway or API process, not chat app delivery as
  the only signal.

## Security notes

- Bind local-only services to `127.0.0.1` unless you intentionally expose them.
- Set an API key before binding the OpenAI-compatible API to a public interface.
- Prefer pairing for DM-capable chat apps, and keep any static `allowFrom`
  allowlists strict.
- Enable `tools.restrictToWorkspace`; on Linux, use the bubblewrap sandbox for
  shell execution.

## Troubleshooting

- Run `mason status` with the same `--config` and `--workspace` flags used by
  the service.
- Run `mason gateway --verbose` while debugging channel startup.
- Check port conflicts if the gateway health endpoint or API endpoint fails to
  bind.

## Related mason docs

- [Deployment](../deployment.md)
- [Multiple Instances](../multiple-instances.md)
- [Configuration](../configuration.md)
- [Chat Apps](../chat-apps.md)
- [OpenAI-Compatible API](../openai-api.md)
