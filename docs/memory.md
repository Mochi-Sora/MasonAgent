# AI Agent Memory in nanobot

This page explains how nanobot implements long-term AI agent memory: the working
state, the curated long-term memory graph, and today's verbatim backup.

Good memory is not a pile of notes. It is a quiet system of attention. It notices
what is worth keeping, lets go of what no longer needs the spotlight, and turns
lived experience into something calm, durable, and useful.

## The Three Surfaces

nanobot does not treat memory as one giant file. It separates remembering into
three surfaces with different lifetimes, because different kinds of remembering
deserve different tools:

| Surface | Stored in | Lifetime | Written by | Read with |
|---------|-----------|----------|------------|-----------|
| Working state | `memory/state.md` (~2 KB) | Cleared at each daily rollover | The model | Injected into the system prompt every turn |
| Long-term memory | `memory/memory.db` | Durable | Consolidation | `recall_memory` |
| Today's backup | `memory/memory.db` | Discarded at the daily rollover | Every turn, verbatim | `recall_backup` |

### Working state

`memory/state.md` is a compact scratchpad — about 2 KB by default — that stays in
the model's context every turn. It holds whatever currently matters: active goals,
decisions, user preferences, corrections, and open questions.

The model owns this file. It rewrites it in place with the `update_state` tool as
soon as the picture changes, not just at the end of a turn. The daily rollover
empties it in place, so treat it as short-lived by design.

### Long-term memory

Long-term memory is a curated set of memories in `memory/memory.db`, with links
between related memories. It holds what should survive the day: durable facts,
preferences, decisions, corrections, and the connections between them.

The model searches it with `recall_memory`. It never writes to it directly:
everything that lands there is promoted by consolidation (see below), which keeps
the tier small, deduplicated, and coherent instead of an append-only pile.

### Today's backup

Every turn of every conversation is captured verbatim in `memory/memory.db` as a
day-stamped episode. This is the raw, complete record — nothing is summarized on
the way in.

The model searches it with `recall_backup`; an empty query lists the latest turns.
It is the place to look for exact recent wording, details, or events that long-term
memory has not kept. It is discarded at the end of the day, after consolidation has
had a chance to promote what matters.

## What the Model Is Told

Memory only works if the agent knows it has some. The system prompt carries an
explicit contract, and the memory tools are always available to the model (they are
never hidden behind capability discovery):

- **Check before assuming.** The model should search long-term memory with
  `recall_memory` before answering questions about the user's past, earlier
  decisions, or ongoing work.
- **Reach for the backup.** When exact recent wording or detail matters, it should
  use `recall_backup` rather than guessing.
- **Write the working state in-turn.** When the user asks it to remember something,
  or when a decision or preference appears, it updates `memory/state.md` right away
  with `update_state`. The backup keeps a verbatim copy regardless, and consolidation
  promotes what matters into long-term memory.

## Consolidation

Consolidation is the slow, thoughtful layer. It replaced the older Dream process,
and it is what turns episodes into lasting memory. It runs on a cron system job
(every two hours by default), immediately before each daily rollover, and its output
is written back through the same store the model reads from.

In one pass it:

- **Promotes** the entries from the backup that are worth keeping as long-term
  memory.
- **Deduplicates and merges** memories that say the same thing in different words,
  and rewrites memories whose meaning drifted or was too narrow.
- **Connects** related memories, so recall can follow a thread instead of returning
  isolated facts.
- **Writes tiny skills.** When the day's work revealed a reusable procedure,
  consolidation can write a small generated skill into `skills/<name>/SKILL.md`.
  Generated skills are marked in their frontmatter and are never allowed to
  overwrite or retire skills a user wrote: retiring a generated skill moves it to
  `skills/retired/` instead.

Each pass also drops a readable snapshot of what it decided at
`memory/consolidation/latest.md`.

The consolidation prompt is chosen per call, so it can run with a different (often
cheaper) model preset than the main conversation. It never raises into the gateway:
a model error, or output that does not parse, simply means nothing changed and the
cursor stays where it was, so the same episodes are retried next time.

## Daily Rollover

Once a day — at `00:00` in the configured timezone, and on gateway startup when a
day was missed — nanobot rolls memory over:

- yesterday's backup episodes are discarded (those already promoted by
  consolidation; unprocessed ones survive until they have had their chance),
- `memory/state.md` is emptied in place,
- the file itself stays, so the layout never changes under the user.

