# Connect nanobot as a Native Linear Agent

This guide takes you from an empty Linear channel configuration to a working
@mention or delegated issue. You create a private Linear OAuth app, save its
credentials in `~/.nanobot/config.json`, and authorize a workspace with
`nanobot channels connect linear`.

> [!NOTE]
> Linear's Agent APIs are currently a Developer Preview. Their schema may
> change. Keep nanobot current when you use this channel.

## What this channel does

- A new task starts when someone explicitly @mentions the installed app or
  delegates an issue to it.
- An ordinary issue comment does not invoke nanobot.
- A follow-up inside the existing Agent Session continues the same nanobot
  session without another @mention.
- Linear's stop control asks nanobot to cancel the active turn. The sender must
  have access, just as for other requests. An activity already delivered to
  Linear cannot be recalled.
- nanobot reports its acknowledgement, tool activity, reasoning, and final
  answer as native Linear Agent Activities.
- Button choices are sent as Linear selection activities, and local outbound
  files are uploaded to Linear before they are linked in the response.
- Private `uploads.linear.app` links in the session prompt are downloaded with
  the workspace OAuth token and passed to nanobot as inbound media after access
  checks. A prompt can import up to 10 attachments and 40 MB in total; skipped
  or unavailable files are called out in the prompt instead of failing silently.

The OAuth request includes `app:mentionable` and `app:assignable`, in addition
to `read` and `write`. Existing workspace installations must be reconnected to
grant `app:assignable`.

The native channel owns Agent Session transport. Connect the Linear MCP app when
the agent also needs tools for searching or changing Linear issues; OAuth scopes
on the channel do not expose those actions as nanobot tools. MCP does not replace
the channel's OAuth installation, webhook, or Agent Session transport.

## Before you start

There are two separate decisions: **connect a workspace**, then **choose who can
use nanobot**. The administrator sets up the app once; teammates do not need to
create their own apps, paste credentials, or scan a code.

```text
Prepare HTTPS → Create the Linear app → Save credentials in nanobot
             → Authorize a workspace → Enable members → Try an @mention
```

The HTTPS address receives Linear events. OAuth installs the app in a workspace.
Member access controls who may ask your nanobot to do work. These steps serve
different purposes; authorizing a workspace does not automatically approve all
its members.

Prepare these four things:

| Requirement | What you need |
|---|---|
| Working nanobot | `nanobot agent -m "Hello"` returns a response |
| Linear access | Permission to create a private OAuth app; a workspace admin must approve its installation |
| Public HTTPS origin | A reachable address such as `https://nanobot.example.com`; use a fixed hostname for ongoing use |
| Local route | The public address forwards to nanobot's Linear listener, which defaults to port `3979` |

The public address is an **origin**, not a complete endpoint. Enter
`https://nanobot.example.com`, not
`https://nanobot.example.com/linear/webhook`.

### Webhook and networking requirements

The native Linear Agent transport requires a webhook; there is no polling mode.
The OAuth callback also needs to reach the same nanobot instance.

You do **not** need a public IP address when using an HTTPS tunnel such as
Cloudflare Tunnel or Tailscale Funnel. On a publicly reachable server, Caddy,
nginx, or another reverse proxy can provide HTTPS and forward requests to
`127.0.0.1:3979`. Use a stable hostname for normal use: if the hostname changes,
update `publicBaseUrl` in nanobot and both registered URLs in the Linear app.

Choose the simplest option that matches your deployment:

| Your deployment | Recommended route |
|---|---|
| Home server, laptop, or a network behind NAT | A named HTTPS tunnel with a fixed hostname |
| VPS with a domain and existing HTTPS proxy | Add a reverse-proxy route to `127.0.0.1:3979` |
| Docker or Kubernetes | Route the ingress or proxy to the container's port `3979`; keep the public URL on the ingress |
| Short local test | A temporary HTTPS tunnel works; update `publicBaseUrl` and the app's callback and webhook URLs whenever its hostname changes |

With the default paths, the route must preserve these two requests:

```text
https://nanobot.example.com/linear/oauth/callback  -> 127.0.0.1:3979
https://nanobot.example.com/linear/webhook         -> 127.0.0.1:3979
```

