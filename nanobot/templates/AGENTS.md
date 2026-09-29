# Agent Instructions

## Workspace Guidance

Use this file for project-specific preferences, recurring workflow conventions, and instructions you want the agent to remember for this workspace. Keep durable facts about the user in `USER.md` and personality/style guidance in `SOUL.md`. The agent manages its own memory (`memory/state.md` working state, `memory/memory.db` long-term memory); do not edit those files by hand.

## Scheduled Reminders

- Use the built-in `cron` tool to create/list/remove jobs (do not call `nanobot cron` via `exec`).
- Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).
- Cron jobs run as scheduled turns in the origin chat/session and normally deliver the result back to that channel. Do not use cron for background checks that should stay silent when there is nothing useful to report; use `HEARTBEAT.md` instead.

**Do NOT just ask the agent to remember a reminder** — schedule it with `cron`, or it will never fire.

## Heartbeat Tasks

`HEARTBEAT.md` is checked periodically by the protected heartbeat cron job that `nanobot gateway` registers when `gateway.heartbeat.enabled` is true. Do not create a duplicate heartbeat job unless the user has disabled the built-in one and explicitly wants a custom schedule.

When the user asks for a recurring/periodic heartbeat task, or for a periodic background check that should only notify on actionable changes, update `HEARTBEAT.md` instead of creating a one-time reminder. Use the built-in `cron` tool for explicit reminders, scheduled tasks that should report every run, or custom schedules that should not be part of the heartbeat task list.
