# Providers and Models

Use this page when the first reply fails because of provider/model mismatch, or when you want to adapt the concrete setup example to a different provider. If you already know which provider you want and only need a pasteable setup, use [`provider-cookbook.md`](./provider-cookbook.md).

For normal local setup, run `mason onboard --wizard` to add provider credentials and select a model, or edit `config.json` directly. Use the JSON below for manual deployments, local endpoints, provider-specific fields, or diagnosis.

For every setup, answer three questions:

1. Which provider owns the credential or endpoint?
2. What model name does that provider expect?
3. Does the provider need `apiKey`, `apiBase`, OAuth login, cloud credentials, or only a local server URL?

Select the model with `agents.defaults.model` and `agents.defaults.provider` (or `/model` in chat). Generation settings are separate: point `agents.defaults.modelPreset` at a top-level `modelPresets` entry, or apply one in chat with `/presets <name>`. Presets are settings-only and never bind a model or provider, so switching models never changes your temperature, style, or token limits.

## Choose a Provider Without Guessing

The docs show concrete provider names so the JSON is copyable, not because mason ranks providers. Start from the service or endpoint you actually control:

| If you have... | Configure... |
|---|---|
| An API key from a hosted provider or gateway | That provider's `providers.<name>.apiKey`, then `agents.defaults.provider` plus a model ID from that service. |
| An OpenCode Zen or Go key | `providers.opencodeZen.apiKey` or `providers.opencodeGo.apiKey`, then `agents.defaults.provider: "opencode_zen"` or `"opencode_go"`. |
| A company proxy or regional endpoint | The matching provider block plus `apiBase` if the proxy gives you a URL. |
| A local OpenAI-compatible server | A local provider block such as `ollama`, `vllm`, `lmStudio`, or `custom`, usually with `apiBase`. |
| An OAuth-based account | Run the matching `mason provider login ...` command, then set `agents.defaults.provider` (or select it once with `/model <provider> <model>`). |
| No provider yet | Pick one outside mason based on account access, pricing, regional availability, privacy requirements, and the model IDs you need. Then come back with its key and model ID. |

## Minimal Shape

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "anthropic/claude-opus-4.5",
      "provider": "openrouter"
    }
  }
}
```

The provider config gives mason credentials and endpoint details. `agents.defaults.model` and `agents.defaults.provider` select the model; `modelPresets` optionally supplies generation settings. Replace the example provider and model together; mixing an API key from one provider with a model ID from another is the most common first-run failure.

## Provider, Model, API Key, and Base URL

These fields answer different questions:

| Field | Where it lives | Meaning |
|---|---|---|
| `provider` | `agents.defaults.provider` | Which mason provider adapter should send the request. `/model <provider> <model>` overrides it per session. |
| `model` | `agents.defaults.model` | The model ID expected by that provider or gateway. |
| `apiKey` | `providers.<provider>.apiKey` | Credential for that provider. Use `${ENV_VAR}` for secrets. |
| `apiBase` | `providers.<provider>.apiBase` | HTTP base URL of the provider endpoint. |
| `proxy` | `providers.<provider>.proxy` | Optional HTTP proxy for this provider only. Supported for OpenAI-compatible providers, OpenAI Codex, and xAI OAuth. |

You usually omit `apiBase` for hosted built-in providers such as OpenRouter, Anthropic direct, OpenAI direct, Groq, or Bedrock because mason knows their default endpoints. Set `apiBase` for `custom`, local OpenAI-compatible servers, provider proxies, regional endpoints, or subscription endpoints. Include the API version path when the endpoint requires it, for example `https://api.example.com/v1` or `http://localhost:11434/v1`.

Use `proxy` when one provider must send HTTP traffic through a proxy without changing process-wide `HTTP_PROXY` / `HTTPS_PROXY`. This is supported for providers that use mason's OpenAI-compatible client, including `openai`, `custom`, named custom providers, OpenRouter-style gateways, local OpenAI-compatible servers, and similar registry entries. It is also supported for `openai_codex` and `xai_grok`, including OAuth token exchange/refresh and model requests. Native provider backends such as `anthropic`, `bedrock`, `azure_openai`, and `github_copilot` reject `proxy`; use their endpoint-specific configuration instead.

## Common Provider Patterns

### OpenRouter Gateway