Keep `host` at `127.0.0.1` when the proxy or tunnel runs on the same machine so
the listener is not exposed directly on the local network. Containers and
separate reverse-proxy hosts may require `0.0.0.0`. The Linear listener serves
plain HTTP locally; terminate HTTPS at the proxy or tunnel instead of exposing
port `3979` directly to the internet.

Only the callback and webhook need public routes. Keep the rest of the machine
private. An HTTPS endpoint is not an invitation to run the agent: webhooks must
pass signature checks, and senders must pass access checks.

### Temporary HTTPS for a local test

Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/),
then run it in a separate terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:3979
```

Use the printed `https://<random-name>.trycloudflare.com` origin as
`publicBaseUrl` in the setup below. Keep the tunnel running throughout
authorization and testing. The listener starts during `nanobot channels connect
linear`, so a 502 before that step can mean the local listener has not started
yet.

[Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
are intended for testing and generate a new hostname on restart. Update
`publicBaseUrl` and the Linear app's Redirect URI and Webhook URL before
continuing with a new hostname.

These settings must be updated together:

| Setting | Value with the new tunnel hostname |
|---|---|
| nanobot `publicBaseUrl` | `https://<new-hostname>.trycloudflare.com` |
| Linear **Redirect URIs** | `https://<new-hostname>.trycloudflare.com/linear/oauth/callback` |
| Linear **Webhooks → URL** | `https://<new-hostname>.trycloudflare.com/linear/webhook` |

Updating only the Redirect URI allows OAuth to succeed but leaves task events
pointing at the old tunnel. After updating the Webhook URL, also check its
**Delivery status**; a correct URL does not enable delivery by itself.

## Setup

### 1. Create the Linear app

In Linear, create a **private** OAuth application for your workspace with:

```text
Redirect URI:  https://nanobot.example.com/linear/oauth/callback
Webhook URL:   https://nanobot.example.com/linear/webhook
Webhook types: AgentSessionEvent, PermissionChange, OAuthAuthorization
```

Do not add a `Comment` webhook subscription. Linear delivers new @mentions and
Agent Session follow-ups through `AgentSessionEvent`; subscribing to comments
would add events that this channel intentionally ignores.

In the app's **Webhooks** section, check **Delivery status**. If it is
**Disabled**, use the adjacent **…** menu to enable delivery. Confirm the URL
points to the current public origin and that **Events** includes
**Agent session events** (`AgentSessionEvent`).

> [!IMPORTANT]
> OAuth authorization and webhook delivery are separate. Authorization can
> succeed while webhook delivery is disabled. In that state, Linear can create
> an Agent Session, but nanobot receives no task and Linear may report
> **Agent didn't start** or **nanobot failed to start**.

### 2. Save the credentials in nanobot

Merge this into `~/.nanobot/config.json`:

```json
{
  "channels": {
    "linear": {
      "enabled": true,
      "clientId": "YOUR_LINEAR_CLIENT_ID",
      "clientSecret": "YOUR_LINEAR_CLIENT_SECRET",
      "webhookSigningSecret": "YOUR_LINEAR_WEBHOOK_SIGNING_SECRET",
      "publicBaseUrl": "https://nanobot.example.com",
      "host": "127.0.0.1",
      "port": 3979,
      "webhookPath": "/linear/webhook",
      "oauthCallbackPath": "/linear/oauth/callback",
      "allowFrom": ["YOUR_LINEAR_USER_ID"],
      "showReasoning": true
    }
  }
}
```

Copy the values from the Linear app's settings:

| Linear value | nanobot field |
|---|---|
| Client ID | `clientId` |
| Client secret | `clientSecret` |
| Webhook signing secret | `webhookSigningSecret` |

Treat the Client Secret and Webhook signing secret like passwords. They belong
only in the nanobot configuration and the Linear application settings.

Listener settings, callback paths, and allowed users use the same block; see
[Configuration](../configuration.md#channel-settings) for every field.

### 3. Authorize the workspace

Stop the gateway if it is already running, then run:

```bash
nanobot channels connect linear
```

1. The command prints an authorization URL. Open it in a browser.
2. Choose the workspace, review the app's permissions and team access, and
   approve the installation as a workspace admin.
3. When the browser lands on the loopback callback address and cannot load the
   page, copy the full URL from the address bar and paste it back into the
   terminal.

A successful authorization prints the connected workspace. Start the gateway so
Linear can deliver webhooks:

```bash
nanobot gateway
```

Use `nanobot channels connect linear --force` to add or replace a workspace
later, `--operation inspect` to list connected workspaces, and
`--operation disconnect --workspace-id <id>` to revoke one workspace's tokens
and remove its local installation.

The pre-filled approach above creates a private app for the current workspace. A
distributable OAuth app can authorize additional workspaces; nanobot stores and
refreshes each workspace installation separately.

### 4. Choose who can use nanobot

Access is controlled in two layers:

- `allowFrom` lists Linear user IDs that may talk to the agent. `["*"]` allows
  every active member of the connected workspace.
- Without a matching `allowFrom` entry, a member's first prompt returns a
  pairing code. Approve it with `/pairing approve <code>` from an authorized
  chat or the local CLI (`nanobot agent -m "/pairing approve ABCD-EFGH"`).

At request admission nanobot checks current team membership through the app's
Linear API authorization, including active status and excluding app accounts.
Directory/API failure does not authorize a task; transient failures use the
channel's bounded webhook retry path. This adds API requests and latency, rather
than relying on stale cached permissions. Removing a member from `allowFrom`
blocks new requests (including stop requests); it does not cancel already
admitted/running tasks or recall replies. Stop ongoing tasks separately.

By default, reasoning is posted as Linear thought activities. Set
`showReasoning` to `false` for a quieter session.

### 5. Test the first task

1. Open an issue in the connected Linear workspace.
2. Add a comment that @mentions the app and includes a request, for example:

   ```text
   @nanobot summarize the likely cause and suggest the next diagnostic step
   ```

   You can instead delegate the issue to the app to start the Agent Session.
3. Open the Agent Session. You should first see a starting acknowledgement,
   followed by agent activity and a final response.
4. Send a follow-up inside that Agent Session. You do not need to @mention the
   app again there.

## Reconnect, remove, or reset workspaces

Each nanobot instance uses one configured Linear OAuth app, which may have
multiple workspace installations. A **private** app can only be installed in its
own workspace. To use the same app in another workspace, its distribution must
allow that installation in Linear; changing a nanobot field cannot bypass this.

- **Add or replace a workspace**: run `nanobot channels connect linear --force`.
  Choose the other workspace in Linear, review team access, and authorize. You
  do not need another HTTPS address or another set of app credentials for the
  same app.
- **Reauthorize a workspace**: run the same `--force` command. Use it to renew
  authorization or grant missing scopes. Cancelling authorization leaves
  existing connections alone.
- **Remove a workspace**: run
  `nanobot channels connect linear --operation disconnect --workspace-id <id>`.
  This revokes that workspace's tokens and clears its local installation. Other
  workspaces are unaffected. App credentials remain available for connecting
  again.
- **Switch apps**: update `clientId`, `clientSecret`, and
  `webhookSigningSecret`, then run `--operation disconnect` for the old
  app's workspaces and `nanobot channels connect linear` for the new one.
  Installations that belong to a different OAuth Client ID are rejected until
  they are reconnected.

Removal does not delete the Linear workspace, app, issues or comments, nor
nanobot conversation history or pairing approvals. Deleting the app itself is a
separate action in Linear.

## Updating a remote installation

A local branch does not update an already running remote gateway.

1. Develop and test in a separate checkout with test-only config/state. Do not
   copy production OAuth tokens, model keys, or pairing state into a development
   profile, or run a second gateway against the production webhook installation.
2. After review and deployment approval, back up the remote config, pairing file,
   and Linear SQLite state consistently (stop the gateway or use SQLite backup;
   copying just the database while WAL writes are active is not sufficient).
3. Update the remote source and dependencies to the reviewed revision, then
   restart the gateway. Preserve remote credentials and state. Existing OAuth
   scopes and webhook routes need not change.
4. Verify the existing owner's access, `allowFrom` behavior, and that no extra
   listener is exposed. Grant teammate access only deliberately.

## Verify the setup

The setup is complete when all of these checks pass:

- `nanobot status` lists the Linear channel as enabled with no runtime error,
  and `nanobot gateway` startup logs no Linear listener failure.
- The Linear app's **Webhooks → Delivery status** is enabled, its URL uses the
  current public origin, and **Events** includes **Agent session events**.
- While the channel is running, opening
  `http://127.0.0.1:3979/linear/health` on the nanobot machine returns
  `{"ok":true}`. Use your configured host and port if you changed them.
- If your proxy also forwards `/linear/health`, its public URL returns
  `{"ok":true}`. This is optional: a proxy exposing only the callback and webhook
  can correctly return 404 for public health. Use successful OAuth and webhook
  delivery to verify those two routes.
- A new comment with an explicit @mention creates an Agent Session and receives
  a response.
- Delegating an issue to the app creates an Agent Session and receives a response.
- A normal issue comment without an @mention does nothing.
- A follow-up inside the Agent Session receives a response without another
  @mention.

## Security and reliability

- OAuth uses authorization code flow, PKCE, a short-lived CSRF state, and the
  Linear app actor.
- Webhooks are verified against `Linear-Signature` using the raw request body
  before JSON parsing. nanobot also checks the delivery timestamp and configured
  OAuth Client ID.
- `Linear-Delivery` IDs are deduplicated.
- Verified events are committed to a local SQLite queue before nanobot returns
  HTTP 200. Queued webhook deliveries that have not yet been dispatched are
  retried after a restart. This does not guarantee resumption of an interrupted
  agent turn.
- Access and rotating refresh tokens are stored in the Linear channel state
  database, not in `config.json`.
- OAuth revocation removes the affected workspace installation locally.

Back up the nanobot instance data directory as carefully as other credentials.
Do not publish `linear/state.sqlite3`, the Client Secret, or the Webhook signing
secret.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `publicBaseUrl` is rejected | Use a public `https://` origin: no HTTP, localhost, private IP, or path. |
| `Invalid redirect_uri parameter for the application` | In the Linear app matching nanobot's Client ID, set **Redirect URIs** to the current public origin plus `/linear/oauth/callback` (or your configured `oauthCallbackPath`). It must match the authorization request exactly. Then run `nanobot channels connect linear` again. |
| The tunnel URL changed | Update `publicBaseUrl` and the Redirect URI and Webhook URL in the Linear app, then run `nanobot channels connect linear` again. |
| `Unable to start the Linear callback listener` | Another process uses the listen port, usually a running gateway with Linear enabled. Stop it and retry. |
| OAuth opens but cannot finish | Confirm the proxy forwards `/linear/oauth/callback` to the configured listen host and port. Paste the full callback URL back into the terminal, including `code` and `state`. |
| Public health returns 502 | The channel is not running. Start `nanobot gateway`. Check local health first, then confirm the tunnel or proxy targets the same listener port. |
| Another workspace is unavailable during authorization | Check the current app's distribution and your installation permissions in Linear. A private app only works in its own workspace. `--force` does not create a new app. |
| **Agent didn't start**, **nanobot failed to start**, or a session stays on **Thinking…** without a response | First check **Webhooks → Delivery status** in the Linear app. If **Disabled**, enable it from **…**. Confirm the current Webhook URL and `AgentSessionEvent` subscription, then select **Retry** in the Linear session. OAuth success and a working `/linear/health` endpoint do not prove that Linear is sending events. |
| An @mention still gets no response with delivery enabled | Inspect **Webhook delivery failures** in the Linear app and run `nanobot gateway logs`. A 404 points to the webhook path; a 502 points to the listener or tunnel; a 401 can indicate a signing-secret mismatch or stale event timestamp. Confirm the Client ID and signing secret belong to the same app and the workspace authorization has not been revoked. |
| The first @mention returns a pairing code | Approve it with `/pairing approve <code>`, or add the member's Linear user ID to `allowFrom` and restart the gateway. |
| Normal comments do nothing | This is intentional. Start a task by @mentioning the app, or continue inside an existing Agent Session. |
| Delegating an issue does not start a session | Run `nanobot channels connect linear --force` so the installation grants `app:assignable`, then confirm the app can be selected as the issue delegate. |
| The agent can discuss an issue but cannot search or change it | Connect the Linear MCP app (`nanobot mcp login <server>`). The native channel transports the conversation but does not add issue-management tools. |
| Authorization reports missing scopes | Run `nanobot channels connect linear --force`. Do not reuse an authorization URL that omits `read`, `write`, `app:mentionable`, or `app:assignable`. |

For Linear's platform-side behavior, see the official
[Agents guide](https://linear.app/developers/agents),
[OAuth app manifest reference](https://linear.app/developers/oauth-app-manifests),
[OAuth guide](https://linear.app/developers/oauth-2-0-authentication), and
[webhook reference](https://linear.app/developers/webhooks).
