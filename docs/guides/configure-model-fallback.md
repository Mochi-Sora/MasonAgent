# How to Configure Model Fallback in mason

Model fallback lets mason try a primary model first, then fall back to one or
more candidates when the primary provider fails or rate-limits.

## What you will build

- a primary model selected by `agents.defaults.model` and `agents.defaults.provider`
- an ordered `agents.defaults.fallbackModels` chain
- optional inline candidates that carry their own provider and settings

## When to use this

Use fallback when you want better reliability across rate limits, provider
outages, local model downtime, or cost-sensitive routing.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
mason agent -m "Hello!"
```

Verify each provider works before adding it as a fallback.

## Minimal working example

Merge this shape into `~/.mason/config.json` and replace provider/model names
with ones you control:

```json
{
  "modelPresets": {
    "Fast": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "Fast",
      "model": "primary-model-id",
      "provider": "primary-provider",
      "fallbackModels": [
        {
          "model": "fallback-model-id",
          "provider": "fallback-provider",
          "maxTokens": 4096,
          "contextWindowTokens": 200000,
          "temperature": 0.1
        }
      ]
    }
  }
}
```

String entries in `fallbackModels` are model IDs and use the active provider.
Use inline objects when a fallback needs a different provider or its own
settings. A string that matches a legacy preset name still resolves to that
preset's old `model` binding for migration. The
[Provider Cookbook](../provider-cookbook.md) has concrete recipes for common
providers.

## Production notes

- Keep fallback context windows realistic; smaller fallback windows constrain
  how much context can fit.
- Put cheaper or faster fallbacks before expensive ones when acceptable.
- Use `/model <provider> <model>` for runtime model switching and `/presets <name>`
  to change generation settings, both without editing config.
- Keep generation preset names human-readable; the same names appear in `/presets`.

## Security notes

- Different providers may have different data handling policies.
- Do not put provider keys directly in shared config files.
- Confirm fallback models can safely receive the same prompts and files.

## Troubleshooting

- If a fallback never triggers, confirm the primary error is treated as
  retryable/fallbackable.
- If startup fails, check that each inline fallback names a valid provider and
  that every string candidate is a model ID your provider serves.
- If output is truncated after fallback, review `maxTokens` and
  `contextWindowTokens`.

## Related mason docs

- [Providers and Models](../providers.md)
- [Provider Cookbook: Fallback Models](../provider-cookbook.md#recipe-fallback-models)
- [Configuration: Model Fallbacks](../configuration.md#model-fallbacks)
