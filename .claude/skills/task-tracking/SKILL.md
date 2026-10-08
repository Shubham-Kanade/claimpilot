---
name: task-tracking
description: Update docs/TASKS.md and docs/DECISIONS.md. Use when starting or finishing any task or milestone, when a plan changes, when something is blocked, or when the user asks "where are we" or "what's next".
---
# Task tracking

`docs/TASKS.md` is the single source of truth for progress. CLAUDE.md imports it, so every session sees it.

## Format
- Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked (add the reason after an em dash) · ★ must · ☆ stretch
- Milestones (`## M<n> <name> (<date>) [status]`) are written up front as one summary line of ★/☆ items.
- **Low-level subtasks are added only when a milestone starts.** Expand the summary line into a checklist of concrete tasks, each about 30–90 minutes of work.
- `**Current focus:**` at the top always names the active milestone and task.
- `## Done log` at the bottom: one dated line per meaningful completion (`- YYYY-MM-DD: ...`).

## Procedure
1. **Starting work:** set the task to `[~]` and update `Current focus`. If the milestone has no subtasks yet, expand it now.
2. **Discovering work:** add it as a subtask under the right milestone. Don't keep hidden to-do lists.
3. **Finishing:** set `[x]`. Mark the milestone `[x]` once all its ★ items are done. Append to the done log.
4. **Blocked:** set `[!] — <reason / who must act>`. User actions are prefixed with `**User action:**`.
5. **Scope cut:** move the item under the milestone as `~~text~~ (cut: reason)`. Never silently delete it.
6. **Decisions:** when a choice is made with real alternatives (library, model route, architecture), append an ADR to `docs/DECISIONS.md` in this form: `### ADR-NNN: <title> (date)`, then Decision / Why / Consequences bullets.

Keep TASKS.md under about 120 lines. Collapse finished milestones to a single `[x]` line with their done-log entries.
