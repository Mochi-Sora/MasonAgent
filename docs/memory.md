# AI Agent Memory in mason

This page explains how mason implements long-term AI agent memory: the working
state, the single SQLite memory tier, and the compressed full backup that
protects it.

Good memory is not a pile of notes. It is a quiet system of attention. It notices
what is worth keeping, lets go of what no longer needs the spotlight, and turns
lived experience into something calm, durable, and useful.

## One Tier, Two Representations

mason keeps memory in one SQLite database, `memory/memory.db`. There is no
short-term tier that gets thrown away: the same store holds the raw record and the
knowledge derived from it.

| Representation | Stored in | Lifetime | Written by | Read with |
|----------------|-----------|----------|------------|-----------|
| Working state | `memory/state.md` (~2 KB) | Cleared at each daily rollover | The model | Injected into the system prompt every turn |
| Structured memory | `memory/memory.db` | Durable | Consolidation | `recall_memory` |
| Verbatim journal | `memory/memory.db` | Durable and unbounded | Every turn, verbatim | `recall_backup` |

Two more surfaces sit alongside them: `memory/DATES.md` holds dated commitments
(searched on demand with `search_dates`, because a date is the whole point and
curation favors timeless facts), and `memory/state_history.md` keeps the last 20
working-state versions so an overwrite can be undone. A compressed, lossless
full copy of the database is written daily to `backups/memory/` for disaster
recovery.

### Working state

`memory/state.md` is a compact scratchpad — about 2 KB by default — that stays in
the model's context every turn. It holds whatever currently matters: active goals,
decisions, user preferences, corrections, and open questions.

The model owns this file. It rewrites it in place with the `update_state` tool as
soon as the picture changes, not just at the end of a turn. The daily rollover
empties it in place, so treat it as short-lived by design.

### Structured memory

Structured memory is what the agent knows: durable facts, preferences, decisions,
events, summaries, the relationships between them, and open knowledge gaps.

The model searches it with `recall_memory`. It never writes to it directly:
everything that lands there is derived by consolidation (see below), which keeps
the store deduplicated, coherent, and free of one-off noise. Passing
`kind="gap"` with an empty query lists the open questions the agent has recorded
about what it does not yet know.

### Verbatim journal

Every turn of every conversation is captured verbatim in `memory/memory.db` as a
day-stamped episode. This is the raw, complete record — nothing is summarized on
the way in, and it is never purged automatically.

The model searches it with `recall_backup`; an empty query lists the latest turns.
It is the place to look for exact wording, details, or events that structured
memory has not kept. Because the journal is unbounded, a question about something
from months ago remains answerable.

Only the newest turns of a long conversation stay in the model's context each turn
when `agents.defaults.sessionReplay` is `tail` (the token budget,
`sessionReplayMaxTokens`, defaults to 32,000 tokens). The default is `none`: no
conversation history at all, so state and memory are the only continuity and
`recall_backup` is how earlier turns come back.

Because the working state carries the continuity, the framework keeps the model
honest about it: after any turn that ends without an `update_state` call, the next
turn carries a short runtime note asking for that decision again. The model can still
decide nothing needs to change; it just has to decide.

## What the Model Is Told

Memory only works if the agent knows it has some. The system prompt carries an
explicit contract, and the memory tools are always available to the model (they are
never hidden behind capability discovery):

- **Check before assuming.** The model should search structured memory with
  `recall_memory` before answering questions about the user's past, earlier
  decisions, or ongoing work.
- **Reach for the journal.** When exact wording or detail matters, it should use
  `recall_backup` rather than guessing.
- **Write the working state in-turn.** When the user asks it to remember something,
  or when a decision or preference appears, it updates `memory/state.md` right away
  with `update_state`. The journal keeps a verbatim copy regardless, and
  consolidation promotes what matters into structured memory.
- **Know its gaps.** Open gaps are stored as structured memories of kind `gap`;
  `recall_memory(kind="gap")` shows what the agent has explicitly recorded as
  unknown, so it can ask instead of inventing an answer.

## Consolidation

Consolidation is the slow, thoughtful layer — the pass that replaced the older
Dream process. It turns the permanent journal into durable knowledge. It runs on a
cron system job (every two hours by default), and its output is written back
through the same store the model reads from. The daily rollover runs a narrower
dedupe/contradiction pass before the backup.

