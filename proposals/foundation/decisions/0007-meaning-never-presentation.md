# 0007. Words describe meaning, never presentation

- **Status:** amended · settled before 2026-11-05 (foundation point 6; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users
- **Amended by [0036](0036-engine-hints.md):** "CLI-shaped marks become hints" no longer means hints inside untaped's words. An engine's hints (`-x`, positional, default columns) are that engine owner's **own words package**, attached through the notation's `Annotated` and ignored by engines that don't know them.
- **Reopen if:** some front end can't work from neutral words. The rejected option (front-end-specific words) would then win.

## Problem

Today's words carry CLI shapes: flags, columns, exit codes. A second front end (MCP, TUI, a hub) would have to read CLI meaning, or the author would have to declare everything once per front end.

## Decision

Words say **what** something means. Each front end decides **how** to present it.

| Word | Meaning | CLI engine | MCP engine |
|---|---|---|---|
| a field `name: str` | a required input | `--name` | a tool parameter |
| `Output[Repo]` | a list of repos | table/json/pipe | structured JSON |
| error category `not_found` | failed: not found | exit 1 + stderr | a tool error |
| `Confirm` | needs consent | `--yes` or a prompt | a confirm parameter or the client's approval |
| docstring | what it does | `--help` | the tool description |

## Consequences

- CLI-shaped marks become hints (refined by [0036](0036-engine-hints.md)).
- Exit codes are mapped from categories by the CLI engine.
- Interactive things use the unattended path ([0009](0009-unattended.md)).
- Mounting, rendering and config reading move out of `runtime/`.
- "What a setting is" (a word) and "where its value comes from" (the config engine) are independent.
- Config sources probably *extend*: file, env, a policy server, layered.
