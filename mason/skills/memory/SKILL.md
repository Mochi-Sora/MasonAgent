---
name: memory
description: Recall durable facts and exact past conversations from memory.
---

# Memory

Your memory lives in one SQLite store with two searchable representations, plus
the session state. Use the built-in tools; do not grep memory files.

- `update_state(current_goal, done, next_steps, session_summary, target_session=None)` — edit the framework-maintained state for this session, or another session by key. The framework refreshes it after every turn, so use this only for corrections or explicit user changes. Load it first with `find_capabilities(need="edit session state")`.
- `read_state(target_session=None)` — read another session's state when the user asks about it. Your own session's state is already in your prompt.
- `recall_memory(query, limit=5)` — durable facts, preferences, and decisions curated across sessions. Check it before answering questions about the user or earlier work.
- `recall_journal(query, session=None, limit=10)` — every captured turn, kept indefinitely, verbatim. An empty query lists the latest turns. Use it for exact wording or detail that has not been consolidated yet.

The session state is cleared only by `/state clear`, never by daily maintenance.
The journal is permanent: nothing is discarded at rollover. Consolidation
promotes worthwhile entries into structured memory, so you never copy between
them manually. A compressed full database backup is written daily for disaster
recovery only; it is not a searchable memory surface.