Gateway-style setup for model IDs served through OpenRouter.

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "anthropic/claude-opus-4.5",
      "provider": "openrouter"
    }
  }
}
```

Use the model ID exactly as OpenRouter lists it.

To opt into OpenRouter server-managed search and fetch, add:

```json
{
  "providers": {
    "openrouter": {
      "extraBody": {
        "tools": [
          { "type": "openrouter:web_search" },
          { "type": "openrouter:web_fetch" }
        ]
      }
    }
  }
}
```

Chat Completions-compatible OpenRouter
[server tools](https://openrouter.ai/docs/guides/features/server-tools), such as those above, are
appended to mason's generated functions. This keeps unrelated local tools such as `write_file`
available in the same request. Responses-only server tools require an API surface that the
OpenRouter provider does not currently enable.

### OrcaRouter Gateway

[OrcaRouter](https://www.orcarouter.ai) is an OpenAI-compatible model routing gateway. Configure
the built-in `orcarouter` provider and use a model ID from OrcaRouter's catalog:

```json
{
  "providers": {
    "orcarouter": {
      "apiKey": "${ORCAROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "orcarouter/auto",
      "provider": "orcarouter"
    }
  }
}
```

Use the model ID exactly as OrcaRouter lists it. `orcarouter/auto` routes to a
suitable upstream automatically; explicit IDs such as
`anthropic/claude-sonnet-4.6` or `openai/gpt-5` are also accepted. OrcaRouter API keys start with
`sk-orca-`. Use OrcaRouter's catalog to pick a currently available model ID.

### Eden AI Gateway

Eden AI exposes an OpenAI-compatible chat-completions endpoint at
`https://api.edenai.run/v3`. Configure the built-in `edenai` provider and use
the full `provider/model` identifier listed by Eden AI:

```json
{
  "providers": {
    "edenai": {
      "apiKey": "${EDENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "anthropic/claude-sonnet-4-5",
      "provider": "edenai"
    }
  }
}
```

Mason sends the model ID unchanged, including its provider prefix. Use
Eden AI's [model listing](https://www.edenai.co/docs/v3/llms/listing-models)
to choose a currently available model.

### OpenCode Zen and Go

OpenCode Zen and OpenCode Go are OpenCode-managed gateways for coding-agent models.
They share `OPENCODE_API_KEY`, but use separate provider config keys and default base
URLs in mason.

```json
{
  "providers": {
    "opencodeZen": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "opencode/deepseek-v4-pro",
      "provider": "opencode_zen"
    }
  }
}
```

For OpenCode Go, switch the provider block and model selection:

```json
{
  "providers": {
    "opencodeGo": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "opencode-go/deepseek-v4-flash",
      "provider": "opencode_go"
    }
  }
}
```

OpenCode documents model IDs with `opencode/<model-id>` for Zen and
`opencode-go/<model-id>` for Go. mason accepts those prefixes and strips them
before sending the request to OpenCode. Use model IDs that OpenCode lists under
the `chat/completions` endpoint; models listed only under `responses`,
`messages`, or provider-specific endpoints are not handled by this
OpenAI-compatible provider path.

### Anthropic Direct

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "claude-opus-4-5",
      "provider": "anthropic"
    }
  }
}
```

Anthropic direct uses the native Anthropic provider. Do not use an OpenRouter model ID unless the provider is OpenRouter.

If you use an Anthropic-compatible proxy, keep the provider as `anthropic` and override `apiBase`:

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}",
      "apiBase": "https://anthropic-proxy.example.com"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "claude-sonnet-4-5",
      "provider": "anthropic"
    }
  }
}
```

Arbitrary custom provider names are OpenAI-compatible only; they do not use the Anthropic Messages API request format.

### OpenAI Direct

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 128000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "gpt-5",
      "provider": "openai"
    }
  }
}
```

`providers.openai.apiType` may be set when you need to force a specific OpenAI API surface. Other providers reject `apiType`; leave it unset outside `providers.openai`. Replace the model with a model ID available to your OpenAI account. Direct OpenAI Responses, OpenAI Codex, Azure OpenAI Responses, and eligible GitHub Copilot models share [opaque Responses state retention](./configuration.md#responses-state-and-compaction); native compaction is enabled only where the backend supports it. Provider-native features for OpenAI web search, Codex Fast mode, DeepSeek web search, and Grok X Search are enabled by setting the corresponding raw provider request fields under `extraBody`.

DeepSeek is the model-level exception in the OpenAI-compatible provider: `deepseek-flash` and `deepseek-v4-pro` (plus the legacy `deepseek-v4-flash*` aliases) automatically use DeepSeek's native Responses API. Mason does **not** enable DeepSeek's provider-hosted web search or fetch tools; its own `web_search` and `web_fetch` function tools are sent instead. To opt in to the hosted `web_search` tool, add it explicitly under `providers.deepseek.extraBody.tools` (for example `[{"type": "web_search"}]`).

### Custom OpenAI-Compatible Endpoint

The `custom` provider fits one OpenAI-compatible endpoint that is not represented by a named provider.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "provider-model-name",
      "provider": "custom"
    }
  }
}
```

