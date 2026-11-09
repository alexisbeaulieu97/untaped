# 0049. Kernel hooks; `Loaded` replaces `OwnerCheck`

- **Status:** accepted · 2026-11-06 · the maintainer
- **Rests on:** [0027](0027-kernel-extension-points.md) the kernel's own extension points; [0047](0047-architecture-vs-design.md) architecture vs design
- **Amends:** [0027](0027-kernel-extension-points.md) (`OwnerCheck` is gone; the kernel's own points are hooks of two kinds); [0029](0029-resolvers.md) (`Resolver` is a question hook); [0024](0024-grammar-bucket.md) and [0038](0038-settings-and-no-run-family.md) (owners check and gather on `Loaded`).

## Problem

`OwnerCheck` let a plugin check how others use its words at load, and the kernel refused what it rejected. Review S2: anyone could fill it and veto anyone's words. Checking it against the entry test raised a general question: when may the kernel offer a hook at all, and what does a hook cost?

## Decision

The maintainer: the kernel may be aware of its own limitations and offer hooks for them, so it stays without opinions. A hook's cost is the kernel's opinion on its shape: that shape is the one opinion nobody can swap, changing it breaks every filler, and it can constrain other kernel choices ("after all plugins loaded" would forbid lazy loading).

**A hook earns its place only when all three hold:**

1. it sits at a point in the kernel's own job (finding and routing) where the kernel would otherwise need an opinion, or it holds knowledge only the kernel has;
2. the kernel still works with it empty;
3. no plugin could reach the same result by ordinary means.

A hook exposes a fact or a moment, never a policy.

**Two kinds** (the maintainer named the direction "events"):

- **Moments:** the kernel announces something that happened in its job. Listeners only observe; all are called, in an order that means nothing; what one raises is reported as data. A "pre" hook that can stop or change what follows is a question in disguise (and ordering such hooks would need resolvers to order themselves).
- **Questions:** the kernel needs an answer it can't give without an opinion (`Resolver`: which filler; `Dispatch`: how a call is carried). Each states how several answers combine and what happens when none comes.

`scope` is neither: the host passes it in.

**`OwnerCheck` → `Loaded`**, a moment: "this plugin finished loading", with its declarations. Owners check how others use their words there, or in their base class's `__init_subclass__`.

Why the moment is needed (tested on Python 3.14.7): with PEP 649 deferred annotations, `typing.get_type_hints` inside `__init_subclass__` raises `NameError` for a word defined later in the same file. Only the kernel knows when a plugin has finished loading.

```python
class List(Command):
    output: Output[Sprint]     # Sprint is defined below
class Sprint(Record): ...
# a check in Command.__init_subclass__ → NameError: Sprint
# a check on Loaded(jira)             → sees both ✓
```

## Rejected

- `OwnerCheck` restricted to "who may check which word" (S2's suggested fix): still a veto; a moment that changes the outcome is a question in disguise.
- **A veto question, "admit this declaration?"** (checked 2026-11-07 during concern 1): passes conditions 1 and 2, fails 3. An owner reaches the same result by ordinary means: it refuses at load by raising in `__init_subclass__` (the whole plugin fails, `LoadProblem`), or keeps its own verdict from `Loaded`, and a call to a filler it dislikes still ends in a known outcome (`Err`). See [0054](0054-recording-is-atomic.md).

## Consequences

Dissolved: anyone vetoing others' words; the quarantined checker (a quarantined owner's words don't import, so plugins using them fail to load too); the composition phase. `Resolver`, `Dispatch` and `scope` each kept with a stated reason.
