# How AI Agent Memory Works in nanobot

This guide explains how to use nanobot's long-term AI agent memory: session
history, compacted conversation summaries, the working state, SQLite long-term
memory, and the verbatim backup that consolidation promotes from.

## What you will build

- a workspace with persistent session history
- a working state the agent keeps current with `update_state`
- curated long-term memory that survives across sessions
- a searchable backup of today's turns

## When to use this

Use memory when an agent should remember stable preferences, project facts,
decisions, and recurring context across sessions. Do not use memory as a dumping
ground for every raw transcript; nanobot separates short-term messages from
curated durable knowledge.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
nanobot agent -m "Hello!"
```

## Minimal working example

Ask the agent to remember a stable fact in a normal session:

```text
Remember that I prefer concise release notes.
```

The agent records it in the working state with `update_state`, and the turn is
captured verbatim in today's backup. A consolidation pass runs every two hours
by default and promotes worthwhile entries from the backup into long-term memory.

In a later session, ask about it:

```text
What do I prefer for release notes?
```

The agent searches long-term memory with `recall_memory` (and can fall back to
`recall_backup` for exact recent wording) before answering. Memory lives in the
active workspace, usually under `~/.nanobot/workspace/`.

## Production notes

- Use one workspace per project or personal context.
- Long-term memory is curated automatically in `memory/memory.db`; set
  `agents.defaults.memory.consolidation.modelOverride` to a cheaper model preset
  if consolidation should not use the main model.
- Back up the workspace's `memory/` directory, including `memory.db` and
  `state.md`.
- The backup tier covers the current day only; keep session history or your own
  exports when you need exact older transcripts.
- The memory subsystem is SQLite-based and does not support Windows.

## Security notes

- Memory may contain sensitive user or project facts.
- Avoid sharing workspaces without reviewing `SOUL.md`, `USER.md`,
  `memory/state.md`, and `memory/memory.db`.
- Use separate workspaces for personal and team contexts.

## Troubleshooting

- If memory feels stale, remember consolidation is periodic; the agent can still
  search today's backup with `recall_backup`.
- If a fact must survive, ask the agent to remember it; consolidation decides
  what is promoted from the backup.
- If a new session lacks context, confirm it uses the same workspace.

## Related nanobot docs

- [AI Agent Memory in nanobot](../memory.md)
- [Concepts](../concepts.md)
- [Configuration](../configuration.md#auto-compact)
- [Chat Commands](../chat-commands.md)