`custom` does not infer a default base URL. Set `apiBase`.

If you have more than one custom OpenAI-compatible endpoint, give each endpoint its own provider key under `providers` and select that key with `/model <provider> <model>` (or set it as `agents.defaults.provider`). The key can be a name that makes sense in your environment, such as `companyProxy`, `tenant-a`, or `dev-local`.

```json
{
  "providers": {
    "companyProxy": {
      "apiKey": "${COMPANY_PROXY_API_KEY}",
      "apiBase": "https://llm-proxy.example.com/v1"
    },
    "tenant-a": {
      "apiBase": "https://tenant-a.example.com/v1"
    }
  },
  "modelPresets": {
    "company": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "company",
      "model": "gpt-4o-mini",
      "provider": "companyProxy"
    }
  }
}
```

Custom provider keys are treated as direct OpenAI-compatible providers. `apiBase` is required because mason cannot know the endpoint URL. `apiKey` is optional for local servers or private proxies that do not require one. Choose a name that does not conflict with a built-in provider name or alias, such as `openai`, `openai-codex`, `github-copilot`, or `lm-studio`. Do not set `apiType` on custom provider keys; `apiType` is only for `providers.openai`.

If your custom endpoint documents a nonstandard thinking toggle, set `providers.<name>.thinkingStyle` to `thinking_type`, `enable_thinking`, or `reasoning_split`; mason then maps `reasoningEffort` onto that provider-specific request body. Leave it unset for ordinary OpenAI-compatible endpoints.

This named custom provider path is not for Anthropic-compatible endpoints. For Anthropic-compatible proxies, use `providers.anthropic.apiBase` and select `anthropic` as the provider.

### ModelScope

ModelScope (魔搭社区) exposes an OpenAI-compatible LLM endpoint plus a separate async image generation API. Both are covered by the built-in `modelscope` provider.

Create a ModelScope [access token](https://modelscope.cn/my/myaccesstoken), then choose a model whose page exposes API-Inference. The example below uses [`Qwen/Qwen3-32B`](https://modelscope.cn/models/Qwen/Qwen3-32B); hosted availability and quotas are controlled by ModelScope. See the official [API-Inference guide](https://modelscope.cn/docs/model-service/API-Inference/intro) for current service details.

```json
{
  "providers": {
    "modelscope": {
      "apiKey": "${MODELSCOPE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "Qwen/Qwen3-32B",
      "provider": "modelscope"
    }
  }
}
```

Use an inference-enabled model ID exactly as ModelScope publishes it (usually `Namespace/model-name`). The default base URL is `https://api-inference.modelscope.cn/v1`; override `providers.modelscope.apiBase` only if your account routes through a different host. Chat model IDs may optionally be prefixed with `modelscope/`; mason strips that routing prefix before sending the request.

ModelScope image generation reuses the same provider key but is configured under `tools.imageGeneration`, not in `modelPresets`:

```json
{
  "tools": {
    "imageGeneration": {
      "enabled": true,
      "provider": "modelscope",
      "model": "Qwen/Qwen-Image-2512"
    }
  }
}
```

Use the image model's exact ModelScope ID without a leading `modelscope/`; the image client sends this value unchanged and handles ModelScope's async submit/poll flow. The example uses [`Qwen/Qwen-Image-2512`](https://modelscope.cn/models/Qwen/Qwen-Image-2512). See [Image Generation](./image-generation.md#modelscope) for supported sizes, aspect ratios, and the complete provider configuration.

### Ollama

Start Ollama separately, then point mason at the OpenAI-compatible endpoint.

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "llama3.2",
      "provider": "ollama"
    }
  }
}
```

Most Ollama setups do not require an API key.

Ollama renders the OpenAI-compatible messages and tools through each model's chat
template. If ordinary model responses are fast but tool-using turns show low prompt
cache reuse, diagnose the rendered template before changing mason's context or
memory settings. The
[Ollama prompt-cache guide](./guides/configure-ollama-prompt-cache.md) explains the
log pattern and a tested `llama3.1:8b` workaround.

### vLLM or Other Local OpenAI-Compatible Server

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "served-model-name",
      "provider": "vllm"
    }
  }
}
```

