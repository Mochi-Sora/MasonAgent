# How to Connect an AI Agent to Chat Apps with nanobot

nanobot can run as a self-hosted chatbot or AI agent in Telegram, Discord,
Slack, WeChat, Email, Mattermost, and other chat apps. The gateway receives chat
messages, runs the agent, and sends replies back to the same channel.

## What you will build

- a working local agent
- one enabled chat channel
- a running gateway
- a pairing-based approval flow or a narrow static allowlist

## When to use this

Use chat apps when the agent should live where users already communicate:
private DMs, team channels, group chats, email threads, or bot workspaces.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
nanobot agent -m "Hello!"
```

Get one reply in the terminal before adding a channel. Then choose one platform guide for the bot/account prerequisites:

- [Telegram AI agent](./telegram-ai-agent.md)
- [Discord AI agent](./discord-ai-agent.md)
- [Slack AI agent](./slack-ai-agent.md)
- [Feishu AI agent](./feishu-ai-agent.md)
- [WhatsApp AI agent](./whatsapp-ai-agent.md)
- [WeChat AI agent](./wechat-ai-agent.md)
- [QQ AI agent](./qq-ai-agent.md)
- [Email AI agent](./email-ai-agent.md)
- [Mattermost AI agent](./mattermost-ai-agent.md)

## Minimal working example

Use the guided channel setup:

1. Get the platform token, login state, webhook, or mailbox credentials.
2. Run `nanobot onboard --wizard` and choose the channel step.
3. Pick the platform and answer the credential prompts, or start its QR flow.
4. Install optional support if prompted.
5. Start the gateway: `nanobot gateway`.
6. Send a private test message.
7. Approve the pairing request with `/pairing approve <code>` from an authorized chat when a DM-capable channel asks for one.

To configure manually instead, use the full [Chat Apps reference](../chat-apps.md#manual-setup-pattern).

Check status from the terminal when you need a lower-level confirmation:

```bash
nanobot channels status
```

For a chat-only or server deployment, start the gateway directly:

```bash
nanobot gateway
```

Use the full [Chat Apps reference](../chat-apps.md) when you manage `config.json` directly or need platform-specific advanced settings.

## Production notes

- Keep the gateway running as a service for always-on chat apps.
- Use mention-only group policies before opening a bot to busy channels.
- Use one channel at a time while debugging.
- Prefer DMs for first tests; pairing only works in DMs, and group chats add
  permissions and routing behavior.

## Security notes

- Prefer pairing or explicit allowlists; do not use `allowFrom: ["*"]` outside
  an intentional sandbox.
- Rotate bot tokens if they are pasted into logs or shared files.
- Review file, shell, and web tool access before inviting other users.

## Troubleshooting

- If `nanobot channels status` does not show the channel, the config key or
  optional dependency is likely missing.
- If the first DM returns a pairing code, approve it with `/pairing approve <code>` from an authorized chat.
- If messages do not arrive, run `nanobot gateway --verbose` and compare
  platform credentials, event permissions, and allow lists.
- If group replies are unexpected, review that channel's group policy.

## Related nanobot docs

- [Chat Apps](../chat-apps.md)
- [Configuration](../configuration.md#channel-settings)
- [Pairing](../configuration.md#pairing)
- [Deployment](../deployment.md)
