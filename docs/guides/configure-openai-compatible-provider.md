# How to Configure an OpenAI-Compatible Provider in mason

mason can call OpenAI-compatible model providers by configuring an `apiBase`,
optional `apiKey`, and a model selection that references that provider name.

## What you will build

- a custom provider entry
- `agents.defaults.model`/`provider` pointing at that provider
- one successful `mason agent` run

## When to use this

Use this for local or hosted services that expose OpenAI-compatible endpoints,
including internal gateways, local model servers, and provider proxies that are
not already named in mason.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
```

Verify the endpoint responds before debugging mason:

```bash
curl -sS https://api.example.com/v1/models
```

## Minimal working example

Merge this into `~/.mason/config.json`:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "Custom": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "Custom",
      "model": "provider-model-name",
      "provider": "custom"
    }
  }
}
```

Then run:

```bash
mason agent -m "Hello!"
```

## Production notes

- Include the version path in `apiBase` when the service expects `/v1`.
- Use separate provider names for separate endpoints.
- Use a placeholder key such as `EMPTY` only when the endpoint requires a
  non-empty key but does not validate it.
- Leave `apiType` unset for OpenAI-compatible custom endpoints.

## Security notes

- Keep provider keys in environment variables.
- Treat internal model gateways as sensitive network services.
- Do not point mason at untrusted proxy endpoints for private workspaces.

## Troubleshooting

- If `curl /models` fails, fix the provider endpoint before changing mason.
- If mason says the model is unknown, check the model ID expected by the
  provider.
- If auth fails, confirm whether the provider wants Bearer auth and whether the
  key is present in the environment that starts mason.

## Related mason docs

- [Provider Cookbook: Custom OpenAI-Compatible Provider](../provider-cookbook.md#recipe-custom-openai-compatible-provider)
- [Providers: Custom OpenAI-Compatible Endpoint](../providers.md#custom-openai-compatible-endpoint)
- [OpenAI-Compatible Agent API](./openai-compatible-agent-api.md)