Some OpenAI-compatible local servers require any non-empty API key even when they do not validate it.

### LM Studio

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "local-model",
      "provider": "lm_studio"
    }
  }
}
```

Config keys may be camelCase or snake_case. Provider names in `agents.defaults` (or the `/model` selection) should use the registry name, such as `lm_studio`.

### AWS Bedrock

Bedrock can use the AWS credential chain, profile, region, or Bedrock bearer token depending on your AWS setup.

```json
{
  "providers": {
    "bedrock": {
      "region": "us-east-1",
      "profile": "default"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 8192,
      "contextWindowTokens": 200000
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "provider": "bedrock"
    }
  }
}
```

See [`configuration.md#providers`](./configuration.md#providers) for Bedrock-specific notes.

### OAuth Providers

Some providers do not use API keys in `config.json`.

For OpenAI Codex:

```bash
mason provider login openai-codex --set-main
```

On a headless host (a VPS or remote shell) use the device-code flow instead;
it prints a URL and a one-time code and needs no local callback listener:

```bash
mason provider login openai-codex --device --set-main
```

If `~/.codex/auth.json` already exists from a Codex CLI login, mason adopts
those credentials automatically instead of starting a new sign-in.

mason refreshes the account's Codex model catalog online, including current
context-window and reasoning-effort metadata, and falls back to a small compatible
catalog when the service cannot be reached. The current fallback covers GPT-6.1 Sol,
GPT-6 Astra/Sol/Luna, and the GPT-5.6 models that remain available during the
rollout; retired models (GPT-5.5, GPT-5.4, GPT-5.3-Codex-Spark) are not offered.

For an eligible X Premium / Grok subscription:

```bash
mason provider login xai-grok --set-main
```

This selects `xai-grok/grok-4.6`. mason reads xAI's online
model catalog, so newly available subscription models work without a mason
release. Online metadata is cached and enriched with mason's curated labels;
if xAI is temporarily unavailable, mason uses the last successful catalog or
a small built-in fallback. The same catalog
controls whether the provider exposes the hosted `x_search` tool; models that do
not advertise support continue without hosted X Search.
When enabled, Grok can search current X posts and return inline source links
without invoking a local mason tool. Credentials are stored under the
active instance's `auth/xai.json` (normally `~/.mason/auth/xai.json`), not in
`config.json` and not in Grok Build's credential file.
Hosted X Search remains enabled by default and can be disabled with
`providers.xaiGrok.extraBody.tools: []`.

The login is xAI subscription OAuth, not X Developer OAuth. It follows the
public client contract documented and implemented by
[Grok Build](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/02-authentication.md);
xAI may change that upstream contract independently of mason.

For GitHub Copilot:

```bash
mason provider login github-copilot --set-main
```

The login prints a device code; enter it on GitHub while the command waits.

mason reads the models enabled for the signed-in Copilot account and
lists entries that support its current Copilot chat-completions or Responses
transport, hiding models that it cannot route safely.

