# How AI Agent Memory Works in mason

This guide explains how to use mason's long-term AI agent memory: session
history, compacted conversation summaries, the framework-maintained session state,
the single-tier SQLite memory, and the verbatim journal that consolidation
promotes from.

## What you will build

- a workspace with persistent session history
- a session state the framework keeps current after every turn
- structured memory that survives across sessions
- a searchable, permanent verbatim journal of every turn
- a compressed daily backup for recovery

## When to use this

Use memory when an agent should remember stable preferences, project facts,
decisions, and recurring context across sessions. Do not use memory as a dumping
ground for every raw transcript; mason separates short-term messages from
curated durable knowledge.

## Install

```bash
./scripts/install.sh  # from a checkout; runs the setup wizard
mason agent -m "Hello!"
```

## Minimal working example

Ask the agent to remember a stable fact in a normal session:

```text
Remember that I prefer concise release notes.
```

The framework records the turn in the session state and in the permanent
journal after every turn. A consolidation pass runs every two hours by default
and derives structured memories from the journal. The model can still correct the
state through the capability-gated `update_state` tool when a fact must be exact.

In a later session, ask about it:

```text
What do I prefer for release notes?
```

The agent searches structured memory with `recall_memory` (and can fall back to
`recall_journal` for exact wording from the unbounded journal) before answering.
Memory lives in the active workspace, usually under `~/.mason/workspace/`.

## Production notes

- Use one workspace per project or personal context.
- Structured memory is curated automatically in `memory/memory.db`; set
  `agents.defaults.memory.consolidation.modelOverride` to a cheaper model ID
  if consolidation should not use the main model.
- Back up the workspace's `memory/` directory and the compressed backups in
  `backups/memory/`.
- Session state is stored per session under `memory/state/` and is cleared only by
  an explicit `/state clear`.
- The verbatim journal is unbounded: old turns stay searchable with
  `recall_journal`. Daily `.db.gz` backups are for disaster recovery and are
  retained according to `memory.snapshotKeep` (default 7).
- The memory subsystem is SQLite-based and does not support Windows.

## Security notes

- Memory may contain sensitive user or project facts.
- Avoid sharing workspaces without reviewing `SOUL.md`, `USER.md`,
  `memory/state/`, and `memory/memory.db`.
- Use separate workspaces for personal and team contexts.

## Troubleshooting

- If memory feels stale, remember consolidation is periodic; the agent can still
  search the full journal with `recall_journal`.
- If a fact must survive, ask the agent to remember it; consolidation decides
  what becomes structured memory from the journal.
- If a new session lacks context, confirm it uses the same workspace.

## Related mason docs

- [AI Agent Memory in mason](../memory.md)
- [Concepts](../concepts.md)
- [Configuration](../configuration.md#auto-compact)
- [Chat Commands](../chat-commands.md)
