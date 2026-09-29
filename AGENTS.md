This file provides guidance to AI coding agents working with this repository.

## Project Overview

nanobot is a lightweight, open-source AI agent framework: a Python core with CLI and chat channels. Chat channels publish `InboundMessage` events onto an async `MessageBus`; `AgentLoop` (`nanobot/agent/loop.py`) builds per-turn context and `AgentRunner` (`nanobot/agent/runner.py`) runs the LLM/tool loop, publishing replies as `OutboundMessage` events.

## Development Commands

```bash
pytest tests/test_openai_api.py::test_function -v   # single test
ruff check nanobot/                                 # lint (never run `ruff format`)

# Strict type checking (matches CI)
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright

nanobot gateway             # run the gateway
```

## Key Subsystems

- **Agent** (`nanobot/agent/`): `loop.py` owns the turn lifecycle, session keys, hooks, and context building; `runner.py` executes the multi-turn LLM conversation with tool execution; `context.py` builds the system prompt; `skills.py` loads `SKILL.md` skills.
- **Tools** (`nanobot/agent/tools/`): auto-discovered via `pkgutil` scan plus entry-point plugins. `registry.py` stores them, `loader.py` registers them, and `capability_gate.py` + `capability_search.py` hide capabilities behind the `find_capabilities` tool so only discovered schemas reach the model.
- **Memory** (`nanobot/memory/`): two SQLite tiers in `memory/memory.db` plus a small `memory/state.md`. `store.py` holds episodes (today's verbatim backup) with FTS5 search, curated long-term memories, and edges; `state.py` is the working state; `consolidation.py` promotes backup entries into long-term memory and writes generated skills; `maintenance.py` runs the daily rollover; `migration.py` imports legacy `MEMORY.md`/`history.jsonl`. Windows is unsupported for this subsystem.
- **Providers** (`nanobot/providers/`): Anthropic, OpenAI-compatible/Responses, Azure, Bedrock, GitHub Copilot, OpenAI Codex, etc. built on `base.py`; `factory.py` and `registry.py` instantiate providers and discover models. Image generation and transcription live here.
- **Channels** (`nanobot/channels/`): Telegram, Discord, Slack, Feishu, Matrix, Signal, WhatsApp, QQ, NapCat, WeChat, WeCom, DingTalk, Email, Linear, MoChat, MS Teams, Mattermost. Self-contained packages, auto-discovered; `manager.py` coordinates them.
- **Sessions** (`nanobot/session/`): per-session history, TTL-based auto-compaction (`manager.py`), archived summaries, goal state (`goal_state.py`).
- **Config** (`nanobot/config/schema.py`, `loader.py`): Pydantic models loaded from `~/.nanobot/config.json` with camelCase aliases.
- **Other**: OpenAI-compatible API server (`nanobot/api/server.py`), command router (`nanobot/command/`), pairing (`nanobot/pairing/`), built-in skills (`nanobot/skills/`), security guards (`nanobot/security/`).
- **Entry points**: CLI `nanobot/cli/commands.py`, Python SDK `nanobot/nanobot.py`.

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)
- Contribution flow: [`CONTRIBUTING.md`](./CONTRIBUTING.md)

## Code Style

- Python 3.11+, asyncio throughout; line length 100; `ruff` rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`; tests mirror the `nanobot/` package structure.
- Templates for new code: provider `nanobot/providers/base.py`, channel `nanobot/channels/base.py`.