## The Files

In this page, `workspace` means the configured **agent workspace** (by default
`~/.nanobot/workspace/`, or the path passed with `--workspace`). Selecting a
different project for a session changes that session's project context and tool working
directory; it does not relocate the files below.

```text
workspace/
├── SOUL.md                  # The bot's long-term voice and communication style
├── USER.md                  # Stable knowledge about the user
├── skills/                  # User skills and generated skills
└── memory/
    ├── state.md             # Working state, rewritten by the model
    ├── memory.db            # SQLite: episodes (backup) + curated memories + links
    ├── history.jsonl        # Archived conversation summaries (session compaction)
    └── consolidation/
        └── latest.md        # Latest consolidation snapshot, for inspection
```

A selected project may provide its own `AGENTS.md`, but project-local `SOUL.md`,
`USER.md`, and `memory/` do not replace the agent-owned files above. This keeps one
agent's profile and memory continuous while it works across projects. Use a separate
configured agent workspace when identity or memory must be isolated.

`memory/history.jsonl` is not part of the memory tiers above. It still exists as the
append-only log of compressed conversation summaries written when session context is
compacted, and it is what the compaction pipeline uses to reconstruct archived
turns. Archived summaries are never re-injected into later prompts: context
compaction only removes old turns from replay, and anything older is recalled on
demand.

## Configuration

```json
{
  "agents": {
    "defaults": {
      "memory": {
        "enabled": true,
        "stateMaxChars": 2048,
        "consolidation": {
          "enabled": true,
          "intervalH": 2,
          "modelOverride": null
        }
      }
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `enabled` | Capture turns and expose the memory tools. When false, no database or state is used |
| `stateMaxChars` | Size cap for the working state (256–16384, default 2048) |
| `consolidation.enabled` | Register the periodic consolidation job |
| `consolidation.intervalH` | How often consolidation runs, in hours |
| `consolidation.cron` | Legacy cron expression override (takes precedence over `intervalH`) |
| `consolidation.modelOverride` | Optional model preset name for consolidation runs |

`modelOverride` selects a named entry from `model_presets`. It accepts preset names
only; raw model identifiers are not supported. If omitted, consolidation uses the
main agent's selected runtime.

## Upgrading From File-Based Memory

Older installs kept durable memory in `memory/MEMORY.md` and compressed history in
`memory/history.jsonl`. On first start with the new system:

- a customized `memory/MEMORY.md` is imported into long-term memory, chunked into
  individual memories;
- existing `memory/history.jsonl` entries are imported into today's backup;
- both files are then moved to `memory/legacy/`, and an untouched bundled
  `MEMORY.md` template is skipped instead of imported;
- old Dream cursors and Dream sessions are cleaned up by the gateway.

The import runs once and is idempotent. After it completes, `memory/MEMORY.md` is no
longer a memory surface; durable facts about the user belong in `USER.md`.

## Commands

Memory is not hidden behind the curtain. Users can inspect and guide it.

| Command | What it does |
|---------|--------------|
| `/compact` | Summarize the current conversation context while keeping saved chat history |
| `/new` | Start a fresh session |

To inspect memory yourself, read `memory/state.md` and
`memory/consolidation/latest.md`, or query the database directly:

```bash
sqlite3 memory/memory.db "select kind, text from memories order by id desc limit 20;"
sqlite3 memory/memory.db "select ts, role, substr(content, 1, 80) from episodes order by id desc limit 20;"
```

## Notes and Limits

- **Windows is unsupported** for the memory subsystem. The rest of nanobot still
  runs there; memory is skipped rather than emulated.
- **Speed.** Retrieval is served by SQLite with FTS5 indexes over both tiers, so a
  `recall_memory` or `recall_backup` call is a local index lookup, not a scan of a
  growing file. When FTS5 is unavailable in the local SQLite build, the store falls
  back to bounded `LIKE` matching.
- **One database, one workspace.** `memory/memory.db` is per agent workspace and is
  written behind a write lock with WAL journaling, so concurrent gateway tasks do not
  corrupt it.

## In Practice

What this means in daily use is simple:

- conversations can stay fast without carrying infinite context,
- the backup guarantees nothing recent is lost, while long-term memory stays small
  and deduplicated,
- the model can always check what it knows — and knows that it should.

Memory should not feel like a dump. It should feel like continuity.

That is what this design is trying to protect.