In one pass it:

- **Promotes** journal entries that are worth keeping: durable facts, preferences,
  decisions, project context, and recurring procedures.
- **Deduplicates and merges** memories that say the same thing in different words,
  and rewrites memories whose meaning drifted or was too narrow.
- **Resolves or flags contradictions.** When two memories cannot both be true, it
  picks the authoritative one (`supersede`), marks the conflict while keeping both
  (`flag`), or records that both are valid in different contexts (`coexist`).
- **Synthesises higher-level memories.** Related facts can become one broader,
  more useful statement (for example, "likes potatoes" and "likes badminton" can
  become "likes potatoes and badminton"). Source memories are archived only when
  the synthesis fully covers them.
- **Connects** related memories, so recall can follow a thread instead of returning
  isolated facts.
- **Assesses retention.** Every entry gets a usefulness score and a keep/archive
  decision; used and pinned memories are protected.
- **Records knowledge gaps.** Missing context that blocks the user's goals becomes
  an explicit open question, and later passes mark gaps resolved when the answer
  arrives.
- **Writes tiny skills.** When the day's work revealed a reusable procedure,
  consolidation can write a small generated skill into `skills/<name>/SKILL.md`.
  Generated skills are marked in their frontmatter and are never allowed to
  overwrite or retire skills a user wrote: retiring a generated skill moves it to
  `skills/retired/` instead.

Each pass also drops a readable snapshot of what it decided at
`memory/consolidation/latest.md`.

The consolidation prompt is chosen per call, so it can run with a different (often
cheaper) model than the main conversation. It never raises into the gateway:
a model error, or output that does not parse, simply means nothing changed and the
cursor stays where it was, so the same journal entries are retried next time.

### Nightly maintenance

The daily rollover runs a deliberately narrower pass before it writes the backup.
This pass reviews only structured memory and can do exactly two things:

- **deduplicate** exact or near-duplicate entries, and
- **flag contradictions** (or mark them as coexisting in different contexts).

It cannot promote, rewrite, archive, score retention, record gaps, or write
skills, and contradictions are never auto-superseded: the nightly job lowers
confidence and records the conflict, leaving the final decision to a later full
consolidation pass. It works from the current graph rather than the episode cursor,
so it still does useful work when no new turns arrived.

## Daily Rollover

Once a day — at `00:00` in the configured timezone, and on gateway startup when a
day was missed — mason rolls memory over:

- runs a narrow maintenance pass that only deduplicates structured memories and
  flags contradictions (no promotions, retention scoring, syntheses, gaps, or
  skills),
- writes a compressed, lossless full copy of the database to
  `backups/memory/memory-YYYY-MM-DD.db.gz`,
- empties `memory/state.md` in place,
- runs SQLite's `PRAGMA optimize`, an FTS5 merge pass, and a WAL checkpoint,
- keeps the journal and structured memory exactly as they were.

The backup is lossless and self-describing: it is a complete SQLite copy of the
active database — verbatim journal, structured memories, edges, and indexes — with
metadata recording that it is a full backup. Backups are retained according to
`snapshotKeep` (default 7) and written with owner-only permissions.

## The Files

In this page, `workspace` means the configured **agent workspace** (by default
`~/.mason/workspace/`, or the path passed with `--workspace`). Selecting a
different project for a session changes that session's project context and tool working
directory; it does not relocate the files below.

```text
workspace/
├── SOUL.md                  # The bot's long-term voice and communication style
├── USER.md                  # Stable knowledge about the user
├── skills/                  # User skills and generated skills
├── backups/
│   └── memory/              # Compressed full backups (recovery only)
│       └── memory-YYYY-MM-DD.db.gz
└── memory/
    ├── state.md             # Working state, rewritten by the model
    ├── memory.db            # SQLite: verbatim journal + structured memory + links
    ├── history.jsonl        # Archived conversation summaries (session compaction)
    └── consolidation/
        └── latest.md        # Latest consolidation snapshot, for inspection
```

