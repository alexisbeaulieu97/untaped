# 0041. Process facts and the host role

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0040](0040-scope-bindings-noscope.md) scope bindings, `NoScope`

## Problem

The run rule ([0008](0008-the-run-rule.md)) says nothing a plugin reaches during a run comes from the process. Yet the environment, working directory and home folder are read everywhere, and on a hub each user has their own. The reviews also found "host" used without a definition (finding 2).

## Decision

**Process facts are a slot.** Environment, working directory and home are a `process` slot (`ProcessFacts`). Only hosts fill it; nothing else reads the real process. A hub binds each user's own facts. Tests set the facts directly.

```python
os.environ["UNTAPED_GIT__STORE"]          # ✗ in a plugin: the real process
ask(process).env["UNTAPED_GIT__STORE"]    # ✓ the run's facts (EnvResolver does this)
```

**Host is a role, not a bucket:** whatever opens a scope and fills `process`. It is the only code allowed to read the real process, and it decides how kernel results (`LoadProblem`, `Undecided`, …) are shown. CLI, MCP and hub engines play it; an embedded program or a test plays it directly.

Explained later (2026-11-06): the host is the program that starts untaped in a process. The kernel has no `main`; the host calls into it: load, open a scope, ask, show results.

## Consequences

- `EnvResolver` reads `ask(process).env`, not `os.environ`.
- The question "role or bucket?" was raised here and settled in [0042](0042-role-vs-bucket.md).

## Open (parked)

**Scope before plugins load** (the maintainer): the host must open a scope and bind `process` and settings before plugins import (plugin import-time code would otherwise meet `NoScope`), yet those slots are offered by plugins.

**Ambient inputs** (the maintainer): this was the third or fourth time something implicit had to become a slot (settings, capacity and cancellation, the run, process facts). What else is read without being handed or asked for (clock, randomness, terminal, `sys.argv`, signals, locale, user identity, temp folders, host name, logging, today's `ContextVar`s)? To be found by a mechanical search during the code test.
