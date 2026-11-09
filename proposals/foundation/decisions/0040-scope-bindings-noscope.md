# 0040. Scope bindings, `NoScope`, threads

- **Status:** accepted · 2026-11-05 · the maintainer
- **Rests on:** [0038](0038-settings-and-no-run-family.md) settings through a slot; a run is a scope

## Problem

[0038](0038-settings-and-no-run-family.md) made a run a kernel scope in which slots resolve to per-run instances (one user's settings on a hub). Three edges were undefined (review finding 2):

1. a slot bound in the scope **and** chosen by resolvers: which wins?
2. `ask` called outside any scope;
3. work started on another thread: does it see the scope?

## Decision

**1. A binding comes first.** Resolving a slot: bound in the current scope → its binding, before any resolver is asked. Whoever starts a run decides what's in it. A binding may be any object implementing the slot (a test fake needs no entry point). Doctor and `plugin list` show it as "bound by the host".

**2. `ask` outside any scope is refused with `NoScope`**, as data. There is no default scope.

The metaphor used: a scope is the room a run happens in. A call made outside any room that borrowed the last room would hand Alice's token to Bob's job on a hub. Only host, embedded-script and test authors ever meet `NoScope`; opening a scope is one line.

```python
ask(RepoSource).repos()                      # ✗ NoScope: which room?
with scope(settings_store=UserSettings(alice)):
    ask(RepoSource).repos()                  # ✓
```

**3. Threads are carried by the concurrency engine.** Its stated duty: carry the scope into every worker it starts (`contextvars.copy_context().run`, as today's `bounded_map` does at `sdk/_concurrency.py:96`). "Don't start raw threads" is a convention; `NoScope` already reports a violation.

Verified on Python 3.14: threads don't inherit the context (`thread_inherit_context` = 0). Demonstrated: raw pool threads see no scope; carried ones see the same objects, with no copying or serialization, because threads share memory.

## Rejected

- A silent default scope: leaks one user's state into another's run.
- Resolvers before bindings: the host, who starts the run, would not decide what's in it.

## Consequences

- The gaps reviewer's S2 (bootstrap as a host binding) becomes possible. The host's env resolver stays: env picks apply to any slot, not only bootstrap.
- Serialization is needed only across a process boundary (a hub forwarding a call to a runner), where the runner opens its own scope with its own facts.
- Parallel scopes (a hub's jobs side by side) are isolated by `contextvars`. **Nested scopes** (inherit the outer bindings or start empty?) are an open question.
