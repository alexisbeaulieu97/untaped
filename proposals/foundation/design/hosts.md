# Hosts and the run

> Draft. Package names are placeholders.

## Hosts

A **host** is the program that starts untaped in a process: the CLI executable (`untaped-cli` declares it), an MCP server, a hub, a program embedding untaped, a test ([0041](../decisions/0041-process-slot-and-host.md)). The kernel has no `main`; the host calls into it: load plugins, open a scope, ask, show the results.

A host is a role, not a bucket. It is the only code that reads the real process, and it decides how the kernel's facts (`LoadProblem`, `Undecided`, ...) are shown. The `untaped` command belongs to the CLI host's package.

## Process facts

The environment, the working directory and the home folder are a `process` slot (`ProcessFacts`) that only hosts fill. Nothing else reads the real process: the CLI host's `EnvResolver` reads `ask(process).env`. A hub binds each user's own facts; a test sets them directly ([0041](../decisions/0041-process-slot-and-host.md)).

## The run

There is no Run family and no `Context` object. **A run is a kernel scope a host opens**, binding fillers of other owners' slots ([0038](../decisions/0038-settings-and-no-run-family.md)):

| Slot | Owner | What a host binds |
|---|---|---|
| `settings_store` | settings words | this user's settings |
| messages, progress | messages words | a terminal, a WebSocket to the job's page |
| questions | prompts words | a terminal prompt, an MCP request, a flag answered ahead of time |
| `Capacity`, cancellation | concurrency | how many things at once; Ctrl-C or a hub cancelling the job |
| `process` | not decided | this process's facts, or this user's |

```python
with scope(settings_store=UserSettings(alice), messages=WebSocketSink(job),
           questions=HubForm(job), capacity=Capacity(8)):
    ask(RepoSource).repos()
```

The command grammar may offer shortcuts such as `self.progress(...)`.

## Concurrency

Its own package: words, grammar and an engine ([0037](../decisions/0037-concurrency.md)).

- `Capacity`: "N things at once", shared by the whole scope, so nested calls don't multiply. Budgets never appear in contract signatures; providers draw from the capacity; a provider's hard limits (rate limits) stay facts it reports.
- Cancellation, "stopped": Ctrl-C, a hub cancelling a job. It differs from "declined" (a user answering no to `Confirm`), which is a word of the command grammar.
- Its engine's duty: carry the scope into every worker it starts (`contextvars.copy_context().run`). "No raw threads" is a convention that `NoScope` already reports ([0040](../decisions/0040-scope-bindings-noscope.md)).