A selected project may provide its own `AGENTS.md`, but project-local `SOUL.md`,
`USER.md`, and `memory/` do not replace the agent-owned files above. This keeps one
agent's profile and memory continuous while it works across projects. Use a separate
configured agent workspace when identity or memory must be isolated.

`memory/history.jsonl` is not part of the memory tier above. It still exists as the
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
        "snapshotEnabled": true,
        "snapshotKeep": 7,
        "backupDir": null,
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
| `snapshotEnabled` | Write a compressed, lossless full copy of the database at each daily rollover |
| `snapshotKeep` | Number of daily compressed backups retained |
| `backupDir` | Backup directory; unset uses `<workspace>/backups/memory` |
| `consolidation.enabled` | Register the periodic consolidation job |
| `consolidation.intervalH` | How often consolidation runs, in hours |
| `consolidation.cron` | Legacy cron expression override (takes precedence over `intervalH`) |
| `consolidation.modelOverride` | Optional model ID for consolidation runs |

`modelOverride` accepts a model ID. For migration, a value that matches a legacy
preset name resolves to that preset's old `model` binding. If omitted, consolidation
uses the main agent's selected runtime.

## Upgrading From File-Based Memory

Older installs kept durable memory in `memory/MEMORY.md` and compressed history in
`memory/history.jsonl`. On first start with the new system:

- a customized `memory/MEMORY.md` is imported into structured memory, chunked into
  individual memories;
- existing `memory/history.jsonl` entries are imported into the verbatim journal;
- both files are then moved to `memory/legacy/`, and an untouched bundled
  `MEMORY.md` template is skipped instead of imported;
- old Dream cursors and Dream sessions are cleaned up by the gateway.

The import runs once and is idempotent. After it completes, `memory/MEMORY.md` is no
longer a memory surface; durable facts about the user belong in `USER.md`.

Upgrading from the earlier two-tier SQLite store needs no migration: the same
`episodes`, `memories`, and `edges` tables are reused. The difference is lifetime
policy — episodes are no longer purged — and the compression/optimisation pass.
Uncompressed snapshots in `memory/backups/*.db` are moved and gzipped into
`backups/memory/*.db.gz` on the next start, so backup health and retention keep
working after the upgrade.

## Commands

Memory is not hidden behind the curtain. Users can inspect and guide it.

| Command | What it does |
|---------|--------------|
| `/compact` | Summarize the current conversation context while keeping saved chat history |
| `/new` | Start a fresh session |

To inspect memory yourself, read `memory/state.md` and
`memory/consolidation/latest.md`, or query the database directly:

```bash
sqlite3 memory/memory.db "select kind, status, text from memories order by id desc limit 20;"
sqlite3 memory/memory.db "select ts, role, substr(content, 1, 80) from episodes order by id desc limit 20;"
sqlite3 memory/memory.db "select text from memories where kind = 'gap' and status = 'open';"
```

Restoring a backup is a manual operation:

```bash
gunzip -c backups/memory/memory-YYYY-MM-DD.db.gz > /tmp/verbatim.db
sqlite3 /tmp/verbatim.db "select count(*) from episodes;"
```

## Notes and Limits

- **Windows is unsupported** for the memory subsystem. The rest of mason still
  runs there; memory is skipped rather than emulated.
- **Speed.** Retrieval is served by SQLite with FTS5 indexes, so a
  `recall_memory` or `recall_backup` call is a local index lookup, not a scan of a
  growing file. When FTS5 is unavailable in the local SQLite build, the store falls
  back to bounded `LIKE` matching. Consolidation and the daily rollover run
  `PRAGMA optimize`, merge the FTS indexes, and checkpoint the WAL.
- **One database, one workspace.** `memory/memory.db` is per agent workspace and is
  written behind a write lock with WAL journaling, so concurrent gateway tasks do not
  corrupt it. Backups live outside `memory/` and are chmod `0600`.
- **Backups are not a second tier.** They are recovery artifacts. The model reads
  the live journal and structured memory, never the compressed copy.

## In Practice

What this means in daily use is simple:

- conversations can stay fast without carrying infinite context,
- the permanent journal guarantees nothing is lost, while structured memory stays
  deduplicated and coherent,
- the model can always check what it knows — and knows what it does not.

Memory should not feel like a dump. It should feel like continuity.

That is what this design is trying to protect.
