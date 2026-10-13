# How to Configure MCP Tools in mason

This guide adds an MCP server to mason so the agent can use external tools
through the Model Context Protocol.

## What you will build

- a working mason agent
- one MCP integration configured through `~/.mason/config.json`
- a restricted set of MCP tools exposed to the model

## When to use this

Use MCP when the capability you need already exists as an MCP server, or when
you want external tools to be managed outside mason core.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
mason agent -m "Hello!"
```

Install the MCP server runtime separately. Many examples use `npx`, `uvx`, or a
remote HTTP endpoint.

## Minimal working example

Add the server to `~/.mason/config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file"]
      }
    }
  }
}
```

Restart mason and ask a question that requires the MCP tool. Limit the enabled
tools when the server exposes more than the task needs.

### OAuth servers

Mark a remote server with `"auth": "oauth"` instead of static headers:

```json
{
  "tools": {
    "mcpServers": {
      "notion": {
        "type": "streamableHttp",
        "url": "https://mcp.notion.com/mcp",
        "auth": "oauth"
      }
    }
  }
}
```

Then authorize it once; the command prints a URL, and after you approve access in
the browser you paste the full callback URL back into the terminal:

```bash
mason mcp login notion
```

Tokens are stored in the mason data directory under `auth/mcp.json` and are
refreshed automatically afterwards. Remove them with
`mason mcp logout notion`.

## Production notes

- Prefer `enabledTools` over exposing every tool by default.
- Use `toolTimeout` for slow MCP operations.
- Use HTTP MCP only for endpoints you trust.
- For deployment-managed OAuth servers, set `auth` to `oauth` and run
  `mason mcp login <server>` once.
- Keep MCP server commands stable and versioned in deployment docs or scripts.

## Security notes

- Stdio MCP starts a local process; review the command before enabling it.
- HTTP/SSE MCP uses mason's SSRF guard, including OAuth discovery, registration,
  token exchange, and redirects.
- OAuth credentials live in the mason data directory, not in `config.json`.
- Allow private HTTP MCP hosts only with narrow `tools.ssrfWhitelist` CIDRs.
- Do not place secrets in command arguments when environment variables or
  headers can be used.

## Troubleshooting

- Run the MCP command outside mason first.
- Start `mason gateway --verbose` and inspect tool registration logs.
- If an HTTP MCP URL is blocked, check whether it points to loopback or a
  private address that needs explicit allowlisting.

## Related mason docs

- [MCP tools for AI agents](./mcp-tools-for-ai-agents.md)
- [Configuration: MCP](../configuration.md#mcp-model-context-protocol)
- [Security](../configuration.md#security)
