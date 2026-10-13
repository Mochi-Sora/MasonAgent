---
name: memory
description: Recall durable facts and exact past conversations from memory.
---

# Memory

Your memory lives in two searchable tiers plus the working state. Reach for the
built-in tools; do not grep memory files.

- `update_state(content)` — replace the working state shown in your system prompt. Keep it compact; it is cleared when the day rolls over.
- `recall_memory(query, limit=5)` — durable facts, preferences, and decisions curated across sessions. Check it before answering questions about the user or earlier work.
- `recall_backup(query, session=None, limit=10)` — every turn captured today, verbatim. An empty query lists the latest turns. Use it for exact recent wording or detail that has not been consolidated yet.

The backup is discarded when the day rolls over and long-term memory is curated
automatically from it, so you never need to copy one into the other.
