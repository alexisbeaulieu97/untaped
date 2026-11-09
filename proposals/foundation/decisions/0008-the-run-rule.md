# 0008. The run rule

- **Status:** amended · settled before 2026-11-05 (foundation point 7; the maintainer: "Perfect.") · the maintainer
- **Rests on:** [0001](0001-make-your-own.md), the goals (hosted front ends, many runs in one process)
- **Amended by [0038](0038-settings-and-no-run-family.md):** "core offers 'start a run with these settings and this way to talk to the user'" became "a host opens a kernel scope". There is no Run family. Everything per-run is reached through a slot resolved in the scope, never a module global.
- **Amended by [0040](0040-scope-bindings-noscope.md):** a slot bound in the scope resolves to its binding first. `ask` outside any scope is refused with `NoScope`.
- **Reopen if:** process-wide state can't be audited out at acceptable cost, or the hosted and multi-run goals stop being goals. The rejected option (leave state process-wide) would then win.

## Problem

The CLI makes one run and exits. A TUI, GUI or web app makes many runs in one process, possibly for several users. Anything a plugin reads from the process (settings, secrets, the terminal, a cache keyed only by the process) leaks one run's state into another's.

## Decision

**Nothing a plugin reaches during a run comes from the process.** Whoever starts a run decides what's in it.

- **The host creates runs.** The CLI creates one and exits; a TUI, GUI or web app creates many.
- **Front ends need:** several commands at once; progress and messages as events; questions as requests; cancelling one command; settings that change between runs.
- **Local vs hosted is a choice of engines:** auth, per-user config, a per-user secrets vault. Core never learns what a user is. The maintainer wants "potentially both or either", and this rule keeps both possible without choosing now.

```python
# ✗ a plugin reads the process during a run
token = os.environ["GITHUB_TOKEN"]

# ✓ it reaches what the run was given, through a slot resolved in the scope
token = self.settings.token   # asks settings_store in the current scope (shape from 0038)
```

## Consequences

- Audit needed against the rule: providers loaded once per process and profile; the stderr sink; prompts writing straight to a terminal.
- The composed declaration may stay process-wide (it's the same for every user).
- This was the first of several implicit things that had to become slots: settings, capacity and cancellation, process facts ([0038](0038-settings-and-no-run-family.md), [0037](0037-concurrency.md), [0041](0041-process-slot-and-host.md)). That led to the parked "ambient inputs" question.
