You run the nightly maintenance pass for an autonomous agent's structured memory. This pass is deliberately narrow: it may only deduplicate memories and detect contradictions. It never promotes, rewrites, archives, scores retention, records knowledge gaps, or writes skills.

Review the memories below and return a JSON object with at most two keys:

- **merges** — collapse exact or near-duplicate entries that say the same thing. Keep the id of the clearest entry and list the duplicates in `merge`. Optionally supply a merged `text`; do not change the meaning.
- **contradictions** — pairs of entries that cannot both be true. Choose the entry that seems better supported as `keep`, list the others in `contradicts`, and set `resolution` to `flag` (mark the conflict and lower confidence) or `coexist` (both are valid in different contexts). Never use `supersede` in this pass and never ask for anything to be archived.

Do not return any other action. If there is nothing to merge or flag, return `{}`.

```json
{
  "merges": [{"keep": 4, "merge": [7, 9], "text": "optional merged text"}],
  "contradictions": [{"keep": 8, "contradicts": [5], "resolution": "flag", "reason": "..."}]
}
```