Each command authenticates the selected provider and makes its current default model active. OpenAI Codex and eligible GitHub Copilot models participate in [Responses state retention](./configuration.md#responses-state-and-compaction), while native compaction remains provider-capability-specific. OAuth providers are not valid automatic fallbacks. See [`troubleshooting.md`](./troubleshooting.md#provider-and-model-problems) for proxy, headless-login, model-name, and config-key errors.

## Provider Resolution

The model selection and the generation settings are separate:

1. `agents.defaults.model` and `agents.defaults.provider` select the model. At runtime, `/model <provider> <model>` overrides the selection for one session.
2. `agents.defaults.modelPreset` optionally applies a generation-settings preset from the top-level `modelPresets` object. At runtime, `/presets <name>` applies one to the current session. Presets never select a provider or model.

Provider selection follows this practical rule:

- An explicit `provider` in `agents.defaults` (or the `/model` selection) wins.
- `provider: "auto"` tries model-name keywords, configured keys, local base URLs, and gateway providers.
- Gateway providers such as OpenRouter and AiHubMix can route many model families, so the model name must be valid for that gateway.
- Local providers should normally be explicit because generic local model names such as `llama3.2` do not always contain provider keywords.

### Model Name Prefixes

`family/model-name` does not always select provider `family`. Prefix-based provider inference only runs when the active provider is `"auto"`.

- Explicit provider wins: `provider: "openrouter"` with `model: "anthropic/claude-sonnet-4.5"` calls OpenRouter, not Anthropic.
- With `provider: "auto"`, a prefix matching a configured built-in or named custom provider can select that provider. Named custom prefixes are stripped before request, so `companyProxy/gpt-4o-mini` is sent upstream as `gpt-4o-mini`.
- With an explicit named custom provider, the model is sent as written; `provider: "companyProxy"` with `model: "openai/gpt-4o-mini"` sends `openai/gpt-4o-mini` to `companyProxy`.

Pin `provider` in `agents.defaults` when using gateway catalog IDs such as `anthropic/claude-sonnet-4.5`.

## Generation Presets

Presets are standalone generation settings applied on top of whichever model is active. They never select a provider or model: use `/model` to choose a model and `/presets` to apply a preset. The same names can be used as new-session defaults.

```json
{
  "modelPresets": {
    "fast": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    },
    "deep": {
      "maxTokens": 8192,
      "contextWindowTokens": 200000,
      "temperature": 0.1,
      "reasoningEffort": "high"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "model": "anthropic/claude-sonnet-4.5",
      "provider": "openrouter"
    }
  }
}
```

The preset name `default` is reserved for the implicit settings taken from `agents.defaults.*`. Do not define `modelPresets.default`; use `/presets default` to return to the direct `agents.defaults.*` settings in older configs.

Older config files may still contain `model` and `provider` inside a preset. They remain loadable but are ignored: move that model into `agents.defaults.model`/`agents.defaults.provider` (or select it once with `/model`).

## Fallback Models

Fallbacks are useful for transient provider failures, rate limits, or model availability issues. Keep fallbacks compatible with the task size and tool use.

```json
{
  "modelPresets": {
    "fast": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": [
        {
          "model": "claude-opus-4-5",
          "provider": "anthropic",
          "maxTokens": 8192,
          "contextWindowTokens": 200000,
          "temperature": 0.1
        },
        {
          "model": "llama3.2",
          "provider": "ollama",
          "maxTokens": 4096,
          "contextWindowTokens": 32768,
          "temperature": 0.2
        }
      ],
      "model": "anthropic/claude-sonnet-4.5",
      "provider": "openrouter"
    }
  }
}
```

String entries in `fallbackModels` are model IDs. Use an inline object when a fallback needs its own provider or generation settings. For migration, a string that matches a legacy preset name still resolves to that preset's old `model` binding.

Use inline fallback objects so provider and settings travel with each candidate:

```json
{
  "modelPresets": {
    "fast": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "fast",
      "fallbackModels": [
        {
          "provider": "deepseek",
          "model": "deepseek-v4-pro",
          "maxTokens": 4096,
          "contextWindowTokens": 262144
        }
      ],
      "model": "anthropic/claude-sonnet-4.5",
      "provider": "openrouter"
    }
  }
}
```

`fallbackModels` belongs under `agents.defaults`, not inside each preset. If fallback candidates use smaller context windows, mason builds context using the smallest window in the active chain so every candidate can receive the same prompt. See [`configuration.md#model-fallbacks`](./configuration.md#model-fallbacks) for failure conditions.

## Quick Checks

Run these before debugging a chat app:

```bash
mason status
mason agent -m "Hello!"
```

If `mason agent -m "Hello!"` fails:

| Symptom | Likely cause |
|---|---|
| 401, unauthorized, invalid API key | Key is missing, expired, copied with whitespace, or stored under the wrong provider |
| model not found | Model ID does not exist for the selected provider or gateway |
| connection refused | Local provider server is not running or `apiBase` points to the wrong port |
| provider not found | The selected model uses a misspelled provider; use registry names such as `openrouter`, `anthropic`, `ollama`, `vllm`, `lm_studio` |
| works in CLI but not chat app | Provider is fine; debug gateway/channel setup in [`chat-apps.md`](./chat-apps.md) or [`troubleshooting.md`](./troubleshooting.md) |

For the complete provider table and advanced provider-specific notes, see [`configuration.md#providers`](./configuration.md#providers).
