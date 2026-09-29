# Prompt Overrides

This folder holds plain-language prompt overrides for this workspace.

## Heartbeat evaluator

`evaluator.md` overrides the system prompt for the heartbeat notification gate — the model that decides whether a heartbeat result is worth delivering. This is an advanced override; you rarely need it. Before editing, read the evaluator code and the default `evaluator.md`.

To create an editable copy, run:

```text
/evaluator-prompt init
```

That creates `prompts/evaluator.md`. It must still instruct the model to call the `evaluate_notification` tool; otherwise the gate fails closed and stays silent. Delete or empty the file to return to the built-in prompt.
