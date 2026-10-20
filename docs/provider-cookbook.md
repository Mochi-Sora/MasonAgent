# Provider Cookbook

This page is for cases where you already know what you want to connect and need a pasteable setup. Each recipe shows what to set, what to run, and what a failure usually means.

If this is your first install and terminal commands are new to you, start with [`start-without-technical-background.md`](./start-without-technical-background.md). If you want the field-by-field explanation, read [`providers.md`](./providers.md) and then [`configuration.md#providers`](./configuration.md#providers).

Most examples below are snippets to merge into `~/.mason/config.json`. Keep any existing sections you still need, and replace placeholder keys such as `${OPENROUTER_API_KEY}` with environment-variable references or real values only on your own machine.

Recipes are examples, not rankings. Pick the recipe that matches the credential, endpoint, and model ID you already intend to use.

## Choose a Recipe

Match the recipe to the credential or endpoint you already have:

| What you have | Recipe | Must match |
|---|---|---|
| A gateway key and model IDs that include a model family path, such as `provider/model-name` | [OpenRouter Gateway](#recipe-openrouter-gateway) | API key, provider config key, selected provider, and gateway model ID |
| An OpenCode Zen or Go key | [OpenCode Zen or Go](#recipe-opencode-zen-or-go) | `OPENCODE_API_KEY`, the Zen/Go provider key, and a model ID from the matching OpenCode endpoint |
| An OpenAI platform API key and OpenAI model ID | [OpenAI Direct](#recipe-openai-direct) | `OPENAI_API_KEY`, `provider: "openai"`, and an OpenAI model available to that account |
| An Anthropic API key and Anthropic model ID | [Anthropic Direct](#recipe-anthropic-direct) | `ANTHROPIC_API_KEY`, `provider: "anthropic"`, and a non-gateway model ID |
| A Kimi Coding Plan key | [Kimi Coding Plan](#recipe-kimi-coding-plan) | `KIMI_CODING_API_KEY`, `provider: "kimi_coding"`, and `model: "kimi-for-coding"` |
| An OpenAI-compatible `/v1` endpoint that is not a named mason provider | [Custom OpenAI-Compatible Provider](#recipe-custom-openai-compatible-provider) | `apiBase`, optional API key, and the model ID served by that endpoint |
| Ollama already running locally | [Ollama Local Model](#recipe-ollama-local-model) | Ollama `apiBase`, pulled model name, and local server availability |
| vLLM, LM Studio, or another local OpenAI-compatible server | [vLLM or LM Studio](#recipe-vllm-or-lm-studio) | Local `/v1` base URL, any required key, and served model name |
| A primary model plus one or more backups | [Fallback Models](#recipe-fallback-models) | Model IDs or inline candidates in `agents.defaults.fallbackModels` |
| A working agent and a Langfuse project | [Langfuse Tracing](#recipe-langfuse-tracing) | Langfuse env vars in the same process environment that starts mason |

## How to Use a Recipe

1. Install mason and run `mason onboard` once so `~/.mason/config.json` exists. Use `mason onboard --wizard` if you prefer prompts over hand-editing JSON.
2. Put secrets in environment variables when possible.
3. Merge the recipe snippet into `~/.mason/config.json`.
4. Run `mason status`.
5. Run `mason agent -m "Hello!"`.
6. If the CLI works, continue with the gateway or chat apps.

The active model comes from `agents.defaults.model` and `agents.defaults.provider`, or from `/model` for one chat session. `agents.defaults.modelPreset` points to an entry in `modelPresets`, which supplies generation settings only and never selects a model.

## Secret Setup

Environment variables keep API keys out of the config file.

Use the variable name shown by the recipe you picked. The commands below use `OPENROUTER_API_KEY` only as an example; an OpenAI direct recipe uses `OPENAI_API_KEY`, an Anthropic direct recipe uses `ANTHROPIC_API_KEY`, and a custom endpoint can use any variable name you reference in `config.json`.

**macOS / Linux**

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
mason agent -m "Hello!"
```

**Windows PowerShell**

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
mason agent -m "Hello!"
```

Environment variables set this way apply only to the current terminal. For long-running services such as systemd, Docker, LaunchAgent, or a remote shell, set the variables in that service environment before starting mason.

## Recipe: OpenRouter Gateway

This recipe applies when one API key routes many hosted model families.

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary",
      "model": "anthropic/claude-sonnet-4.5",
      "provider": "openrouter"
    }
  }
}
```

Verify:

```bash
mason status
mason agent -m "Hello!"
```

If this fails with `401` or `unauthorized`, check that `OPENROUTER_API_KEY` is visible in the same terminal or service that starts mason. If it fails with `model not found`, choose a model ID that OpenRouter lists for your account.

## Recipe: OpenCode Zen or Go

This recipe applies when your credential comes from OpenCode Zen or OpenCode Go.
Both providers use `OPENCODE_API_KEY`; pick the provider block that matches the
subscription or balance you want to use.

OpenCode Zen:

```json
{
  "providers": {
    "opencodeZen": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
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

OpenCode Go:

```json
{
  "providers": {
    "opencodeGo": {
      "apiKey": "${OPENCODE_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
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

Verify:

```bash
mason status
mason agent -m "Hello!"
```

OpenCode's docs list models across multiple endpoint types. The `opencode_zen`
and `opencode_go` providers in mason use the OpenAI-compatible
`chat/completions` path. If a model fails with `model not found` or an endpoint
shape error, choose a model that OpenCode lists under `chat/completions` for the
matching Zen or Go endpoint.

## Recipe: OpenAI Direct

This recipe applies when you have an OpenAI API key and want to call OpenAI directly instead of through a gateway.

```json
{
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 128000,
      "temperature": 0.1
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

Verify:

```bash
OPENAI_API_KEY="sk-..." mason agent -m "Hello!"
```

If your shell cannot use inline environment variables, set `OPENAI_API_KEY` first and then run `mason agent -m "Hello!"`. If the provider rejects `apiType`, remove `apiType` unless you are using a documented OpenAI-specific mode.

## Recipe: Anthropic Direct

This recipe applies when your key comes from Anthropic and your model name is an Anthropic model ID, not an OpenRouter model path.

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 200000,
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

Verify:

```bash
ANTHROPIC_API_KEY="sk-ant-..." mason agent -m "Hello!"
```

If you copied a model name such as `anthropic/claude-sonnet-4.5`, that is a gateway-style model path and belongs under `provider: "openrouter"`, not `provider: "anthropic"`.

If you use an Anthropic-compatible proxy, select `anthropic` as the provider and set `providers.anthropic.apiBase`:

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
      "contextWindowTokens": 200000,
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

Do not configure Anthropic-compatible endpoints as arbitrary custom provider names; named custom providers use the OpenAI-compatible request format.

## Recipe: Kimi Coding Plan

This recipe applies when your key comes from Kimi's Coding Plan endpoint. Mason uses a dedicated `kimi_coding` provider for this Anthropic Messages API endpoint; do not configure it as a generic `custom` provider.

```json
{
  "providers": {
    "kimiCoding": {
      "apiKey": "${KIMI_CODING_API_KEY}"
    }
  },
  "modelPresets": {
    "kimiCoding": {
      "maxTokens": 4096,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "kimiCoding",
      "model": "kimi-for-coding",
      "provider": "kimi_coding"
    }
  }
}
```

Verify:

```bash
mason status
mason agent -m "Hello!"
```

The default base URL is `https://api.kimi.com/coding/v1`. This endpoint requires a Claude-compatible `User-Agent`; mason sends `claude-code/0.1.0` by default. If your account requires a different value, override it with `providers.kimiCoding.extraHeaders.User-Agent`.

## Recipe: Custom OpenAI-Compatible Provider

This recipe applies to an OpenAI-compatible service that is not a named mason provider.

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
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

Verify the endpoint before blaming mason:

```bash
curl -sS https://api.example.com/v1/models
mason agent -m "Hello!"
```

`apiBase` is the HTTP base URL, not the model name. Include the version path when the service expects it, such as `/v1`. If the service requires a non-empty key but does not validate it, use a placeholder such as `"apiKey": "EMPTY"`.

For multiple custom endpoints, do not overload the single `custom` block. Name each endpoint under `providers` and select it as the provider:

```json
{
  "providers": {
    "workProxy": {
      "apiKey": "${WORK_PROXY_API_KEY}",
      "apiBase": "https://proxy.example.com/v1"
    },
    "lab-local": {
      "apiBase": "http://127.0.0.1:8000/v1"
    }
  },
  "modelPresets": {
    "work": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "work",
      "model": "gpt-4o-mini",
      "provider": "workProxy"
    }
  }
}
```

To use the lab endpoint in a chat session, switch to it explicitly: `/model lab-local served-model-name`.

These custom names behave like direct OpenAI-compatible providers: `apiBase` is required, `apiKey` is optional when the endpoint allows anonymous or placeholder credentials, and `apiType` should be left unset. They do not support Anthropic-compatible endpoints; use the `anthropic` provider with `apiBase` for that case.

## Recipe: Ollama Local Model

This recipe applies when Ollama is already installed and the model has been pulled locally.

```bash
ollama serve
ollama pull llama3.2
```

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  },
  "modelPresets": {
    "local": {
      "maxTokens": 2048,
      "contextWindowTokens": 32768,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local",
      "model": "llama3.2",
      "provider": "ollama"
    }
  }
}
```

Verify:

```bash
curl -sS http://localhost:11434/v1/models
mason agent -m "Hello!"
```

If you see `connection refused`, Ollama is not running or `apiBase` points to the wrong port. If every response is slow, try a smaller local model or lower `contextWindowTokens`.

If direct Ollama responses are fast but tool-using mason turns repeatedly evaluate
thousands of prompt tokens, the model's chat template may be moving its tool
definitions between requests. See
[Improve Ollama Tool-Calling Prompt Cache Reuse](./guides/configure-ollama-prompt-cache.md)
for a diagnostic procedure and an optional model-specific workaround.

## Recipe: vLLM or LM Studio

This recipe applies when a local server exposes an OpenAI-compatible `/v1` API.

```json
{
  "providers": {
    "vllm": {
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "EMPTY"
    }
  },
  "modelPresets": {
    "local": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.2
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local",
      "model": "served-model-name",
      "provider": "vllm"
    }
  }
}
```

For LM Studio, use its local base URL and provider name:

```json
{
  "providers": {
    "lmStudio": {
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "modelPresets": {
    "local": {
      "maxTokens": 2048,
      "contextWindowTokens": 32768
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "local",
      "model": "local-model",
      "provider": "lm_studio"
    }
  }
}
```

The config key can be `lmStudio` or `lm_studio`, but the selected provider must use the registry name `lm_studio`.

## Recipe: Fallback Models

This recipe applies when one provider sometimes rate-limits, one model is expensive, or you want a local backup.

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
          "model": "claude-sonnet-4-5",
          "provider": "anthropic",
          "maxTokens": 4096,
          "contextWindowTokens": 200000,
          "temperature": 0.1
        },
        {
          "model": "llama3.2",
          "provider": "ollama",
          "maxTokens": 2048,
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

`fallbackModels` belongs under `agents.defaults`. String entries are model IDs; a value that matches a legacy preset name resolves to that preset's old model binding for migration. mason tries the active model first, then the fallback candidates in order.

Keep fallback candidates realistic. If the local fallback has a smaller context window, mason must build context that fits the smallest window in the active chain.

## Recipe: Langfuse Tracing

This recipe applies after the agent works and you want observability for OpenAI-compatible provider calls.

Install the optional package in the same Python environment that runs mason:

```bash
mason plugins enable langfuse
```

Set the environment variables before starting mason:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
mason agent -m "Hello!"
```

PowerShell:

```powershell
$env:LANGFUSE_SECRET_KEY = "sk-lf-..."
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
mason agent -m "Hello!"
```

Langfuse is not a model provider in `config.json`. It is configured through environment variables and traces supported OpenAI-compatible provider calls. Native providers that do not use that client path may not produce Langfuse OpenAI-wrapper traces.

## Recipe: Switch Models at Runtime

Use this after you have more than one model you can reach through your providers and are chatting through a supported channel.

```json
{
  "modelPresets": {
    "fast": {
      "maxTokens": 4096,
      "contextWindowTokens": 65536
    },
    "localSmall": {
      "maxTokens": 2048,
      "contextWindowTokens": 32768
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

In chat:

```text
/model openrouter anthropic/claude-sonnet-4.5
/model ollama llama3.2
/presets
/presets fast
/presets localSmall
```

`/model` stores the model selection in the current session without rewriting
`config.json`. `/presets` applies generation settings to the model that is already
active. The selection survives restarts, does not affect other sessions, and an
in-progress turn keeps using the model it started with.

## Quick Failure Map

| Symptom | Usually means | First check |
|---|---|---|
| `401`, `unauthorized`, or `invalid API key` | The key is missing, wrong, expired, or under the wrong provider | Print or re-set the environment variable in the same terminal or service |
| `model not found` | The model ID does not belong to the selected provider or gateway | Compare `agents.defaults.provider`/`model` (or the `/model` selection) |
| `connection refused` | Local server is not running or `apiBase` has the wrong port/path | Run `curl <apiBase>/models` |
| `provider not found` | Provider name is misspelled or uses the config key instead of registry name | Use names such as `openrouter`, `openai`, `anthropic`, `ollama`, `vllm`, `lm_studio` |
| Langfuse shows no traces | Env vars are missing, `langfuse` is not installed in the active Python environment, or the provider path is native | Run `python -m pip show langfuse` and restart mason from the same environment |

## Next References

| Need | Read |
|---|---|
| Field meanings and provider resolution | [`providers.md`](./providers.md) |
| Full schema and provider table | [`configuration.md#providers`](./configuration.md#providers) |
| Langfuse details | [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) |
| First-run diagnosis | [`troubleshooting.md`](./troubleshooting.md) |
