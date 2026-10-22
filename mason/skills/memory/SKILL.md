---
name: memory
description: Recall durable facts and exact past conversations from memory.
---

# Memory

Your memory lives in one SQLite store with two searchable representations, plus
the working state. Use the built-in tools; do not grep memory files.

- `update_state(content)` — replace the working state shown in your system prompt. Keep it compact; it is cleared when the day rolls over.
- `recall_memory(query, limit=5)` — durable facts, preferences, and decisions curated across sessions. Check it before answering questions about the user or earlier work.
- `recall_journal(query, session=None, limit=10)` — every captured turn, kept indefinitely, verbatim. An empty query lists the latest turns. Use it for exact wording or detail that has not been consolidated yet.

The journal is permanent: nothing is discarded at rollover. Consolidation
promotes worthwhile entries into structured memory, so you never copy between
them manually. A compressed full database backup is written daily for disaster
recovery only; it is not a searchable memory surface.
